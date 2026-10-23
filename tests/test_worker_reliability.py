"""Real processes + PostgreSQL locks/leases + public data, without fake runners.

The imported fixtures create a separate random test database, fresh migrations,
temporary storage, and a live uvicorn process for this test module.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone

import httpx
import psycopg
import pytest

from test_api_integration import PYTHON, ROOT, imported, server  # noqa: F401 -- explicitly register real fixtures


def _db(server):
    return psycopg.connect(server.env["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1))


def _worker(server, identifier):
    env = {**server.env, "LEASE_SECONDS": "2", "HEARTBEAT_SECONDS": "0.2", "POLL_SECONDS": "0.02"}
    log = server.logs.parent / f"{identifier}.log"
    with log.open("wb") as stream:
        process = subprocess.Popen([str(PYTHON), "-m", "app.worker", "--once", "--id", identifier],
                                   cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    return process, log


def _finish(process, log, timeout=40):
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        pytest.fail("worker timed out:\n" + log.read_text())
    assert process.returncode == 0, log.read_text()


def _job(server, dataset_id, kind="validate", key="worker-test", params=None):
    response = server.request("POST", "/jobs", json={"type": kind, "dataset_id": dataset_id,
        "params": params or {}, "idempotency_key": key})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def _wait_job(server, job_id, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    with httpx.Client(timeout=5) as client:
        while time.monotonic() < deadline:
            response = client.get(server.url + f"/api/jobs/{job_id}")
            assert response.status_code == 200, response.text
            last = response.json()
            if predicate(last):
                return last
            time.sleep(0.01)
    pytest.fail(f"job did not reach required state: {last}")


@pytest.fixture(scope="module")
def public_dataset(server):
    source = ROOT / "storage/incoming/public-small"
    if not (source / "SOURCE.json").is_file():
        pytest.skip("Fetch the fixed public sample before real worker crash/cancel acceptance")
    destination = server.storage / "incoming" / "public-worker-test"
    shutil.copytree(source, destination)
    provenance = json.loads((source / "SOURCE.json").read_text())
    response = server.request("POST", "/datasets", json={"name": "公共PushT进程恢复验证", "path": "public-worker-test",
        "source_kind": "public", "source_uri": provenance["source_uri"], "revision": provenance["revision"],
        "idempotency_key": "worker-public-import"})
    assert response.status_code == 202, response.text
    server.worker_once()
    job = server.request("GET", f"/jobs/{response.json()['id']}").json()
    assert job["status"] == "succeeded", job
    dataset = server.request("GET", f"/datasets/{job['dataset_id']}").json()
    assert dataset["total_episodes"] == 206
    return dataset


def test_skip_locked_concurrent_workers_claim_distinct_jobs(server, imported):
    """Hold the oldest row lock: both workers must skip it and finish other jobs."""
    ids = [_job(server, imported["dataset"]["id"], kind="statistics", key=f"atomic-claim-{number}") for number in range(3)]
    workers = []
    try:
        with _db(server) as db:
            db.execute("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (ids[0],))
            workers = [_worker(server, f"atomic-worker-{number}") for number in (1, 2)]
            for process, log in workers:
                _finish(process, log)
            # These assertions run while the first job is still locked.
            locked = server.request("GET", f"/jobs/{ids[0]}").json()
            assert locked["status"] == "queued" and locked["attempt"] == 0
            completed = [server.request("GET", f"/jobs/{job_id}").json() for job_id in ids[1:]]
            assert all(job["status"] == "succeeded" and job["attempt"] == 1 for job in completed), completed
            assert {job["worker_id"] for job in completed} == {"atomic-worker-1", "atomic-worker-2"}
            assert all(len(job["attempts"]) == 1 for job in completed)
            assert all(len(job["artifacts"]) == 1 for job in completed)
        # Releasing the lock makes the untouched first job eligible.
        process, log = _worker(server, "atomic-worker-after-unlock")
        workers.append((process, log))
        _finish(process, log)
        assert server.request("GET", f"/jobs/{ids[0]}").json()["status"] == "succeeded"
    finally:
        for process, _ in workers:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_sigkill_real_quality_work_recovers_after_lease_and_preserves_attempts(server, public_dataset):
    """Kill a worker only after real trajectory frame checks have produced progress."""
    job_id = _job(server, public_dataset["id"], key="actual-sigkill-recovery")
    process, log = _worker(server, "sigkill-first-worker")
    try:
        running = _wait_job(server, job_id, lambda job: job["status"] == "running" and job["progress"] > 0.1)
        assert running["attempt"] == 1 and running["heartbeat_at"]
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=5)
        assert process.returncode == -signal.SIGKILL
        stranded = server.request("GET", f"/jobs/{job_id}").json()
        assert stranded["status"] == "running" and stranded["attempt"] == 1, stranded
        previous_events = server.request("GET", f"/jobs/{job_id}/events").json()["items"]
        cursor = previous_events[-1]["id"]
        lease = datetime.fromisoformat(stranded["lease_expires_at"].replace("Z", "+00:00"))
        remaining = (lease - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            time.sleep(remaining + 0.15)
        recovery, recovery_log = _worker(server, "sigkill-recovery-worker")
        _finish(recovery, recovery_log)
        completed = server.request("GET", f"/jobs/{job_id}").json()
        assert completed["status"] == "succeeded" and completed["attempt"] == 2, completed
        attempts = completed["attempts"]
        assert [item["status"] for item in attempts] == ["interrupted", "succeeded"]
        assert "租约超时" in attempts[0]["error"] and attempts[0]["finished_at"]
        assert attempts[1]["worker_id"] == "sigkill-recovery-worker"
        assert "租约超时" in completed["error"]  # Earlier failure remains visible after success.
        assert len(completed["artifacts"]) == 2
        for artifact in completed["artifacts"]:
            assert artifact["job_id"] == job_id and artifact["source_kind"] == "executed"
            response = httpx.get(server.url + artifact["url"])
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        report = server.request("GET", f"/jobs/{job_id}/report").json()
        assert report["attempt"] == 2 and report["job_id"] == job_id
        assert report["dataset_fingerprint"] == public_dataset["fingerprint"]
        assert report["data_source_kind"] == "public" and report["source_kind"] == "executed"
        assert report["summary"]["frames_checked"] == 25650 and report["summary"]["valid"]
        events = server.request("GET", f"/jobs/{job_id}/events").json()["items"]
        assert [event["id"] for event in events] == sorted({event["id"] for event in events})
        assert any("租约超时" in event["message"] for event in events)
        suffix = server.request("GET", f"/jobs/{job_id}/events", params={"after": cursor}).json()["items"]
        assert suffix == [event for event in events if event["id"] > cursor]
        stream = server.request("GET", f"/jobs/{job_id}/stream", headers={"Last-Event-ID": str(cursor)})
        stream_ids = [int(line[4:]) for line in stream.text.splitlines() if line.startswith("id: ")]
        assert stream_ids == [event["id"] for event in suffix]
        assert "event: end" in stream.text
        with _db(server) as db:
            paths = [row[0] for row in db.execute("SELECT storage_path FROM artifacts WHERE job_id=%s", (job_id,))]
        assert paths and all("/attempt-2/" in path for path in paths)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_cancel_running_real_quality_task_publishes_no_artifact(server, public_dataset):
    job_id = _job(server, public_dataset["id"], key="actual-running-cancellation")
    process, log = _worker(server, "cancel-running-worker")
    try:
        running = _wait_job(server, job_id, lambda job: job["status"] == "running" and job["progress"] > 0.1)
        assert running["attempt"] == 1
        requested = server.request("POST", f"/jobs/{job_id}/cancel")
        assert requested.status_code == 200 and requested.json()["status"] == "cancel_requested", requested.text
        _finish(process, log)
        completed = server.request("GET", f"/jobs/{job_id}").json()
        assert completed["status"] == "cancelled" and completed["attempts"][0]["status"] == "cancelled"
        assert completed["artifacts"] == []
        assert not (server.storage / "staging" / f"{job_id}-1").exists()
        events = server.request("GET", f"/jobs/{job_id}/events").json()["items"]
        assert any("cancelled" in event["message"] for event in events)
        assert server.request("GET", f"/jobs/{job_id}/report").status_code == 404
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_real_runner_failure_manual_retry_retains_previous_error(server):
    source = server.storage / "incoming" / "missing-action"
    result = subprocess.run([str(PYTHON), "scripts/generate_fixture.py", "--output", str(source), "--variant", "missing_field"],
                            cwd=ROOT, env=server.env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    response = server.request("POST", "/datasets", json={"name": "缺action的真实格式异常样本", "path": "missing-action",
        "source_kind": "synthetic_demo", "idempotency_key": "missing-action-import"})
    assert response.status_code == 202, response.text
    server.worker_once()
    import_job = server.request("GET", f"/jobs/{response.json()['id']}").json()
    assert import_job["status"] == "succeeded", import_job
    job_id = _job(server, import_job["dataset_id"], kind="statistics", key="real-failure-retry")
    server.worker_once()
    failed = server.request("GET", f"/jobs/{job_id}").json()
    assert failed["status"] == "failed" and "缺少字段" in failed["error"]
    assert failed["artifacts"] == []
    retry = server.request("POST", f"/jobs/{job_id}/retry")
    assert retry.status_code == 200 and retry.json()["status"] == "queued"
    server.worker_once()
    again = server.request("GET", f"/jobs/{job_id}").json()
    assert again["status"] == "failed" and again["attempt"] == 2
    assert [attempt["status"] for attempt in again["attempts"]] == ["failed", "failed"]
    assert [attempt["error"] for attempt in again["attempts"]] == [failed["error"], failed["error"]]
    assert again["created_at"] == failed["created_at"] and again["artifacts"] == []
    assert not (server.storage / "staging" / f"{job_id}-1").exists()
    assert not (server.storage / "staging" / f"{job_id}-2").exists()


def test_api_restart_during_real_worker_job_does_not_lose_execution_or_logs(server, public_dataset):
    job_id = _job(server, public_dataset["id"], key="api-restart-during-quality")
    process, log = _worker(server, "api-independent-worker")
    try:
        running = _wait_job(server, job_id, lambda j: j["status"] == "running" and j["progress"] > 0.1)
        assert process.poll() is None, "real quality work must be active before API stop"
        cursor = server.request("GET", f"/jobs/{job_id}/events").json()["items"][-1]["id"]
        old_api_pid = server.process.pid
        server.stop()
        server.start()
        assert server.process.pid != old_api_pid
        _finish(process, log)
        completed = _wait_job(server, job_id, lambda j: j["status"] == "succeeded")
        assert completed["attempt"] == running["attempt"] == 1
        assert completed["worker_id"] == "api-independent-worker"
        report = server.request("GET", f"/jobs/{job_id}/report").json()
        assert report["summary"]["frames_checked"] == 25650
        suffix = server.request("GET", f"/jobs/{job_id}/events", params={"after": cursor}).json()["items"]
        assert suffix and suffix[-1]["progress"] == 1
        sse = server.request("GET", f"/jobs/{job_id}/stream", headers={"Last-Event-ID": str(cursor)})
        ids = [int(line[4:].strip()) for line in sse.text.splitlines() if line.startswith("id:")]
        assert ids == [event["id"] for event in suffix]
        assert "event: end" in sse.text
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
