"""SQLite authority for identity and retail; dated wholesale projections only.

Short synchronous transactions contain no network work. Each operation has its own
connection so CLI and server may safely coexist. One server process owns recovery.
"""

import fcntl
import hashlib
import json
import math
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from endpoint_gateway.core.accounting import QUANTUM, SCOPES, Observation, Price, Principal
from endpoint_gateway.core.models import Finished

SCHEMA = """
CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE api_keys(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL, scopes TEXT NOT NULL, created REAL NOT NULL,
 expires REAL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE prices(version TEXT PRIMARY KEY, model TEXT NOT NULL, currency TEXT NOT NULL,
 input_rate TEXT NOT NULL, output_rate TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE requests(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 key_id TEXT NOT NULL REFERENCES api_keys(id), model TEXT NOT NULL, backend TEXT NOT NULL,
 protocol TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
 price_version TEXT NOT NULL REFERENCES prices(version), outcome TEXT NOT NULL DEFAULT 'running',
 input_tokens INTEGER, output_tokens INTEGER, billing_status TEXT NOT NULL DEFAULT 'pending',
 job_id TEXT, wholesale_status TEXT NOT NULL DEFAULT 'pending', wholesale_amount TEXT,
 wholesale_currency TEXT, wholesale_basis TEXT, attempts TEXT NOT NULL DEFAULT '[]',
 reconciled REAL, next_reconcile REAL NOT NULL DEFAULT 0,
 reconcile_failures INTEGER NOT NULL DEFAULT 0,
 report_pending INTEGER NOT NULL DEFAULT 1);
CREATE TABLE entries(id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
 kind TEXT NOT NULL CHECK(kind IN ('charge','adjustment')), amount TEXT NOT NULL,
 currency TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL);
CREATE UNIQUE INDEX one_charge ON entries(request_id) WHERE kind='charge';
CREATE INDEX report_requests ON requests(user_id,created,id);
CREATE INDEX pending_requests ON requests(next_reconcile);
CREATE TRIGGER immutable_entries_update BEFORE UPDATE ON entries BEGIN
 SELECT RAISE(ABORT,'Ledger entries are immutable'); END;
CREATE TRIGGER immutable_entries_delete BEFORE DELETE ON entries BEGIN
 SELECT RAISE(ABORT,'Ledger entries are immutable'); END;
CREATE TRIGGER immutable_price BEFORE UPDATE OF model,currency,input_rate,output_rate
 ON prices BEGIN
 SELECT RAISE(ABORT,'Price versions are immutable'); END;
PRAGMA user_version=1;
"""


