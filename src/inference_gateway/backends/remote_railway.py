"""Raw HTTP jobs through a remote Core service; no embedded engine or payment secrets."""

import base64
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from inference_gateway.core.backend import BackendError, BackendUnavailable
from inference_gateway.core.native import NativeRequest


class RemoteRailwayTransport:
    def __init__(self, client: httpx.AsyncClient, timeout: float) -> None:
        self.client, self.timeout = client, timeout

    async def open(
        self, credential: str, profile: dict[str, Any], command: NativeRequest
    ) -> "RemoteRailwayStream":
        content_type = dict(command.headers).get("content-type", "application/json")
        body = {
            "app": profile["app"],
            "model": command.model,
            "path": profile["path"],
            "method": "POST",
            "capability": "native.http.v1",
            "raw_body": base64.urlsafe_b64encode(command.body).decode(),
            "content_type": content_type,
            "return_http_errors": True,
            "operation_ref": command.request_id,
            "max_attempts": 1,
            "timeout_s": self.timeout,
            "first_byte_timeout_s": min(60, self.timeout),
            "idle_timeout_s": min(60, self.timeout),
        }
        request = self.client.build_request(
            "POST",
            "/v1/jobs?stream=true",
            json=body,
            headers={"Authorization": credential, "Accept-Encoding": "identity"},
        )
        try:
            response = await self.client.send(request, stream=True)
        except httpx.HTTPError:
            # Never retry a submission: the remote engine may already have paid.
            raise BackendUnavailable("Remote job outcome unknown") from None
        job_id = response.headers.get("livepeer-job-id")
        if response.status_code in {401, 403, 409, 413, 422, 429}:
            status = response.status_code
            await response.aclose()
            error = BackendError("Core refused the job")
            error.status_code = status
            raise error
        if not job_id:
            await response.aclose()
            raise BackendUnavailable("Remote job was not opened")
        return RemoteRailwayStream(response, job_id)


class RemoteRailwayStream:
    def __init__(self, response: httpx.Response, job_id: str) -> None:
        self.response, self.job_id = response, job_id
        self.status_code = response.status_code
        self.headers = tuple(response.headers.multi_items())

    async def __aiter__(self) -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in self.response.aiter_raw():
                yield chunk
        except httpx.HTTPError:
            raise BackendError("Remote job stream interrupted") from None

    async def complete(self) -> None:
        pass  # Core owns completion, payment settlement and measured usage.

    async def disconnected(self) -> None:
        await self.response.aclose()  # The remote Core HTTP disconnect invokes its settle path.

    async def aclose(self) -> None:
        await self.response.aclose()
