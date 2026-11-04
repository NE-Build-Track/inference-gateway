"""One thread for ledger writes, so their disk waits never stall the event loop."""

import asyncio
import json
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")


class LedgerWriter:
    """Applies writes on one thread, in the order they were queued.

    A write is queued before its caller first awaits, so cancelling the caller never drops it, and
    one thread keeps a request's writes in order: begin, job, usage, then outcome.
    """

    def __init__(self) -> None:
        self._pool: ThreadPoolExecutor | None = None

    def _submit(self, write: Callable[..., T], *args: Any) -> Future[T]:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ledger-writer")
        return self._pool.submit(write, *args)

    def send(self, write: Callable[..., object], *args: Any) -> None:
        """Queue a write without waiting for it. A failure is logged by name and request ID."""
        self._submit(write, *args).add_done_callback(_logged(write, args))

    async def write(self, write: Callable[..., T], *args: Any) -> T:
        """Queue a write, wait for it, and raise its error. Cancelling the wait keeps the write."""
        done = asyncio.wrap_future(self._submit(write, *args))
        try:
            return await asyncio.shield(done)
        except asyncio.CancelledError:
            done.add_done_callback(_logged(write, args))
            raise

    async def flush(self) -> None:
        """Wait until every write queued so far has been applied."""
        await self.write(_nothing)

    def close(self) -> None:
        """Apply queued writes, then stop the thread. A later write starts a new one."""
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=True)


def _nothing() -> None:
    return None


def _logged(write: Callable[..., object], args: tuple[Any, ...]) -> Callable[[Any], None]:
    def check(future: Any) -> None:
        if not future.cancelled() and future.exception() is not None:
            # The exception may quote ledger values, so only the write and request are named.
            logging.getLogger("inference_gateway").warning(
                json.dumps(
                    {
                        "event": "ledger_write_failed",
                        "write": getattr(write, "__name__", "write"),
                        "request_id": args[0] if args and isinstance(args[0], str) else None,
                    }
                )
            )

    return check
