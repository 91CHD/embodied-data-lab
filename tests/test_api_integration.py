"""Real PostgreSQL + migrated schema + real HTTP API, isolated from the workbench DB.

Run: uv run pytest -q tests/test_api_integration.py
Optional EDL_TEST_POSTGRES_DSN points to a PG admin database that permits CREATE DATABASE.
Each session creates and finally drops only its random embodied_api_test_* database.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"


@dataclass
class IsolatedServer:
    env: dict = field(repr=False)
    storage: Path
    port: int
    logs: Path
    process: subprocess.Popen | None = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def request(self, method, route, **kwargs):
        return httpx.request(method, self.url + "/api" + route, timeout=15, **kwargs)

    def start(self):
        with self.logs.open("ab") as output:
            self.process = subprocess.Popen(
                [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
                cwd=ROOT, env=self.env, stdout=output, stderr=subprocess.STDOUT,
            )
        for _ in range(100):
            if self.process.poll() is not None:
                pytest.fail("API startup failed:\n" + self.logs.read_text())
            try:
                if self.request("GET", "/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        pytest.fail("API startup timed out:\n" + self.logs.read_text())

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def worker_once(self):
        result = subprocess.run(
            [str(PYTHON), "-m", "app.worker", "--once", "--id", "isolated-api-test-worker"],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    admin_dsn = os.environ.get("EDL_TEST_POSTGRES_DSN", "postgresql://localhost:55432/postgres")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Real PostgreSQL unavailable; integration not verified: {exc}")
    database = "embodied_api_test_" + uuid.uuid4().hex[:12]
    temp = tmp_path_factory.mktemp("real-pg-api")
    env = dict(os.environ)
    db_options = psycopg.conninfo.conninfo_to_dict(admin_dsn)
    db_options["dbname"] = database
    from sqlalchemy.engine import URL
    env["DATABASE_URL"] = URL.create(
        "postgresql+psycopg", username=db_options.get("user"), password=db_options.get("password"),
        host=db_options.get("host", "localhost"), port=int(db_options.get("port", 55432)), database=database,
    ).render_as_string(hide_password=False)
    env.update(STORAGE_ROOT=str(temp / "storage"), PYTHONPATH=str(ROOT / "backend"), POLL_SECONDS="0.05")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    service = IsolatedServer(env, temp / "storage", port, temp / "api.log")
    try:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
        migrated = subprocess.run(
            [str(ROOT / ".venv/bin/alembic"), "upgrade", "head"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=60,
        )
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        with psycopg.connect(**db_options) as db:
            names = {row[0] for row in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
            assert {"datasets", "episodes", "jobs", "job_events", "artifacts", "experiments", "alembic_version"} <= names
        service.start()
        yield service
    finally:
        service.stop()
        assert database.startswith("embodied_api_test_")
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        admin.close()


@pytest.fixture(scope="module")
def imported(server):
    fixture_path = server.storage / "incoming" / "api-fixture"
    result = subprocess.run(
        [str(PYTHON), "scripts/generate_fixture.py", "--output", str(fixture_path)],
        cwd=ROOT, env=server.env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = {"name": "API 集成验证数据", "path": "api-fixture", "source_kind": "synthetic_demo", "idempotency_key": "api-fixture-import"}
    first = server.request("POST", "/datasets", json=payload)
    assert first.status_code == 202, first.text
    duplicate = server.request("POST", "/datasets", json=payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == first.json()["id"]
    conflict = server.request("POST", "/datasets", json={**payload, "name": "不同请求"})
    assert conflict.status_code == 409
    server.worker_once()
    job = server.request("GET", f"/jobs/{first.json()['id']}").json()
    assert job["status"] == "succeeded", job
    dataset = server.request("GET", f"/datasets/{job['dataset_id']}").json()
    assert dataset["source_kind"] == "synthetic_demo"
    assert dataset["total_episodes"] == 3
    return {"job": job, "dataset": dataset}


def test_fresh_migration_import_and_api_restart_persist(server, imported):
    dataset_id = imported["dataset"]["id"]
    first = server.request("GET", f"/datasets/{dataset_id}").json()
    server.stop()
    server.start()
    second = server.request("GET", f"/datasets/{dataset_id}").json()
    assert second == first
    assert server.request("GET", "/datasets", params={"q": "集成验证"}).json()["total"] == 1


def test_frames_shared_video_and_controlled_media_paths(server, imported):
    dataset_id = imported["dataset"]["id"]
    episodes = server.request("GET", f"/datasets/{dataset_id}/episodes").json()["items"]
    assert [x["episode_index"] for x in episodes] == [0, 1, 2]
    assert "metadata" in episodes[0] and "details" not in episodes[0]
    frame_page = server.request("GET", f"/datasets/{dataset_id}/episodes/1/frames", params={"offset": 2, "limit": 4})
    assert frame_page.status_code == 200, frame_page.text
    body = frame_page.json()
    assert body["offset"] == 2 and len(body["timestamps"]) == 4
    assert len(body["series"]["action"]) == 4
    assert len(body["cameras"]) == 2
    assert body["cameras"][0]["start_time"] != body["cameras"][1]["start_time"]
    camera = body["cameras"][0]
    video = httpx.get(server.url + camera["url"], headers={"Range": "bytes=0-99"})
    assert video.status_code == 206
    assert len(video.content) == 100
    assert video.headers["content-type"] == "video/mp4"
    for path in ("../secret.mp4", "/etc/passwd", "meta/info.json"):
        assert server.request("GET", f"/datasets/{dataset_id}/media", params={"path": path}).status_code == 400
    assert server.request("GET", f"/datasets/{dataset_id}/episodes/999/frames").status_code == 404


def test_job_idempotency_cancel_and_historical_events(server, imported):
    payload = {"type": "statistics", "dataset_id": imported["dataset"]["id"], "params": {}, "idempotency_key": "statistics-cancel-key"}
    first = server.request("POST", "/jobs", json=payload)
    second = server.request("POST", "/jobs", json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    conflict = server.request("POST", "/jobs", json={**payload, "type": "validate"})
    assert conflict.status_code == 409
    job_id = first.json()["id"]
    assert server.request("POST", f"/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert server.request("POST", f"/jobs/{job_id}/cancel").status_code == 409
    events = server.request("GET", f"/jobs/{job_id}/events").json()["items"]
    ids = [event["id"] for event in events]
    assert len(ids) >= 2 and ids == sorted(set(ids))
    suffix = server.request("GET", f"/jobs/{job_id}/events", params={"after": ids[0]}).json()["items"]
    assert [x["id"] for x in suffix] == ids[1:]
    stream = server.request("GET", f"/jobs/{job_id}/stream", headers={"Last-Event-ID": str(ids[0])})
    assert stream.status_code == 200
    assert f"id: {ids[0]}\n" not in stream.text
    assert "event: end" in stream.text


def _experiment(server, dataset_id, name):
    response = server.request("POST", "/experiments", json={
        "name": name, "purpose": "Isolated integration schema validation", "dataset_id": dataset_id,
        "config": {"seed": 42}, "code_version": "integration-test", "notes": "Not a model evaluation",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _bundle(seed=42):
    return {"source_kind": "synthetic_demo", "task_set": ["task-a"], "seed": seed, "notes": "",
            "metric_definition": "successes/trials; synthetic schema fixture",
            "trials": [{"task": "task-a", "success": True, "steps": 10, "latency_ms": 3},
                       {"task": "task-a", "success": False, "steps": 20, "latency_ms": 5}]}


def test_result_schema_comparison_artifact_and_provenance(server, imported):
    first = _experiment(server, imported["dataset"]["id"], "比较实验一")
    second = _experiment(server, imported["dataset"]["id"], "比较实验二")
    first_result = server.request("POST", f"/experiments/{first['id']}/results", json=_bundle())
    assert first_result.status_code == 201, first_result.text
    assert first_result.json()["count"] == 2 and first_result.json()["successes"] == 1
    assert first_result.json()["success_rate"] == 0.5
    assert server.request("POST", f"/experiments/{second['id']}/results", json=_bundle()).status_code == 201
    comparison = server.request("GET", "/experiments/compare", params={"ids": f"{first['id']},{second['id']}"})
    assert comparison.status_code == 200 and comparison.json()["comparable"] is True
    assert server.request("POST", f"/experiments/{second['id']}/results", json=_bundle(seed=7)).status_code == 201
    mismatch = server.request("GET", "/experiments/compare", params={"ids": f"{first['id']},{second['id']}"}).json()
    assert mismatch["comparable"] is False and any("seed" in reason for reason in mismatch["reasons"])
    for invalid in (
        {**_bundle(), "source_kind": "executed"},
        {**_bundle(), "success_rate": 1.0},
        {**_bundle(), "task_set": ["unknown"]},
        {**_bundle(), "trials": [{"task": "task-a", "success": "true", "steps": 1, "latency_ms": 0}]},
    ):
        response = server.request("POST", f"/experiments/{first['id']}/results", json=invalid)
        assert response.status_code == 422, response.text
    malformed = json.dumps(_bundle()).replace('"latency_ms": 3', '"latency_ms": NaN')
    assert server.request("POST", f"/experiments/{first['id']}/results", content=malformed, headers={"Content-Type": "application/json"}).status_code in {400, 422}
    detail = server.request("GET", f"/experiments/{first['id']}").json()
    artifact = detail["artifacts"][0]
    download = httpx.get(server.url + artifact["url"])
    assert download.status_code == 200 and download.json() == _bundle()
    assert artifact["source_kind"] == "synthetic_demo"
    assert len(artifact["sha256"]) == 64


def test_invalid_import_paths_body_limit_and_unconfigured_training(server, imported):
    base = {"name": "invalid", "source_kind": "local", "idempotency_key": "invalid-import-key"}
    for path in ("../", "/etc", "missing"):
        assert server.request("POST", "/datasets", json={**base, "path": path}).status_code == 400
    public_without_revision = {**base, "path": "api-fixture", "source_kind": "public"}
    assert server.request("POST", "/datasets", json=public_without_revision).status_code == 422
    huge = server.request("POST", "/checkpoints", content=b"x" * (2 * 1024 * 1024 + 1), headers={"Content-Type": "application/json"})
    assert huge.status_code == 413
    capabilities = server.request("GET", "/capabilities").json()
    assert capabilities["cpu"] is True and capabilities["train"]["available"] is False
    train = server.request("POST", "/jobs", json={"type": "train", "dataset_id": imported["dataset"]["id"],
        "params": {"repo_id": "local/test"}, "idempotency_key": "unconfigured-train"})
    assert train.status_code == 409


def test_new_source_version_preserves_old_snapshot(server, imported):
    dataset_id = imported["dataset"]["id"]
    original_fingerprint = imported["dataset"]["fingerprint"]
    source = server.storage / "incoming/api-fixture/SOURCE.json"
    metadata = json.loads(source.read_text())
    metadata["revision"] = "fixture-v2"
    metadata["note"] = "Changed source manifest; old snapshots must remain immutable"
    source.write_text(json.dumps(metadata))
    response = server.request("POST", "/datasets", json={
        "name": "API 第二版本", "path": "api-fixture", "source_kind": "synthetic_demo",
        "revision": "fixture-v2", "idempotency_key": "api-fixture-import-v2",
    })
    assert response.status_code == 202
    server.worker_once()
    new_job = server.request("GET", f"/jobs/{response.json()['id']}").json()
    assert new_job["status"] == "succeeded"
    assert new_job["dataset_id"] != dataset_id
    old = server.request("GET", f"/datasets/{dataset_id}").json()
    new = server.request("GET", f"/datasets/{new_job['dataset_id']}").json()
    assert old["fingerprint"] == original_fingerprint
    assert new["fingerprint"] != old["fingerprint"]
    assert server.request("GET", f"/datasets/{dataset_id}/episodes/1/frames").status_code == 200


def test_artifact_tamper_is_rejected_for_download_and_comparison(server, imported):
    dataset_id = imported["dataset"]["id"]
    first = _experiment(server, dataset_id, "校验实验一")
    second = _experiment(server, dataset_id, "校验实验二")
    for experiment in (first, second):
        assert server.request("POST", f"/experiments/{experiment['id']}/results", json=_bundle()).status_code == 201
    artifact = server.request("GET", f"/experiments/{first['id']}").json()["artifacts"][0]
    dsn = server.env["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as db:
        relative = db.execute("SELECT storage_path FROM artifacts WHERE id=%s", [artifact["id"]]).fetchone()[0]
    with (server.storage / relative).open("ab") as stream:
        stream.write(b" ")
    assert httpx.get(server.url + artifact["url"]).status_code == 409
    comparison = server.request("GET", "/experiments/compare", params={"ids": f"{first['id']},{second['id']}"})
    assert comparison.status_code == 409


def test_quality_report_tamper_missing_and_invalid_json_are_explicit(server, imported):
    response = server.request("POST", "/jobs", json={
        "type": "validate", "dataset_id": imported["dataset"]["id"],
        "params": {}, "idempotency_key": "quality-report-integrity-check",
    })
    assert response.status_code == 202
    job_id = response.json()["id"]
    server.worker_once()
    job = server.request("GET", f"/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    original_report = server.request("GET", f"/jobs/{job_id}/report")
    assert original_report.status_code == 200
    artifact = next(item for item in job["artifacts"] if item["kind"] == "quality_report")
    dsn = server.env["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as db:
        relative, expected_hash = db.execute("SELECT storage_path, sha256 FROM artifacts WHERE id=%s", [artifact["id"]]).fetchone()
    report_path = server.storage / relative
    original_bytes = report_path.read_bytes()
    try:
        report_path.write_bytes(original_bytes + b" ")
        changed = server.request("GET", f"/jobs/{job_id}/report")
        assert changed.status_code == 409 and changed.json().get("detail"), changed.text
        report_path.unlink()
        missing = server.request("GET", f"/jobs/{job_id}/report")
        assert missing.status_code == 409 and missing.json().get("detail"), missing.text
        invalid = b'{"summary": this-is-not-json}'
        report_path.write_bytes(invalid)
        # Reach the parsing boundary as well, rather than exercising only the hash mismatch.
        with psycopg.connect(dsn) as db:
            db.execute("UPDATE artifacts SET sha256=%s WHERE id=%s", [hashlib.sha256(invalid).hexdigest(), artifact["id"]])
        malformed = server.request("GET", f"/jobs/{job_id}/report")
        assert malformed.status_code == 409 and malformed.json().get("detail"), malformed.text
    finally:
        report_path.write_bytes(original_bytes)
        with psycopg.connect(dsn) as db:
            db.execute("UPDATE artifacts SET sha256=%s WHERE id=%s", [expected_hash, artifact["id"]])
    assert server.request("GET", f"/jobs/{job_id}/report").json() == original_report.json()
