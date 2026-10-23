"""PostgreSQL-backed, at-least-once worker. No web request owns job execution."""
import argparse
import csv
import json
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from .db import SessionLocal
from .models import Artifact, Dataset, Episode, Job, JobAttempt, now
from .services import record_event
from .settings import settings
from .storage import controlled_path, copy_snapshot, remove_tree, sha_file, write_json


class Cancelled(Exception):
    pass


class LeaseLost(Exception):
    pass


def recover_expired():
    """Fence a lost attempt before requeueing. Old workers cannot publish after losing their lease."""
    with SessionLocal.begin() as db:
        expired = list(db.scalars(select(Job).where(Job.status.in_(["running", "cancel_requested"]),
            Job.lease_expires_at < now()).order_by(Job.lease_expires_at).with_for_update(skip_locked=True).limit(20)))
        for job in expired:
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt))
            reason = f"worker {job.worker_id} 租约超时，attempt {job.attempt} 中断"
            if attempt:
                attempt.status, attempt.error, attempt.finished_at = "interrupted", reason, now()
            job.error = reason
            if job.status == "cancel_requested":
                job.status, job.finished_at = "cancelled", now()
            elif job.attempt < job.max_attempts:
                job.status, job.progress = "queued", 0
            else:
                job.status, job.finished_at = "failed", now()
            job.lease_expires_at = None
            record_event(db, job, reason + f"；当前状态 {job.status}", "warning")
        return len(expired)


def claim_job(worker_id: str):
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.status == "queued").order_by(Job.created_at)
                        .with_for_update(skip_locked=True).limit(1))
        if not job:
            return None
        job.attempt += 1
        job.status, job.worker_id, job.started_at = "running", worker_id, now()
        job.heartbeat_at, job.lease_expires_at = now(), now() + timedelta(seconds=settings.lease_seconds)
        job.finished_at, job.progress = None, 0
        # Do not erase old errors: attempt history and events explain recovery.
        db.add(JobAttempt(job_id=job.id, attempt=job.attempt, worker_id=worker_id, status="running"))
        record_event(db, job, f"{worker_id} 领取任务，attempt {job.attempt}", progress=0)
        db.flush()
        return {"id": job.id, "attempt": job.attempt, "worker_id": worker_id, "type": job.type,
                "params": job.params, "dataset_id": job.dataset_id, "experiment_id": job.experiment_id}


class JobContext:
    def __init__(self, ticket, stop_event=None):
        self.ticket = ticket
        self.stop_event = stop_event or threading.Event()
        self.heartbeat_stop = threading.Event()
        self.lost = threading.Event()
        self.last_check = 0.0
        self.last_event = 0.0
        self.last_progress = -1.0
        self.snapshots: set[Path] = set()
        self.tmp = settings.storage_root / "staging" / f"{ticket['id']}-{ticket['attempt']}"
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def _heartbeat(self):
        while not self.heartbeat_stop.wait(settings.heartbeat_seconds):
            try:
                (settings.storage_root / "logs" / f"worker-{self.ticket['worker_id']}.heartbeat").touch()
                with SessionLocal.begin() as db:
                    result = db.execute(update(Job).where(Job.id == self.ticket["id"],
                        Job.worker_id == self.ticket["worker_id"], Job.attempt == self.ticket["attempt"],
                        Job.status.in_(["running", "cancel_requested"]), Job.lease_expires_at > now())
                        .values(heartbeat_at=now(), lease_expires_at=now() + timedelta(seconds=settings.lease_seconds)))
                    if result.rowcount != 1:
                        self.lost.set()
                        return
            except Exception:
                # On a DB outage the lease expires. Publication always re-checks the DB fence.
                self.lost.set()
                return

    def _owned(self, db, locked=False):
        stmt = select(Job).where(Job.id == self.ticket["id"])
        if locked:
            stmt = stmt.with_for_update()
        job = db.scalar(stmt)
        if not job or job.worker_id != self.ticket["worker_id"] or job.attempt != self.ticket["attempt"]:
            raise LeaseLost("attempt 已被其他 worker 接管")
        if job.status == "cancel_requested" or self.stop_event.is_set():
            raise Cancelled("任务已取消或 worker 正在停止")
        if job.status != "running" or not job.lease_expires_at or job.lease_expires_at <= now():
            raise LeaseLost("任务租约失效")
        return job

    def check(self, force=False):
        if self.lost.is_set():
            raise LeaseLost("心跳失败，停止本次 attempt")
        if self.stop_event.is_set():
            raise Cancelled("worker 正在停止")
        clock = time.monotonic()
        if force or clock - self.last_check > 0.15:
            with SessionLocal() as db:
                self._owned(db)
            self.last_check = clock
        return False

    def progress(self, value, message):
        self.check()
        if value is None or message is None:
            return
        value = max(0, min(float(value), 0.99))
        clock = time.monotonic()
        if clock - self.last_event < 0.25 and value - self.last_progress < 0.02:
            return
        with SessionLocal.begin() as db:
            job = self._owned(db, locked=True)
            job.progress = value
            record_event(db, job, str(message)[:3000], progress=value)
        self.last_event, self.last_progress = clock, value

    def close(self):
        self.heartbeat_stop.set()
        self.thread.join(timeout=3)


