"""Operator-owned approvals: the evidence a runner may serve, never taken from runner metadata.

An approval names one deployment of one runner for one profile and model, and carries the passing
conformance report it was granted on. The file is re-read when it changes, so approving or revoking
needs no restart. Replacing the file revokes approvals for later selections; jobs already running
keep their settlement lifecycle.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from livepeer_runner_contract import ApprovalKey
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from inference_gateway.backends.discovery import origin
from inference_gateway.backends.runner_contract import Approver, RunnerContract

_LOGGER = logging.getLogger("inference_gateway")
RECHECK_SECONDS = 1.0


class Approval(BaseModel):
    """Expiry is UTC epoch seconds. `conformance` is the report `runner-contract conform` wrote."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    orchestrator: str
    runner_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    deployment_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    profile: str = Field(min_length=1)
    expires_at: float = Field(gt=0)
    conformance: dict[str, Any]


_APPROVALS = TypeAdapter(tuple[Approval, ...])


def parse_approvals(raw: bytes | str, contract: RunnerContract) -> tuple[Approval, ...]:
    """A JSON array of approvals, each checked against its report. Errors name no values."""
    try:
        approvals = _APPROVALS.validate_json(raw)
    except ValidationError:
        raise ValueError("Approvals must be a JSON array of approval objects") from None
    for index, approval in enumerate(approvals):
        where = f"approval {index}"
        try:
            origin(approval.orchestrator)
        except ValueError:
            raise ValueError(f"{where}: orchestrator must be an HTTP(S) origin") from None
        if not contract.is_profile(approval.profile):
            raise ValueError(f"{where}: profile is not an entry ID in the pinned registry")
        report = approval.conformance
        problems = contract.report_problems(report)
        if problems:
            raise ValueError(f"{where}: conformance report breaks its schema at {problems[0]}")
        if report["pass"] is not True:
            raise ValueError(f"{where}: conformance report did not pass")
        if (
            report["runner_id"] != approval.runner_id
            or report["deployment_revision"] != approval.deployment_revision
            or report["profile"] != approval.profile
        ):
            raise ValueError(
                f"{where}: conformance report is for another runner, revision or profile"
            )
    return approvals


def approver(
    approvals: tuple[Approval, ...], model: str, now: float
) -> Callable[[ApprovalKey], bool]:
    """Answers the standard's approval key for one model, by orchestrator origin and expiry."""

    def approved(key: ApprovalKey) -> bool:
        try:
            address = origin(key.orchestrator)
        except ValueError:
            return False
        return any(
            approval.runner_id == key.runner_id
            and approval.deployment_revision == key.deployment_revision
            and approval.profile == key.profile
            and approval.model == model
            and approval.expires_at > now
            and origin(approval.orchestrator) == address
            for approval in approvals
        )

    return approved


class Approvals(Protocol):
    def current(self) -> tuple[Approval, ...]: ...

    def approver(self, model: str) -> Approver: ...


class FixedApprovals:
    def __init__(
        self, approvals: tuple[Approval, ...] = (), *, now: Callable[[], float] = time.time
    ) -> None:
        self._approvals = approvals
        self._now = now

    def current(self) -> tuple[Approval, ...]:
        return self._approvals

    def approver(self, model: str) -> Approver:
        return approver(self._approvals, model, self._now())


class ApprovalFile:
    """A bad file fails at startup; after that, a bad edit keeps the last good approvals."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        contract: RunnerContract,
        *,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = Path(path)
        self._contract = contract
        self._now = now
        self._monotonic = monotonic
        self._stamp: tuple[int, int, int] | None = None
        self._checked = -RECHECK_SECONDS
        self._approvals: tuple[Approval, ...] = ()
        try:
            self._stamp, self._approvals = self._read()
        except OSError:
            raise ValueError("Approvals file is unreadable") from None
        self._checked = monotonic()

    def _read(self) -> tuple[tuple[int, int, int], tuple[Approval, ...]]:
        stat = self._path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        return stamp, parse_approvals(self._path.read_bytes(), self._contract)

    def current(self) -> tuple[Approval, ...]:
        if self._monotonic() - self._checked < RECHECK_SECONDS:
            return self._approvals
        self._checked = self._monotonic()
        try:
            stat = self._path.stat()
        except OSError:
            if self._stamp is not None:
                self._stamp = None
                self._warn("unreadable")
            return self._approvals
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if stamp == self._stamp:
            return self._approvals
        self._stamp = stamp  # before reading, so a bad edit warns once
        try:
            self._approvals = parse_approvals(self._path.read_bytes(), self._contract)
        except OSError:
            self._warn("unreadable")
        except ValueError as exc:
            self._warn(str(exc))
        else:
            _LOGGER.info(
                json.dumps({"event": "approvals_reloaded", "approvals": len(self._approvals)})
            )
        return self._approvals

    @staticmethod
    def _warn(reason: str) -> None:
        _LOGGER.warning(
            json.dumps({"event": "approvals_reload_failed", "reason": reason, "kept": True})
        )

    def approver(self, model: str) -> Approver:
        return approver(self.current(), model, self._now())