class SQLiteLedger:
    def __init__(self, path: str) -> None:
        if path == ":memory:":
            raise ValueError("Accounting requires a file database")
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).touch(mode=0o600, exist_ok=True)
        Path(path).chmod(0o600)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
                version = 1
            if version == 1:
                db.executescript("""BEGIN IMMEDIATE;
                    ALTER TABLE requests ADD COLUMN wholesale_usage TEXT NOT NULL DEFAULT '{}';
                    ALTER TABLE requests ADD COLUMN wholesale_usage_source TEXT;
                    PRAGMA user_version=2;
                    COMMIT;""")
                version = 2
            if version == 2:
                db.executescript("""BEGIN IMMEDIATE;
                    ALTER TABLE prices ADD COLUMN cached_rate TEXT;
                    ALTER TABLE requests ADD COLUMN cached_input_tokens INTEGER;
                    CREATE TRIGGER immutable_cached_price BEFORE UPDATE OF cached_rate ON prices
                      BEGIN SELECT RAISE(ABORT,'Price versions are immutable'); END;
                    PRAGMA user_version=3;
                    COMMIT;""")
            elif version != 3:
                raise ValueError("Unsupported accounting schema version")
        Path(path).chmod(0o600)

    @contextmanager
    def server_lock(self) -> Iterator[None]:
        """Fail closed if another server would recover this instance's active requests."""
        with open(self.path + ".lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another gateway owns this accounting database") from None
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_user(self, name: str) -> str:
        if not name.strip():
            raise ValueError("User name required")
        uid = "usr_" + uuid4().hex
        with self.connection() as db:
            db.execute("INSERT INTO users(id,name) VALUES (?,?)", (uid, name))
        return uid

    def users(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM users ORDER BY id")]

    def disable_user(self, user_id: str) -> None:
        with self.connection() as db:
            if not db.execute("UPDATE users SET disabled=1 WHERE id=?", (user_id,)).rowcount:
                raise ValueError("Unknown user")

    @staticmethod
    def _issue(
        db: sqlite3.Connection,
        user_id: str,
        name: str,
        scopes: frozenset[str],
        expires: float | None,
    ) -> tuple[str, str]:
        if not scopes or not scopes <= SCOPES:
            raise ValueError("Invalid key scopes")
        if expires is not None and (not math.isfinite(expires) or expires <= time.time()):
            raise ValueError("Expiry must be in the future")
        user = db.execute("SELECT disabled FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or user["disabled"]:
            raise ValueError("User missing or disabled")
        kid = "key_" + uuid4().hex
        secret = "gw_" + secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO api_keys VALUES (?,?,?,?,?,?,?,0)",
            (
                kid,
                user_id,
                name,
                hashlib.sha256(secret.encode()).hexdigest(),
                json.dumps(sorted(scopes)),
                time.time(),
                expires,
            ),
        )
        return kid, secret

    def create_key(
        self, user_id: str, name: str, scopes: frozenset[str], expires: float | None = None
    ) -> tuple[str, str]:
        with self.connection() as db:
            return self._issue(db, user_id, name, scopes, expires)

    def keys(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,user_id,name,scopes,created,expires,revoked FROM api_keys "
                    "WHERE (? IS NULL OR user_id=?) ORDER BY id",
                    (user_id, user_id),
                )
            ]

    def revoke_key(self, key_id: str) -> None:
        with self.connection() as db:
            if not db.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,)).rowcount:
                raise ValueError("Unknown key")

    def rotate_key(self, key_id: str) -> tuple[str, str]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM api_keys WHERE id=? AND revoked=0", (key_id,)
            ).fetchone()
            if not row:
                raise ValueError("Unknown or revoked key")
            result = self._issue(
                db,
                row["user_id"],
                row["name"],
                frozenset(json.loads(row["scopes"])),
                row["expires"],
            )
            db.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
            return result

    def authenticate(self, secret: str) -> Principal | None:
        digest = hashlib.sha256(secret.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute(
                "SELECT k.* FROM api_keys k JOIN users u ON u.id=k.user_id "
                "WHERE digest=? AND revoked=0 AND disabled=0 AND (expires IS NULL OR expires>?)",
                (digest, time.time()),
            ).fetchone()
        return (
            Principal(row["user_id"], row["id"], frozenset(json.loads(row["scopes"])))
            if row
            else None
        )

    def set_price(self, price: Price) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE prices SET active=0 WHERE model=?", (price.model,))
            db.execute(
                "INSERT INTO prices VALUES (?,?,?,?,?,1,?)",
                (
                    price.version,
                    price.model,
                    price.currency.lower(),
                    str(price.input_per_million),
                    str(price.output_per_million),
                    str(price.cached_input_per_million)
                    if price.cached_input_per_million is not None
                    else None,
                ),
            )

    def prices(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM prices ORDER BY model,version")]

    def begin(
        self, request_id: str, principal: Principal, model: str, backend: str, protocol: str
    ) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            price = db.execute(
                "SELECT version FROM prices WHERE model=? AND active=1", (model,)
            ).fetchone()
            if not price:
                raise ValueError("No retail price configured for model")
            now = time.time()
            db.execute(
                "INSERT INTO "
                "requests(id,user_id,key_id,model,backend,protocol,created,updated,price_version) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    principal.user_id,
                    principal.key_id,
                    model,
                    backend,
                    protocol,
                    now,
                    now,
                    price["version"],
                ),
            )

    def record_usage(self, request_id: str, usage: Finished) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT r.*,p.currency,p.input_rate,p.output_rate,p.cached_rate FROM requests r "
                "JOIN prices p ON p.version=r.price_version WHERE r.id=?",
                (request_id,),
            ).fetchone()
            if not row:
                raise ValueError("Unknown request")
            if row["input_tokens"] is not None:
                if (row["input_tokens"], row["output_tokens"], row["cached_input_tokens"] or 0) != (
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cached_input_tokens,
                ):
                    raise ValueError("Conflicting usage; use an explicit adjustment")
                return
            price = Price(
                row["price_version"],
                row["model"],
                row["currency"],
                Decimal(row["input_rate"]),
                Decimal(row["output_rate"]),
                Decimal(row["cached_rate"]) if row["cached_rate"] is not None else None,
            )
            charge = price.charge(usage)
            db.execute(
                "INSERT INTO entries VALUES (?,?,?,?,?,?,?)",
                (
                    "charge_" + request_id,
                    request_id,
                    "charge",
                    str(charge),
                    price.currency,
                    "measured usage",
                    time.time(),
                ),
            )
            db.execute(
                "UPDATE requests SET "
                "input_tokens=?,output_tokens=?,cached_input_tokens=?,"
                "billing_status='charged',updated=? WHERE id=?",
                (
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cached_input_tokens,
                    time.time(),
                    request_id,
                ),
            )

    def adjustment(self, request_id: str, amount: Decimal, reason: str, operation_id: str) -> None:
        if (
            not reason.strip()
            or not operation_id
            or not amount.is_finite()
            or abs(amount) > Decimal("1000000000")
        ):
            raise ValueError("Adjustment requires bounded amount, reason and operation ID")
        amount = amount.quantize(QUANTUM)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT p.currency FROM requests r JOIN prices p ON "
                "p.version=r.price_version WHERE r.id=?",
                (request_id,),
            ).fetchone()
            if not row:
                raise ValueError("Unknown request")
            previous = db.execute(
                "SELECT * FROM entries WHERE id=?", ("adjust_" + operation_id,)
            ).fetchone()
            if previous:
                if (previous["request_id"], Decimal(previous["amount"]), previous["reason"]) != (
                    request_id,
                    amount,
                    reason,
                ):
                    raise ValueError("Conflicting adjustment operation ID")
                return
            db.execute(
                "INSERT INTO entries VALUES (?,?,?,?,?,?,?)",
                (
                    "adjust_" + operation_id,
                    request_id,
                    "adjustment",
                    str(amount),
                    row["currency"],
                    reason,
                    time.time(),
                ),
            )

    def outcome(self, request_id: str, status: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE requests SET outcome=?,updated=? WHERE id=? AND outcome='running'",
                (status, time.time(), request_id),
            )

    def recover(self) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE requests SET outcome='interrupted',updated=? WHERE outcome='running'",
                (time.time(),),
            )

    def due(self, backend: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM requests WHERE backend=? AND next_reconcile<=? "
                    "AND outcome!='running' ORDER BY next_reconcile,created LIMIT ?",
                    (backend, time.time(), limit),
                )
            ]

    def observation(self, request_id: str, value: Observation) -> None:
        if value.usage is not None:
            self.record_usage(request_id, value.usage)
        with self.connection() as db:
            now = time.time()
            db.execute(
                "UPDATE requests SET "
                "job_id=COALESCE(?,job_id),wholesale_status=?,wholesale_amount=?, "
                "wholesale_currency=?,wholesale_basis=?,attempts=?,reconciled=?,next_reconcile=?, "
                "reconcile_failures=0,report_pending=?,wholesale_usage=?,"
                "wholesale_usage_source=? WHERE id=?",
                (
                    value.job_id,
                    value.status,
                    value.amount,
                    value.currency,
                    value.basis,
                    json.dumps(value.attempts),
                    now,
                    now + 60,
                    not value.usage_reported,
                    json.dumps(value.quantities),
                    value.usage_source,
                    request_id,
                ),
            )
            if value.status == "none":
                db.execute(
                    "UPDATE requests SET billing_status='not_billable' WHERE id=? AND "
                    "input_tokens IS NULL",
                    (request_id,),
                )

    def bind_job(self, request_id: str, job_id: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE requests SET job_id=? WHERE id=? AND (job_id IS NULL OR job_id=?)",
                (job_id, request_id, job_id),
            )

    def retry_reconciliation(self, request_id: str) -> None:
        with self.connection() as db:
            if not db.execute(
                "UPDATE requests SET next_reconcile=0,reconcile_failures=0 WHERE id=?",
                (request_id,),
            ).rowcount:
                raise ValueError("Unknown request")

    def reconcile_failed(self, request_id: str) -> None:
        with self.connection() as db:
            row = db.execute(
                "SELECT reconcile_failures FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            failures = row[0] + 1
            delay = min(3600, 5 * 2 ** min(failures, 10))
            # After eight failures retain evidence for explicit operator redrive.
            next_at = time.time() + delay if failures < 8 else 253402300799
            db.execute(
                "UPDATE requests SET reconcile_failures=?,next_reconcile=? WHERE id=?",
                (failures, next_at, request_id),
            )

    def get_request(self, principal: Principal, request_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT created FROM requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            return None
        records = self.rows(principal, row["created"], row["created"] + 1, request_id)
        return records[0] if records else None

    def rows(
        self, principal: Principal, start: float, end: float, request_id: str | None = None
    ) -> list[dict[str, Any]]:
        owner = None if principal.permits("admin") else principal.user_id
        with self.connection() as db:
            records = [
                dict(r)
                for r in db.execute(
                    "SELECT r.*,p.currency FROM requests r JOIN prices p ON "
                    "p.version=r.price_version "
                    "WHERE created>=? AND created<? AND (? IS NULL OR user_id=?) "
                    "AND (? IS NULL OR r.id=?) ORDER BY "
                    "created,id LIMIT 10001",
                    (start, end, owner, owner, request_id, request_id),
                )
            ]
            if len(records) > 10000:
                raise ValueError("Report exceeds 10000 requests; narrow the time window")
            for row in records:
                entries = [
                    dict(e)
                    for e in db.execute(
                        "SELECT * FROM entries WHERE request_id=? ORDER BY created,id", (row["id"],)
                    )
                ]
                row["retail_amount"] = (
                    str(sum((Decimal(e["amount"]) for e in entries), Decimal(0)))
                    if entries
                    else None
                )
                if row["billing_status"] == "not_billable" and not entries:
                    row["retail_amount"] = "0"
                row["entries"] = entries
                row["attempts"] = json.loads(row["attempts"])
                row["wholesale_usage"] = json.loads(row["wholesale_usage"])
                if not principal.permits("admin"):
                    for field in (
                        "job_id",
                        "wholesale_status",
                        "wholesale_amount",
                        "wholesale_currency",
                        "wholesale_basis",
                        "attempts",
                        "reconciled",
                        "next_reconcile",
                        "reconcile_failures",
                        "report_pending",
                        "backend",
                        "wholesale_usage",
                        "wholesale_usage_source",
                    ):
                        row.pop(field, None)
        return records
