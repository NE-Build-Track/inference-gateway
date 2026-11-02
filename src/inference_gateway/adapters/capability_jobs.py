"""Single-host durable media jobs and tenant-owned artifacts.

Generation is injected by the composition root. This adapter never selects a model
backend or retries generation after an ambiguous outcome.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


class Executor(Protocol):
    async def execute(
        self,
        capability: str,
        inputs: dict[str, Any],
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> dict[str, Any]: ...


class JobConflict(ValueError):
    pass


class Jobs:
    def __init__(
        self,
        database: str,
        artifacts: str,
        executor: Executor,
        catalog: dict[str, Any],
        *,
        retention: int = 86400,
        queue_limit: int = 16,
        timeout: float = 900,
        output_root: str,
    ):
        if retention <= 0 or queue_limit <= 0 or timeout <= 0:
            raise ValueError("Job limits must be positive")
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS media_jobs (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, capability TEXT NOT NULL,
              fingerprint TEXT NOT NULL, idempotency TEXT, state TEXT NOT NULL,
              created REAL NOT NULL, expires REAL NOT NULL, revision TEXT NOT NULL,
              result TEXT, error TEXT, UNIQUE(owner, idempotency));
            CREATE TABLE IF NOT EXISTS media_artifacts (
              id TEXT PRIMARY KEY, job TEXT NOT NULL, owner TEXT NOT NULL,
              path TEXT NOT NULL, media_type TEXT NOT NULL, expires REAL NOT NULL);
        """)
        self.root = Path(artifacts).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.output_root = Path(output_root).resolve()
        self.executor, self.catalog = executor, catalog
        self.retention, self.queue_limit, self.timeout = retention, queue_limit, timeout
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.inputs: dict[str, dict[str, Any]] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_limit)
        self.worker: asyncio.Task[None] | None = None
        self.maintenance: asyncio.Task[None] | None = None
        self.input_root: Path | None = None
        self._closing = False

    async def start(self) -> None:
        # Request bodies are deliberately not persisted. Interrupted work is not replayed.
        self.db.execute(
            "UPDATE media_jobs SET state='failed',error='execution_interrupted' "
            "WHERE state IN ('queued','running')"
        )
        self.worker = asyncio.create_task(self._work())
        self.maintenance = asyncio.create_task(self._maintain())

    async def _maintain(self) -> None:
        while True:
            self.expire()
            await asyncio.sleep(min(60, self.retention))

    def get(self, owner: str, job: str) -> dict[str, Any]:
        self.expire()
        row = self.db.execute(
            "SELECT * FROM media_jobs WHERE id=? AND owner=?", (job, owner)
        ).fetchone()
        if row is None:
            raise KeyError(job)
        return {
            "id": row["id"],
            "object": "job",
            "capability": row["capability"],
            "status": row["state"],
            "created_at": int(row["created"]),
            "expires_at": int(row["expires"]),
            "deployment_revision": row["revision"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
        }

    async def submit(
        self, owner: str, capability: str, inputs: dict[str, Any], key: str | None = None
    ) -> dict[str, Any]:
        spec = self.catalog[capability]
        spec.validate(inputs)
        fingerprint = hashlib.sha256(
            json.dumps([capability, inputs], sort_keys=True).encode()
        ).hexdigest()
        if key:
            row = self.db.execute(
                "SELECT id,fingerprint FROM media_jobs WHERE owner=? AND idempotency=?",
                (owner, key),
            ).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise JobConflict("Idempotency key already belongs to a different request")
                return self.get(owner, row["id"])
        if self.worker is None or self.worker.done():
            raise RuntimeError("Job worker is not running")
        if self.queue.full():
            raise asyncio.QueueFull
        job, now = "job_" + uuid4().hex, time.time()
        self.db.execute(
            "INSERT INTO media_jobs VALUES (?,?,?,?,?,'queued',?,?,?,?,NULL)",
            (
                job,
                owner,
                capability,
                fingerprint,
                key,
                now,
                now + self.retention,
                spec.digest,
                None,
            ),
        )
        self.inputs[job] = dict(inputs)
        self.queue.put_nowait(job)
        return self.get(owner, job)

    async def _work(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                row = self.db.execute("SELECT state FROM media_jobs WHERE id=?", (job,)).fetchone()
                if row and row["state"] == "queued":
                    task = asyncio.create_task(self._execute(job))
                    self.tasks[job] = task
                    try:
                        await task
                    except asyncio.CancelledError:
                        current = asyncio.current_task()
                        if current is not None and current.cancelling():
                            raise
                    finally:
                        self.tasks.pop(job, None)
            finally:
                self.inputs.pop(job, None)
                self.queue.task_done()

    async def _execute(self, job: str) -> None:
        row = self.db.execute("SELECT * FROM media_jobs WHERE id=?", (job,)).fetchone()
        self.db.execute(
            "UPDATE media_jobs SET state='running' WHERE id=? AND state='queued'", (job,)
        )
        try:
            result = await self.executor.execute(
                row["capability"], self.inputs[job], timeout=self.timeout
            )
            self.catalog[row["capability"]].validate_output(result)
            public = {k: v for k, v in result.items() if k != "artifacts"}
            public["artifacts"] = []
            for item in result.get("artifacts", []):
                source = await asyncio.to_thread(Path(item["path"]).resolve)
                if not source.is_relative_to(self.output_root) or not await asyncio.to_thread(
                    source.is_file
                ):
                    raise ValueError("Backend returned an invalid artifact")
                artifact = "art_" + uuid4().hex
                destination = self.root / artifact
                await asyncio.to_thread(shutil.copyfile, source, destination)
                self.db.execute(
                    "INSERT INTO media_artifacts VALUES (?,?,?,?,?,?)",
                    (
                        artifact,
                        job,
                        row["owner"],
                        str(destination),
                        item["media_type"],
                        row["expires"],
                    ),
                )
                public["artifacts"].append(
                    {
                        "id": artifact,
                        "url": "/v1/artifacts/" + artifact,
                        "media_type": item["media_type"],
                    }
                )
            self.db.execute(
                "UPDATE media_jobs SET state='succeeded',result=? WHERE id=?",
                (json.dumps(public), job),
            )
        except asyncio.CancelledError:
            if self._closing:
                self.db.execute(
                    "UPDATE media_jobs SET state='failed',error='execution_interrupted' WHERE id=?",
                    (job,),
                )
            else:
                self.db.execute("UPDATE media_jobs SET state='cancelled' WHERE id=?", (job,))
            raise
        except Exception:
            # Do not persist raw backend exceptions, prompts or worker paths.
            self.db.execute(
                "UPDATE media_jobs SET state='failed',error='generation_failed' WHERE id=?", (job,)
            )

    async def cancel(self, owner: str, job: str) -> dict[str, Any]:
        value = self.get(owner, job)
        if value["status"] == "running":
            task = self.tasks.get(job)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        elif value["status"] == "queued":
            self.db.execute("UPDATE media_jobs SET state='cancelled' WHERE id=?", (job,))
            self.inputs.pop(job, None)
        return self.get(owner, job)

    def artifact(self, owner: str, artifact: str) -> tuple[Path, str]:
        self.expire()
        row = self.db.execute(
            "SELECT * FROM media_artifacts WHERE id=? AND owner=? AND expires>?",
            (artifact, owner, time.time()),
        ).fetchone()
        if row is None:
            raise KeyError(artifact)
        return Path(row["path"]), row["media_type"]

    def expire(self) -> None:
        now = time.time()
        rows = self.db.execute(
            "SELECT id,path FROM media_artifacts WHERE expires<=?", (now,)
        ).fetchall()
        for row in rows:
            Path(row["path"]).unlink(missing_ok=True)
            self.db.execute("DELETE FROM media_artifacts WHERE id=?", (row["id"],))
        self.db.execute(
            "UPDATE media_jobs SET state='expired',result=NULL WHERE expires<=? "
            "AND state NOT IN ('queued','running')",
            (now,),
        )
        if self.input_root is not None:
            rows = self.db.execute(
                "SELECT id,path FROM media_uploads WHERE expires<=? AND owner!='*'", (now,)
            ).fetchall()
            for row in rows:
                (self.input_root / row["path"]).unlink(missing_ok=True)
                self.db.execute("DELETE FROM media_uploads WHERE id=?", (row["id"],))

    async def close(self) -> None:
        self._closing = True
        if self.maintenance:
            self.maintenance.cancel()
        if self.worker:
            self.worker.cancel()
        await asyncio.gather(
            *(task for task in (self.maintenance, self.worker) if task), return_exceptions=True
        )
        self.db.execute(
            "UPDATE media_jobs SET state='failed',error='execution_interrupted' "
            "WHERE state IN ('queued','running')"
        )
        self.db.close()
