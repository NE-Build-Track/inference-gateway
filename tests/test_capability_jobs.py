import asyncio

import pytest
from stream_pack.catalog import load_catalog

from inference_gateway.adapters.capability_jobs import JobConflict, Jobs


class FakeExecutor:
    def __init__(self):
        self.calls = 0
        self.block = False
        self.cancelled = asyncio.Event()

    async def execute(self, capability, inputs, *, timeout):  # noqa: ASYNC109
        self.calls += 1
        if self.block:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
        return {"text": "transcript", "artifacts": []}


async def terminal(jobs, user, identifier):
    for _ in range(100):
        job = jobs.get(user, identifier)
        if job["status"] not in {"queued", "running"}:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("Job did not finish")


@pytest.mark.asyncio
async def test_idempotency_is_tenant_scoped_and_inputs_cannot_change(tmp_path):
    executor = FakeExecutor()
    jobs = Jobs(
        str(tmp_path / "jobs.sqlite"),
        str(tmp_path / "artifacts"),
        executor,
        load_catalog(),
        output_root=str(tmp_path / "outputs"),
    )
    await jobs.start()
    try:
        one = await jobs.submit("alice", "whisper", {"audio": "x.wav"}, "key")
        same = await jobs.submit("alice", "whisper", {"audio": "x.wav"}, "key")
        assert one["id"] == same["id"]
        with pytest.raises(JobConflict):
            await jobs.submit("alice", "whisper", {"audio": "y.wav"}, "key")
        with pytest.raises(KeyError):
            jobs.get("bob", one["id"])
        other = await jobs.submit("bob", "whisper", {"audio": "x.wav"}, "key")
        assert other["id"] != one["id"]
        assert (await terminal(jobs, "alice", one["id"]))["status"] == "succeeded"
        await terminal(jobs, "bob", other["id"])
        assert executor.calls == 2
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_running_cancel_waits_for_executor_cleanup(tmp_path):
    executor = FakeExecutor()
    executor.block = True
    jobs = Jobs(
        str(tmp_path / "jobs.sqlite"),
        str(tmp_path / "artifacts"),
        executor,
        load_catalog(),
        output_root=str(tmp_path),
    )
    await jobs.start()
    try:
        value = await jobs.submit("alice", "whisper", {"audio": "x.wav"})
        for _ in range(100):
            if jobs.get("alice", value["id"])["status"] == "running":
                break
            await asyncio.sleep(0.01)
        result = await jobs.cancel("alice", value["id"])
        assert executor.cancelled.is_set()
        assert result["status"] == "cancelled"
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_restart_marks_interrupted_jobs_failed_without_replay(tmp_path):
    path = str(tmp_path / "jobs.sqlite")
    executor = FakeExecutor()
    executor.block = True
    jobs = Jobs(
        path, str(tmp_path / "artifacts"), executor, load_catalog(), output_root=str(tmp_path)
    )
    await jobs.start()
    value = await jobs.submit("alice", "whisper", {"audio": "x.wav"})
    for _ in range(100):
        if jobs.get("alice", value["id"])["status"] == "running":
            break
        await asyncio.sleep(0.01)
    await jobs.close()
    recovered = Jobs(
        path, str(tmp_path / "artifacts"), executor, load_catalog(), output_root=str(tmp_path)
    )
    await recovered.start()
    try:
        assert recovered.get("alice", value["id"])["status"] == "failed"
        assert executor.calls == 1
        assert recovered.get("alice", value["id"])["error"] == "execution_interrupted"
    finally:
        await recovered.close()
