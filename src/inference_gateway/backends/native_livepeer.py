"""Opaque native requests through Builder Core's paid streaming API."""

from collections.abc import AsyncGenerator, Callable, Sequence
from uuid import UUID

from livepeer_builder import (
    ActorApi,
    ActorContext,
    EngineError,
    JobFailed,
    JobRequest,
    NoCapacity,
    OperationConflict,
    OperationExists,
    Runner,
)
from livepeer_builder.api import JobStreamApi

from inference_gateway.backends.discovery import origin
from inference_gateway.backends.native_selection import Admission, NativeSelection
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.core.backend import (
    BackendConflict,
    BackendDeadline,
    BackendError,
    BackendUnavailable,
)
from inference_gateway.core.models import Finished
from inference_gateway.core.native import NativeRequest


class _PreserveOrder:
    def order(self, candidates: Sequence[Runner], request: JobRequest) -> Sequence[Runner]:
        return candidates


class LivepeerNativeBackend:
    def __init__(
        self,
        engine: ActorApi,
        settings: LivepeerSettings,
        approvals: Callable[[], tuple[Admission, ...]],
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.routes = tuple(r for r in settings.routes if r.mode == "native")
        self.selection = NativeSelection(_PreserveOrder(), approvals)
        self.actor = ActorContext(
            actor_id=settings.actor_id,
            application_id=settings.application_id,
            permissions=frozenset({"discover", "jobs.run", "jobs.read", "costs.read"}),
        )

    def accepts(self, model: str) -> bool:
        return any(r.name == model for r in self.routes)

    def eligible(self, route: ModelRoute, path: str) -> bool:
        health = self.engine.health()
        if not health.ready or not health.discovery_fresh:
            return False
        candidates = []
        approved = {origin(url) for url in self.settings.approved_orchestrators}
        for candidate in self.engine.discovery.candidates(route.app):
            try:
                if approved and origin(candidate.orchestrator_url or "") not in approved:
                    continue
            except ValueError:
                continue
            candidates.append(candidate)
        try:
            self.selection.order(
                candidates,
                JobRequest(app=route.app, model=route.name, path=path),
            )
            return True
        except NoCapacity:
            return False

    def models(self) -> tuple[str, ...]:
        from inference_gateway.adapters.runner_contract import PROFILES

        return tuple(
            sorted(
                {
                    r.name
                    for r in self.routes
                    if any(self.eligible(r, p["path"]) for p in PROFILES.values())
                }
            )
        )

    async def report_usage(self, job_id: str, usage: Finished) -> None:
        await self.engine.jobs.report_usage(
            self.actor,
            UUID(job_id),
            {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
                "cached_prompt_tokens": usage.cached_input_tokens,
            },
        )

    async def open(self, request: NativeRequest) -> "LivepeerNativeStream":
        route = next(
            (r for r in self.routes if r.name == request.model and self.eligible(r, request.path)),
            None,
        )
        streaming = self.engine.jobs.streaming
        if route is None or streaming is None:
            raise BackendUnavailable("No admitted native model/endpoint")
        headers = dict(request.headers)
        content_type = headers.pop("content-type", "application/json")
        job = JobRequest(
            app=route.app,
            path=request.path,
            model=request.model,
            capability="native.http.v1",
            raw_body=request.body,
            content_type=content_type,
            headers=tuple(headers.items()),
            return_http_errors=True,
            operation_ref=request.request_id,
            max_attempts=self.settings.max_attempts,
            timeout_s=self.settings.timeout_seconds,
            first_byte_timeout_s=min(
                self.settings.first_byte_timeout_seconds, self.settings.timeout_seconds
            ),
            idle_timeout_s=self.settings.idle_timeout_seconds,
        )
        try:
            stream = await streaming.stream(self.actor, job)
        except (OperationConflict, OperationExists):
            raise BackendConflict("Generation operation already dispatched") from None
        except JobFailed as exc:
            if exc.job.failure and exc.job.failure.kind == "timeout":
                raise BackendDeadline("Native job deadline exceeded") from None
            if exc.job.state == "uncertain":  # may have run and been charged: not "could not start"
                raise BackendError("Native job outcome unknown") from None
            raise BackendUnavailable("Native job could not start") from None
        except EngineError:
            raise BackendUnavailable("Native job could not start") from None
        return LivepeerNativeStream(stream)


class LivepeerNativeStream:
    def __init__(self, stream: JobStreamApi) -> None:
        self.stream = stream

    @property
    def status_code(self) -> int:
        return self.stream.status_code

    @property
    def headers(self) -> tuple[tuple[str, str], ...]:
        headers = self.stream.headers
        return (
            headers
            if any(k.lower() == "content-type" for k, _ in headers)
            else (*headers, ("content-type", self.stream.content_type))
        )

    @property
    def job_id(self) -> str:
        return str(self.stream.job.id)

    def __aiter__(self) -> AsyncGenerator[bytes, None]:
        return self.chunks()

    async def chunks(self) -> AsyncGenerator[bytes, None]:
        iterator = self.stream.__aiter__()
        try:
            async for chunk in iterator:
                yield chunk
        except EngineError:
            raise BackendError("Native backend stream failed") from None
        finally:
            await iterator.aclose()

    async def complete(self) -> None:
        await self.stream.complete()

    async def disconnected(self) -> None:
        await self.stream.disconnected()

    async def aclose(self) -> None:
        await self.stream.aclose()
