"""Inject a report disk-write failure after a REAL snapshot has moved out of staging.

This is fault injection for file/DB compensation, not a fake runner or fake progress.
"""
import subprocess

from test_api_integration import PYTHON, ROOT, server  # noqa: F401


def test_import_report_io_failure_cleans_moved_unregistered_snapshot(server):
    source = server.storage / "incoming" / "io-failure"
    generated = subprocess.run([str(PYTHON), "scripts/generate_fixture.py", "--output", str(source)],
                               cwd=ROOT, env=server.env, capture_output=True, text=True)
    assert generated.returncode == 0, generated.stderr
    submitted = server.request("POST", "/datasets", json={"name": "IO fault fixture", "path": "io-failure",
        "source_kind": "synthetic_demo", "idempotency_key": "io-failure-import-key"})
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["id"]
    # Replacing only write_json leaves copy, inspect, hash, rename, job claim and failure DB writes real.
    code = """
import sys
from app import worker
original = worker.write_json
def fail_report(path, data):
    if path.name == 'import_report.json':
        assert list((worker.settings.storage_root/'datasets').glob('*/attempt-1'))
        raise OSError('injected report disk write failure after snapshot rename')
    return original(path, data)
worker.write_json = fail_report
sys.argv = ['worker', '--once', '--id', 'io-failure-worker']
worker.main()
"""
    run = subprocess.run([str(PYTHON), "-c", code], cwd=ROOT, env=server.env,
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    job = server.request("GET", f"/jobs/{job_id}").json()
    assert job["status"] == "failed", job
    assert "injected report disk write failure" in job["error"]
    assert job["attempts"][0]["status"] == "failed"
    assert job["dataset_id"] is None and job["artifacts"] == []
    assert server.request("GET", "/datasets").json()["total"] == 0
    assert not (server.storage / "datasets" / job_id / "attempt-1").exists()
    assert not (server.storage / "staging" / f"{job_id}-1").exists()
    assert (source / "meta/info.json").is_file(), "original data must remain"
