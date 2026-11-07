"""Opaque native requests through Builder Core's paid streaming API."""

import json
from collections.abc import AsyncGenerator
from uuid import UUID

from livepeer_builder import (
    ActorApi,
    ActorContext,
    EngineError,
    JobFailed,
    JobRequest,
    OperationConflict,
    OperationExists,
)
from livepeer_builder.api import JobStreamApi
from livepeer_runner_contract import ResultReader

from inference_gateway.backends.approvals import Approvals
from inference_gateway.backends.discovery import NATIVE, origin
from inference_gateway.backends.runner_contract import ENDPOINTS, RunnerContract
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.core.backend import (
    BackendConflict,
    BackendDeadline,
    BackendError,
    BackendUnavailable,
    QuotaExhausted,
)
from inference_gateway.core.models import Finished
from inference_gateway.core.native import NativeRequest


class LivepeerNativeBackend:
    def __init__(
        self,
        engine: ActorApi,
        settings: LivepeerSettings,
        contract: RunnerContract,
        approvals: Approvals,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.routes = tuple(r for r in settings.routes if r.mode == "native")
        self.contract = contract
        self.approvals = approvals
        self.actor = ActorContext(
            actor_id=settings.actor_id,
            application_id=settings.application_id,
            permissions=frozenset({"discover", "jobs.run", "jobs.read", "costs.read"}),
        )

    def accepts(self, model: str) -> bool:
        return any(r.name == model for r in self.routes)

    def eligible(self, route: ModelRoute, endpoint: str) -> bool:
        """Whether an approved runner with free capacity serves the route on this endpoint now."""
        health = self.engine.health()
        if not health.ready or not health.discovery_fresh:
            return False
        if not self.contract.serves(route.capability, endpoint):
            return False
        candidates = []
        approved = {origin(url) for url in self.settings.approved_orchestrators}
        for candidate in self.engine.discovery.candidates(route.capability):
            try:
                if approved and origin(candidate.orchestrator_url or "") not in approved:
                    continue
            except ValueError:
                continue
            candidates.append(candidate)
        return bool(
            self.contract.eligible(
                candidates,
                capability=route.capability,
                model=route.name,
                endpoint=endpoint,
                approvals=self.approvals.approver(route.name),
                approved_only=True,
            )
        )

    def supports(self, model: str, endpoint: str) -> bool:
        profile = ENDPOINTS[endpoint]
        if not any(
            r.name == model and self.contract.serves(r.capability, endpoint) for r in self.routes
        ):
            return False
        return any(a.model == model and a.profile == profile for a in self.approvals.current())

    def problems(self, request: NativeRequest) -> tuple[str, ...]:
        try:
            body = json.loads(request.body)
        except ValueError:
            return ("the body isn't JSON",)
        return self.contract.problems(request.endpoint, body)

    def models_for(self, endpoints: tuple[str, ...]) -> tuple[str, ...]:
        def serves(name: str, endpoint: str) -> bool:
            return any(self.eligible(r, endpoint) for r in self.routes if r.name == name)

        names = {r.name for r in self.routes}
        return tuple(sorted(n for n in names if all(serves(n, e) for e in endpoints)))

    def models(self) -> tuple[str, ...]:
        return tuple(
            sorted({r.name for r in self.routes if any(self.eligible(r, e) for e in ENDPOINTS)})
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
            (
                r
                for r in self.routes
                if r.name == request.model and self.eligible(r, request.endpoint)
            ),
            None,
        )
        streaming = self.engine.jobs.streaming
        if route is None or streaming is None:
            raise BackendUnavailable("No admitted native model/endpoint")
        headers = dict(request.headers)
        content_type = headers.pop("content-type", "application/json")
        job = JobRequest(
            app=route.capability,
            path=self.contract.path(request.endpoint),
            model=request.model,
            capability=NATIVE,
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
            if exc.job.failure and exc.job.failure.reason == "allowance_exhausted":  # Core D58
                raise QuotaExhausted("Native job allowance exhausted") from None
            raise BackendUnavailable("Native job could not start") from None
        except EngineError:
            raise BackendUnavailable("Native job could not start") from None
        reader = self.contract.reader(
            request.endpoint, status=stream.status_code, content_type=stream.content_type
        )
        return LivepeerNativeStream(stream, reader)


class LivepeerNativeStream:
    def __init__(self, stream: JobStreamApi, reader: ResultReader) -> None:
        self.stream = stream
        self.reader = reader

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
                self.reader.feed(chunk)
                yield chunk
        except EngineError:
            raise BackendError("Native backend stream failed") from None
        finally:
            await iterator.aclose()

    def succeeded(self) -> bool:
        return self.reader.finish().success

    async def complete(self) -> None:
        await self.stream.complete()

    async def disconnected(self) -> None:
        await self.stream.disconnected()

    async def aclose(self) -> None:
        await self.stream.aclose()
