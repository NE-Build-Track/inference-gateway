"""Durable receipts and response artifacts for synchronous network batch calls."""

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


class BatchConflict(ValueError):
    pass


class BatchStore:
    """Stores network outcomes; never executes or replays generation."""

    def __init__(self, database: str, artifacts: str, *, retention: int = 86400):
        if retention <= 0:
            raise ValueError("Batch artifact retention must be positive")
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(artifacts).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.retention = retention
        self.database = Path(database).resolve()
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS batch_requests (
            id TEXT PRIMARY KEY,owner TEXT NOT NULL,model TEXT NOT NULL,endpoint TEXT NOT NULL,
            fingerprint TEXT NOT NULL,idempotency TEXT,state TEXT NOT NULL,
            created REAL NOT NULL,network_job_id TEXT,response_artifact TEXT,
            status_code INTEGER,headers TEXT,usage TEXT,error TEXT,expires REAL,
            UNIQUE(owner,idempotency));
          CREATE TABLE IF NOT EXISTS batch_artifacts (
            id TEXT PRIMARY KEY,request_id TEXT NOT NULL,owner TEXT NOT NULL,
            media_type TEXT NOT NULL,expires REAL NOT NULL);
        """)

    @contextmanager
    def server_lock(self) -> Iterator[None]:
        import fcntl

        with self.database.with_suffix(self.database.suffix + ".lock").open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def recover(self) -> None:
        self.db.execute(
            "UPDATE batch_requests SET state='failed',error='upstream_outcome_unknown' "
            "WHERE state='running'"
        )
        self.expire()

    def begin(
        self, owner: str, model: str, endpoint: str, fingerprint: str, key: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        self.expire()
        if key:
            row = self.db.execute(
                "SELECT * FROM batch_requests WHERE owner=? AND idempotency=?", (owner, key)
            ).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise BatchConflict("Idempotency key belongs to another request")
                return dict(row), False
        identifier = "batch_" + uuid4().hex
        self.db.execute(
            "INSERT INTO batch_requests (id,owner,model,endpoint,"
            "fingerprint,idempotency,state,created) "
            "VALUES (?,?,?,?,?,?,'running',?)",
            (identifier, owner, model, endpoint, fingerprint, key, time.time()),
        )
        return self.get(owner, identifier), True

    def get(self, owner: str, identifier: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM batch_requests WHERE id=? AND owner=?", (identifier, owner)
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        return dict(row)

    def bind(self, identifier: str, network_job_id: str) -> None:
        self.db.execute(
            "UPDATE batch_requests SET network_job_id=? WHERE id=?", (network_job_id, identifier)
        )

    def put_artifact(self, owner: str, identifier: str, body: bytes, media_type: str) -> str:
        artifact = "art_" + uuid4().hex
        path = self.root / artifact
        try:
            path.write_bytes(body)
            self.db.execute(
                "INSERT INTO batch_artifacts VALUES (?,?,?,?,?)",
                (artifact, identifier, owner, media_type, time.time() + self.retention),
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return artifact

    def finish(
        self,
        owner: str,
        identifier: str,
        status: int,
        headers: tuple[tuple[str, str], ...],
        body: bytes,
        usage: dict[str, int | float],
    ) -> str:
        content_type = next(
            (v for k, v in headers if k.lower() == "content-type"), "application/octet-stream"
        )
        artifact = self.put_artifact(owner, identifier, body, content_type)
        self.db.execute(
            "UPDATE batch_requests SET state=?,response_artifact=?,status_code=?,headers=?,"
            "usage=?,expires=? "
            "WHERE id=? AND owner=?",
            (
                "succeeded" if 200 <= status < 300 else "failed",
                artifact,
                status,
                json.dumps(headers),
                json.dumps(usage),
                time.time() + self.retention,
                identifier,
                owner,
            ),
        )
        return artifact

    def fail(self, identifier: str, code: str) -> None:
        self.db.execute(
            "UPDATE batch_requests SET state='failed',error=?,expires=? "
            "WHERE id=? AND state='running'",
            (code, time.time() + self.retention, identifier),
        )

    def artifact(self, owner: str, identifier: str) -> tuple[Path, str]:
        self.expire()
        row = self.db.execute(
            "SELECT * FROM batch_artifacts WHERE id=? AND owner=? AND expires>?",
            (identifier, owner, time.time()),
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        return self.root / identifier, row["media_type"]

    def replay(
        self, owner: str, row: dict[str, Any]
    ) -> tuple[int, tuple[tuple[str, str], ...], bytes]:
        if row["state"] == "expired":
            raise KeyError(row["id"])
        if not row["response_artifact"]:
            raise BatchConflict("Network outcome is incomplete; generation must not be repeated")
        path, _ = self.artifact(owner, row["response_artifact"])
        return (
            row["status_code"],
            tuple(tuple(pair) for pair in json.loads(row["headers"])),
            path.read_bytes(),
        )

    def expire(self) -> None:
        now = time.time()
        for row in self.db.execute(
            "SELECT id FROM batch_artifacts WHERE expires<=?", (now,)
        ).fetchall():
            (self.root / row["id"]).unlink(missing_ok=True)
            self.db.execute("DELETE FROM batch_artifacts WHERE id=?", (row["id"],))
        self.db.execute(
            "UPDATE batch_requests SET state='expired',response_artifact=NULL "
            "WHERE expires<=? AND state!='running'",
            (now,),
        )

    def close(self) -> None:
        self.db.close()


def request_fingerprint(endpoint: str, model: str, body: bytes) -> str:
    return hashlib.sha256(endpoint.encode() + b"\0" + model.encode() + b"\0" + body).hexdigest()
