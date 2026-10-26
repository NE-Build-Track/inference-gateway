"""Livepeer inference through the public, borrowed Builder Core actor API."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable
from uuid import uuid4

import anyio
from livepeer_builder import (
    ActorApi,
    ActorContext,
    EngineError,
    JobFailed,
    JobRequest,
    NoCapacity,
    NotProvisioned,
    OperationConflict,
    OperationExists,
    ProviderUnavailable,
)

from endpoint_gateway.backends.livepeer_accounting import LivepeerAccounting
from endpoint_gateway.backends.livepeer_request import runner_payload
from endpoint_gateway.backends.openai_events import CompletionDecoder
from endpoint_gateway.backends.sse import SSEDecoder
from endpoint_gateway.config import LivepeerSettings
from endpoint_gateway.core.backend import (
    BackendConflict,
    BackendDeadline,
    BackendError,
    BackendUnavailable,
    UnknownModel,
)
from endpoint_gateway.core.models import Event, Finished, Generate


class LivepeerBackend:
    def __init__(
        self,
        engine: ActorApi,
        settings: LivepeerSettings,
        usage_recorder: Callable[[str, Finished], None] | None = None,
        job_recorder: Callable[[str, str], None] | None = None,
    ) -> None:
        self._usage_recorder = usage_recorder
        self._job_recorder = job_recorder
        streaming = engine.jobs.streaming
        if streaming is None:
            raise ValueError("Builder Core must provide streaming capability")
        self._engine = engine
        self._streaming = streaming
        self._settings = settings
        self._routes = {route.name: route for route in settings.routes}
        self._actor = ActorContext(
            actor_id=settings.actor_id,
            application_id=settings.application_id,
            permissions=frozenset({"discover", "jobs.run", "jobs.read", "costs.read"}),
        )
        self.accounting = LivepeerAccounting(engine, self._actor)
        self._logger = logging.getLogger("endpoint_gateway.livepeer")

    def ready(self) -> bool:
        health = self._engine.health()
        return (
            health.ready
            and health.discovery_fresh
            and all(self._engine.discovery.candidates(route.app) for route in self._routes.values())
        )

    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        route = self._routes.get(request.model)
        if route is None:
            raise UnknownModel("Unknown configured model")
        operation_ref = request.request_id or "req_" + uuid4().hex
        job_request = JobRequest(
            app=route.app,
            path="/v1/chat/completions",
            method="POST",
            model=request.model,
            payload=runner_payload(request, route),
            operation_ref=operation_ref,
            max_attempts=self._settings.max_attempts,
            timeout_s=self._settings.timeout_seconds,
            first_byte_timeout_s=min(
                self._settings.first_byte_timeout_seconds, self._settings.timeout_seconds
            ),
            idle_timeout_s=self._settings.idle_timeout_seconds,
        )
        try:
            stream = await self._streaming.stream(self._actor, job_request)
            self._logger.info(
                json.dumps(
                    {
                        "event": "livepeer_job_started",
                        "request_id": operation_ref,
                        "job_id": str(stream.job.id),
                    }
                )
            )
            iterator = stream.__aiter__()
            try:
                if self._job_recorder:
                    self._job_recorder(operation_ref, str(stream.job.id))
                if stream.status_code != 200 or not stream.content_type.startswith(
                    "text/event-stream"
                ):
                    raise BackendError("Runner did not return an SSE completion")
                sse = SSEDecoder(self._settings.max_event_bytes)
                decoder = CompletionDecoder(self._settings.max_output_bytes)
                async for raw in iterator:
                    frames = sse.feed(raw)
                    output: list[Event] = []
                    for data in frames:
                        output.extend(decoder.feed(data))
                    if decoder.done:
                        sse.finish()
                        # A valid model terminal marker, not a client disconnect, ends delivery.
                        finish = next(e for e in output if isinstance(e, Finished))
                        # Persist measured usage before any cancellable settlement/reporting await.
                        if self._usage_recorder:
                            self._usage_recorder(operation_ref, finish)
                        await stream.complete()
                        try:
                            await asyncio.wait_for(
                                self._engine.jobs.report_usage(
                                    self._actor,
                                    stream.job.id,
                                    {
                                        "prompt_tokens": finish.input_tokens,
                                        "completion_tokens": finish.output_tokens,
                                        "cached_prompt_tokens": finish.cached_input_tokens,
                                    },
                                ),
                                timeout=5,
                            )
                        except Exception:
                            # Keep the paid reply if accounting reporting fails.
                            self._logger.warning(
                                json.dumps(
                                    {
                                        "event": "livepeer_usage_report_failed",
                                        "request_id": operation_ref,
                                        "job_id": str(stream.job.id),
                                        "input_tokens": finish.input_tokens,
                                        "output_tokens": finish.output_tokens,
                                    }
                                )
                            )
                    for event in output:
                        yield event
                    if decoder.done:
                        return
                sse.finish()
                raise BackendError("Runner stream ended before a valid terminal marker")
            except (asyncio.CancelledError, GeneratorExit):
                # Notify before iterator close, which would abandon immediately.
                with anyio.CancelScope(shield=True):
                    await stream.disconnected()
                raise
            finally:
                with anyio.CancelScope(shield=True):
                    try:
                        await stream.aclose()
                    finally:
                        await iterator.aclose()
                self._logger.info(
                    json.dumps(
                        {
                            "event": "livepeer_job_closed",
                            "request_id": operation_ref,
                            "job_id": str(stream.job.id),
                            "state": stream.job.state,
                        }
                    )
                )
        except JobFailed as exc:
            self._logger.warning(
                json.dumps(
                    {
                        "event": "livepeer_job_failed",
                        "request_id": operation_ref,
                        "job_id": str(exc.job.id),
                        "state": exc.job.state,
                    }
                )
            )
            failure = exc.job.failure
            if failure is not None and failure.kind == "timeout":
                raise BackendDeadline("Livepeer generation deadline exceeded") from exc
            if failure is not None and failure.kind in {
                "no_offering",
                "no_capacity",
                "not_provisioned",
                "refused",
                "unreachable",
                "payment",
            }:
                raise BackendUnavailable("Livepeer generation unavailable") from exc
            raise BackendError("Livepeer generation failed") from exc
        except (NoCapacity, NotProvisioned, ProviderUnavailable) as exc:
            raise BackendUnavailable("Livepeer generation unavailable") from exc
        except (OperationExists, OperationConflict) as exc:
            raise BackendConflict("Generation operation already dispatched") from exc
        except EngineError as exc:
            raise BackendError("Livepeer engine failed") from exc
