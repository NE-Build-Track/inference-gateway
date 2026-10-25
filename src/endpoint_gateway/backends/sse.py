"""Bounded SSE data decoder; accepts arbitrary byte/UTF-8 and CR/LF boundaries."""

from endpoint_gateway.core.backend import BackendError


class SSEDecoder:
    def __init__(self, max_event_bytes: int) -> None:
        self._limit = max_event_bytes
        self._line = bytearray()
        self._data: list[str] = []
        self._size = 0
        self._after_cr = False

    def feed(self, chunk: bytes) -> list[str]:
        events: list[str] = []
        for byte in chunk:
            if self._after_cr and byte == 10:
                self._after_cr = False
                continue
            self._after_cr = byte == 13
            if byte in (10, 13):
                self._end_line(events)
            else:
                self._size += 1
                if self._size > self._limit:
                    raise BackendError("Runner SSE frame exceeds configured limit")
                self._line.append(byte)
        return events

    def _end_line(self, events: list[str]) -> None:
        try:
            line = self._line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BackendError("Invalid runner UTF-8") from exc
        self._line.clear()
        if not line:
            if self._data:
                events.append("\n".join(self._data))
            self._data.clear()
            self._size = 0
        elif line.startswith("data:"):
            data = line[5:]
            self._data.append(data[1:] if data.startswith(" ") else data)
        elif line == "data":
            self._data.append("")
        # Comments and other SSE fields do not carry completion data.

    def finish(self) -> None:
        if self._line or self._data:
            raise BackendError("Truncated runner SSE frame")