@dataclass
class RunnerOutput:
    files: list[tuple[Path, str]]
    dataset: dict | None = None
    dataset_root: Path | None = None


class JobRunner:
    def run(self, ctx: JobContext) -> RunnerOutput:
        raise NotImplementedError


class ImportRunner(JobRunner):
    def run(self, ctx):
        from .data.lerobot_v3 import inspect_dataset
        params = ctx.ticket["params"]
        source = controlled_path(settings.storage_root / "incoming", params["path"])
        snapshot = ctx.tmp / "snapshot"
        ctx.tmp.mkdir(parents=True, exist_ok=False)
        copy_snapshot(source, snapshot, ctx.progress)
        ctx.progress(0.35, "读取 v3 元信息并计算快照内容指纹")
        metadata = inspect_dataset(snapshot)
        ctx.check(force=True)
        dest = settings.storage_root / "datasets" / ctx.ticket["id"] / f"attempt-{ctx.ticket['attempt']}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Track ownership BEFORE rename: cancellation/report IO can fail before run() returns.
        ctx.snapshots.add(dest)
        snapshot.replace(dest)
        report = {"kind": "import_report", "source_kind": "executed", "data_source_kind": params["source_kind"],
                  "dataset_fingerprint": metadata["fingerprint"], "revision": params.get("revision"),
                  "total_episodes": metadata["total_episodes"], "summary": metadata["summary"],
                  "note": "元信息索引完成；数据质量需要另行运行检查任务"}
        file = ctx.tmp / "import_report.json"
        write_json(file, report)
        ctx.progress(0.95, f"已索引 {metadata['total_episodes']} 个 episode")
        return RunnerOutput([(file, "import_report")], metadata, dest)


class DataRunner(JobRunner):
    def __init__(self, kind):
        self.kind = kind

    def run(self, ctx):
        from .data.lerobot_v3 import dataset_fingerprint, quality_report, split_episodes, statistics_report
        with SessionLocal() as db:
            dataset = db.get(Dataset, ctx.ticket["dataset_id"])
            if not dataset:
                raise ValueError("任务的数据集版本不存在")
            root = controlled_path(settings.storage_root, dataset.root_path)
            fingerprint, revision, source_kind = dataset.fingerprint, dataset.revision, dataset.source_kind
        ctx.tmp.mkdir(parents=True, exist_ok=False)
        ctx.progress(0, "读取不可变数据版本，开始处理")
        if dataset_fingerprint(root) != fingerprint:
            raise ValueError("数据快照内容与登记指纹不一致；请重新导入新版本，不覆盖历史数据")
        if self.kind == "validate":
            result = quality_report(root, ctx.progress, ctx.check)
            kind = "quality_report"
        elif self.kind == "statistics":
            result = statistics_report(root, ctx.progress, ctx.check)
            kind = "statistics"
        else:
            result = split_episodes(root, **ctx.ticket["params"])
            kind = "split"
        result.update({"dataset_id": ctx.ticket["dataset_id"], "dataset_fingerprint": fingerprint,
                       "dataset_revision": revision, "job_id": ctx.ticket["id"], "attempt": ctx.ticket["attempt"],
                       "source_kind": "executed", "data_source_kind": source_kind,
                       "params": ctx.ticket["params"]})
        path = ctx.tmp / f"{kind}.json"
        write_json(path, result)
        files = [(path, kind)]
        if self.kind == "validate":
            csv_file = ctx.tmp / "issues.csv"
            fields = ["severity", "rule", "episode_index", "frame_index", "field", "message"]
            with csv_file.open("w", newline="", encoding="utf-8-sig") as out:
                writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for issue in result.get("issues", []):
                    writer.writerow(issue)
            files.append((csv_file, "quality_issues_csv"))
        ctx.check(force=True)
        return RunnerOutput(files)


RUNNERS = {"import_dataset": ImportRunner(), "validate": DataRunner("validate"),
           "split": DataRunner("split"), "statistics": DataRunner("statistics")}


def publish(ctx, output):
    """Files first, then a fenced DB transaction. Roll back files on ordinary commit failure.

    A SIGKILL between rename and commit can leave an unreferenced directory; a later
    attempt has a different path and never treats that directory as a published result.
    """
    directory = settings.storage_root / "artifacts" / ctx.ticket["id"] / f"attempt-{ctx.ticket['attempt']}"
    directory.mkdir(parents=True, exist_ok=False)
    published = []
    unused_snapshot = None
    try:
        for path, kind in output.files:
            dest = directory / path.name
            path.replace(dest)
            published.append((dest, kind))
        with SessionLocal.begin() as db:
            job = ctx._owned(db, locked=True)
            if output.dataset:
                metadata = output.dataset
                dataset = db.scalar(select(Dataset).where(Dataset.fingerprint == metadata["fingerprint"]))
                if dataset is None:
                    p = ctx.ticket["params"]
                    dataset = Dataset(name=p["name"], source_kind=p["source_kind"], source_uri=p.get("source_uri"),
                        revision=p.get("revision"), fingerprint=metadata["fingerprint"], format_version=metadata["format_version"],
                        root_path=output.dataset_root.relative_to(settings.storage_root).as_posix(), status="indexed",
                        features=metadata["features"], summary=metadata["summary"], total_episodes=metadata["total_episodes"])
                    db.add(dataset)
                    db.flush()
                    for ep in metadata["episodes"]:
                        db.add(Episode(dataset_id=dataset.id, episode_index=ep["episode_index"], task=ep["task"],
                            length=ep["length"], duration=ep["duration"], cameras=ep["cameras"], details=ep["metadata"]))
                else:
                    unused_snapshot = output.dataset_root
                    record_event(db, job, "相同内容指纹已存在，复用不可变数据集记录")
                job.dataset_id = dataset.id
            for path, kind in published:
                db.add(Artifact(job_id=job.id, experiment_id=job.experiment_id, kind=kind, filename=path.name,
                    storage_path=path.relative_to(settings.storage_root).as_posix(), sha256=sha_file(path),
                    size=path.stat().st_size, source_kind="executed"))
            if job.type == "validate":
                report_path = next(p for p, kind in published if kind == "quality_report")
                report = json.loads(report_path.read_text())
                dataset = db.get(Dataset, job.dataset_id)
                dataset.status = "checked"
                dataset.summary = {**dataset.summary, "quality": report.get("summary", {}), "quality_job_id": job.id}
            job.status, job.progress, job.finished_at, job.lease_expires_at = "succeeded", 1, now(), None
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt))
            attempt.status, attempt.finished_at = "succeeded", now()
            record_event(db, job, "任务完成，报告产物已登记并可下载", progress=1)
        if unused_snapshot:
            remove_tree(unused_snapshot)
    except Exception:
        remove_tree(directory)
        raise


def finish_failure(ctx, error):
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.id == ctx.ticket["id"]).with_for_update())
        if not job or job.worker_id != ctx.ticket["worker_id"] or job.attempt != ctx.ticket["attempt"] or job.status not in {"running", "cancel_requested"}:
            return
        cancelled = isinstance(error, Cancelled) or job.status == "cancel_requested"
        job.status, job.error = ("cancelled" if cancelled else "failed"), str(error)[:8000]
        job.finished_at, job.lease_expires_at = now(), None
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt))
        if attempt:
            attempt.status, attempt.error, attempt.finished_at = job.status, job.error, now()
        record_event(db, job, f"{job.status}: {job.error}", "warning" if cancelled else "error")


def execute(ticket, stop_event=None):
    ctx = JobContext(ticket, stop_event)
    ctx.thread.start()
    output = None
    try:
        runner = RUNNERS.get(ticket["type"])
        if not runner:
            raise ValueError("任务类型没有可执行 runner；训练/仿真环境未配置")
        output = runner.run(ctx)
        ctx.check(force=True)
        publish(ctx, output)
    except LeaseLost as error:
        print(f"[{ticket['id']}] fenced: {error}", flush=True)
    except Exception as error:
        finish_failure(ctx, error)
        print(f"[{ticket['id']}] {type(error).__name__}: {error}", flush=True)
    finally:
        ctx.close()
        remove_tree(ctx.tmp)
        for snapshot in ctx.snapshots:
            with SessionLocal() as db:
                referenced = db.scalar(select(Dataset.id).where(Dataset.root_path == snapshot.relative_to(settings.storage_root).as_posix()))
            if not referenced:
                remove_tree(snapshot)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=f"{socket.gethostname()}-{os.getpid()}")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        fresh = [p for p in (settings.storage_root / "logs").glob("worker-*.heartbeat") if time.time() - p.stat().st_mtime < 30]
        raise SystemExit(0 if fresh else 1)
    if not all(c.isalnum() or c in "-_." for c in args.id):
        raise SystemExit("worker id 只能使用字母数字 -_.")
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    heartbeat = settings.storage_root / "logs" / f"worker-{args.id}.heartbeat"
    print(f"worker {args.id} started; lease={settings.lease_seconds}s", flush=True)
    try:
        while not stop.is_set():
            heartbeat.touch()
            recover_expired()
            ticket = claim_job(args.id)
            if ticket:
                execute(ticket, stop)
            if args.once:
                return
            stop.wait(settings.poll_seconds if not ticket else 0)
    finally:
        heartbeat.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
