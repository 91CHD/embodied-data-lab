import asyncio
import json
import math
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .db import SessionLocal, get_session
from .models import Artifact, Dataset, Episode, EvaluationResult, Experiment, Job, JobAttempt, JobEvent, PolicyCheckpoint, now, uid
from .schemas import CheckpointCreate, DatasetImport, EvaluationBundle, ExperimentCreate, JobCreate, SplitParams
from .services import as_dict, dataset_dict, enqueue, job_dict, record_event, require
from .settings import settings
from .storage import controlled_path, sha_file, write_json
from .training import EvaluateParams, TrainParams, capabilities

app = FastAPI(title="具身数据与实验管理平台", version="0.1.0")
SessionDep = Depends(get_session)
TERMINAL = {"succeeded", "failed", "cancelled"}


@app.middleware("http")
async def limit_body(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        # Only small JSON is accepted: data/video files remain in the controlled incoming directory.
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 2 * 1024 * 1024:
                return JSONResponse(status_code=413, content={"detail": "请求不能超过 2MB"})
            chunks.append(chunk)
        request._body = b"".join(chunks)
        if request._body:
            try:
                def reject_constant(value):
                    raise ValueError(f"JSON不允许非有限数值: {value}")
                json.loads(request._body, parse_constant=reject_constant)
            except (ValueError, RecursionError, UnicodeError):
                return JSONResponse(status_code=400, content={"detail": "请求必须为有效JSON，且不能包含NaN/Infinity或过深嵌套"})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": finite_json(jsonable_encoder(exc.errors(), custom_encoder={ValueError: str}))})


def pages(session, statement, offset, limit):
    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    return list(session.scalars(statement.offset(offset).limit(limit))), total


@app.get("/api/health")
def health(session: Session = SessionDep):
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "postgresql"}


@app.get("/api/capabilities")
def get_capabilities():
    return capabilities()


@app.get("/api/sources")
def sources():
    items = []
    for path in sorted((settings.storage_root / "incoming").iterdir()):
        if not path.is_dir() or path.is_symlink() or not (path / "meta/info.json").is_file():
            continue
        item = {"path": path.name, "name": path.name, "source_kind": "local"}
        file = path / "SOURCE.json"
        if file.is_file() and file.stat().st_size < 20000:
            try:
                raw = json.loads(file.read_text())
                item.update({key: raw[key] for key in ("name", "source_kind", "source_uri", "revision") if key in raw})
            except (ValueError, OSError):
                pass
        items.append(item)
    return {"items": items, "total": len(items)}


@app.get("/api/datasets")
def datasets(q: str = Query("", max_length=200), offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), session: Session = SessionDep):
    stmt = select(Dataset).order_by(Dataset.created_at.desc())
    if q:
        stmt = stmt.where(Dataset.name.ilike(f"%{q}%"))
    rows, total = pages(session, stmt, offset, limit)
    return {"items": [dataset_dict(x) for x in rows], "total": total}


@app.post("/api/datasets", status_code=202)
def import_dataset(body: DatasetImport, session: Session = SessionDep):
    try:
        source = controlled_path(settings.storage_root / "incoming", body.path)
        if not source.is_dir():
            raise ValueError("数据源必须为目录")
    except ValueError as e:
        raise HTTPException(400, str(e))
    params = body.model_dump(exclude={"idempotency_key"})
    return job_dict(session, enqueue(session, "import_dataset", params, body.idempotency_key))


@app.get("/api/datasets/{dataset_id}")
def dataset_detail(dataset_id: str, session: Session = SessionDep):
    return dataset_dict(require(session, Dataset, dataset_id))


@app.get("/api/datasets/{dataset_id}/episodes")
def episodes(dataset_id: str, q: str = Query("", max_length=200), offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), session: Session = SessionDep):
    require(session, Dataset, dataset_id)
    stmt = select(Episode).where(Episode.dataset_id == dataset_id).order_by(Episode.episode_index)
    if q:
        filters = Episode.task.ilike(f"%{q}%")
        if q.isdigit():
            filters = filters | (Episode.episode_index == int(q))
        stmt = stmt.where(filters)
    rows, total = pages(session, stmt, offset, limit)
    return {"items": [as_dict(x) for x in rows], "total": total}


def finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_json(v) for v in value]
    return value


@app.get("/api/datasets/{dataset_id}/episodes/{episode_index}/frames")
def frames(dataset_id: str, episode_index: int, offset: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000), session: Session = SessionDep):
    from .data.lerobot_v3 import read_episode
    ds = require(session, Dataset, dataset_id)
    if not session.scalar(select(Episode.id).where(Episode.dataset_id == dataset_id, Episode.episode_index == episode_index)):
        raise HTTPException(404, "episode 不存在")
    try:
        result = read_episode(controlled_path(settings.storage_root, ds.root_path), episode_index, offset, limit)
        for camera in result["cameras"]:
            camera["url"] = f"/api/datasets/{dataset_id}/media?path={quote(camera.pop('path'), safe='')}"
        return finite_json(result)
    except (ValueError, OSError, KeyError) as e:
        raise HTTPException(422, str(e))


@app.get("/api/datasets/{dataset_id}/media")
def media(dataset_id: str, path: str, session: Session = SessionDep):
    ds = require(session, Dataset, dataset_id)
    try:
        root = controlled_path(settings.storage_root, ds.root_path)
        video = controlled_path(root, path)
        if video.suffix != ".mp4" or not video.is_file():
            raise ValueError("仅可读取数据集内 MP4")
        return FileResponse(video, media_type="video/mp4")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/jobs")
def jobs(dataset_id: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), session: Session = SessionDep):
    stmt = select(Job).order_by(Job.created_at.desc())
    if dataset_id:
        stmt = stmt.where(Job.dataset_id == dataset_id)
    rows, total = pages(session, stmt, offset, limit)
    return {"items": [job_dict(session, x) for x in rows], "total": total}


@app.post("/api/jobs", status_code=202)
def create_job(body: JobCreate, session: Session = SessionDep):
    require(session, Dataset, body.dataset_id)
    if body.experiment_id:
        exp = require(session, Experiment, body.experiment_id)
        if exp.dataset_id != body.dataset_id:
            raise HTTPException(422, "实验与任务的数据集版本不一致")
    try:
        if body.type in {"train", "evaluate"}:
            (TrainParams if body.type == "train" else EvaluateParams).model_validate(body.params)
            raise HTTPException(409, capabilities()[body.type]["reason"])
        if body.type == "split":
            body.params = SplitParams.model_validate(body.params).model_dump()
        elif body.params:
            raise HTTPException(422, "此任务不接受额外参数")
    except ValidationError as e:
        raise HTTPException(422, jsonable_encoder(e.errors(), custom_encoder={ValueError: str}))
    return job_dict(session, enqueue(session, body.type, body.params, body.idempotency_key, body.dataset_id, body.experiment_id))


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str, session: Session = SessionDep):
    job = require(session, Job, job_id)
    data = job_dict(session, job)
    data["attempts"] = [as_dict(x) for x in session.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt))]
    return data


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, session: Session = SessionDep):
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.status == "queued":
        job.status, job.finished_at = "cancelled", now()
    elif job.status == "running":
        job.status = "cancel_requested"
    elif job.status in TERMINAL:
        raise HTTPException(409, "任务已结束")
    record_event(session, job, "已请求取消任务", "warning")
    session.commit()
    return job_dict(session, job)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, session: Session = SessionDep):
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "仅失败或取消的任务可重试")
    job.status, job.progress, job.finished_at = "queued", 0, None
    job.worker_id, job.lease_expires_at = None, None
    job.max_attempts = max(job.max_attempts, job.attempt + 1)
    record_event(session, job, "手动重试已入队；保留前次 attempt 和错误", "warning", 0)
    session.commit()
    return job_dict(session, job)


@app.get("/api/jobs/{job_id}/events")
def events(job_id: str, after: int = Query(0, ge=0), session: Session = SessionDep):
    require(session, Job, job_id)
    rows = list(session.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > after).order_by(JobEvent.id).limit(1000)))
    return {"items": [as_dict(e) for e in rows], "total": len(rows)}


@app.get("/api/jobs/{job_id}/stream")
def stream(job_id: str, after: int = Query(0, ge=0), last_event_id: str | None = Header(None), session: Session = SessionDep):
    require(session, Job, job_id)
    if last_event_id:
        try:
            after = max(after, int(last_event_id))
        except ValueError:
            raise HTTPException(400, "Last-Event-ID 必须为整数")

    async def generate():
        cursor = after
        while True:
            # Never retain a transaction or connection across an SSE wait.
            with SessionLocal() as db:
                # Read terminal state first. If it is terminal, its final event is already committed
                # and the subsequent READ COMMITTED query must see it. Opposite order can miss it.
                status = db.get(Job, job_id).status
                rows = list(db.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > cursor).order_by(JobEvent.id).limit(250)))
                payloads = [(e.id, json.dumps(jsonable_encoder(as_dict(e)), ensure_ascii=False)) for e in rows]
            for id_, payload in payloads:
                cursor = id_
                yield f"id: {id_}\nevent: log\ndata: {payload}\n\n"
            if status in TERMINAL and len(rows) < 250:
                yield f"event: end\ndata: {json.dumps({'status': status, 'cursor': cursor})}\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{job_id}/report")
def job_report(job_id: str, session: Session = SessionDep):
    require(session, Job, job_id)
    artifact = session.scalar(select(Artifact).where(Artifact.job_id == job_id, Artifact.kind.in_(["quality_report", "split", "statistics", "import_report"])).order_by(Artifact.created_at.desc()))
    if not artifact:
        raise HTTPException(404, "报告尚未生成")
    return verified_artifact_json(artifact)


def verified_artifact_json(artifact):
    try:
        path = controlled_path(settings.storage_root, artifact.storage_path)
        if sha_file(path) != artifact.sha256:
            raise ValueError("校验值不匹配")
        return json.loads(path.read_text())
    except (ValueError, OSError, UnicodeError):
        raise HTTPException(409, "产物校验失败：文件被修改、丢失或无效，不能作为原始结果读取")


@app.get("/api/artifacts/{artifact_id}/download")
def download(artifact_id: str, session: Session = SessionDep):
    artifact = require(session, Artifact, artifact_id)
    try:
        path = controlled_path(settings.storage_root, artifact.storage_path)
        if sha_file(path) != artifact.sha256:
            raise HTTPException(409, "产物校验失败，文件可能已被外部修改")
        return FileResponse(path, filename=artifact.filename)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/checkpoints")
def checkpoints(session: Session = SessionDep):
    rows = list(session.scalars(select(PolicyCheckpoint).order_by(PolicyCheckpoint.created_at.desc())))
    return {"items": [as_dict(x) for x in rows], "total": len(rows)}


@app.post("/api/checkpoints", status_code=201)
def create_checkpoint(body: CheckpointCreate, session: Session = SessionDep):
    checkpoint = PolicyCheckpoint(**body.model_dump())
    session.add(checkpoint)
    session.commit()
    return as_dict(checkpoint)


@app.get("/api/experiments")
def experiments(session: Session = SessionDep):
    rows = list(session.scalars(select(Experiment).order_by(Experiment.created_at.desc())))
    return {"items": [as_dict(x) for x in rows], "total": len(rows)}


@app.post("/api/experiments", status_code=201)
def create_experiment(body: ExperimentCreate, session: Session = SessionDep):
    require(session, Dataset, body.dataset_id)
    if body.checkpoint_id:
        require(session, PolicyCheckpoint, body.checkpoint_id)
    exp = Experiment(**body.model_dump())
    session.add(exp)
    session.commit()
    return as_dict(exp)


@app.get("/api/experiments/compare")
def compare(ids: str, session: Session = SessionDep):
    selected = list(dict.fromkeys(ids.split(",")))
    if not 2 <= len(selected) <= 6:
        raise HTTPException(422, "请选择 2 至 6 个实验")
    items, conditions, reasons = [], [], []
    for id_ in selected:
        exp = require(session, Experiment, id_)
        ds = session.get(Dataset, exp.dataset_id)
        result = session.scalar(select(EvaluationResult).where(EvaluationResult.experiment_id == id_).order_by(EvaluationResult.created_at.desc()))
        checkpoint = session.get(PolicyCheckpoint, exp.checkpoint_id) if exp.checkpoint_id else None
        items.append({"experiment": as_dict(exp), "dataset": as_dict(ds), "checkpoint": as_dict(checkpoint) if checkpoint else None,
                      "result": as_dict(result) if result else None})
        if not result:
            reasons.append(f"{exp.name} 没有评测记录")
            continue
        artifact = session.get(Artifact, result.artifact_id)
        bundle = verified_artifact_json(artifact)
        counts = {t: sum(1 for trial in bundle["trials"] if trial["task"] == t) for t in sorted(result.task_set)}
        conditions.append({"数据版本": ds.fingerprint, "任务集合及样本数": counts,
                           "seed": result.seed, "指标口径": result.metric_definition,
                           "来源类型": result.source_kind})
    if len(conditions) == len(selected):
        for key in conditions[0]:
            if any(c[key] != conditions[0][key] for c in conditions[1:]):
                reasons.append(f"{key} 不一致，不可直接比较")
    return {"items": items, "comparable": not reasons, "reasons": reasons}


@app.get("/api/experiments/{experiment_id}")
def experiment_detail(experiment_id: str, session: Session = SessionDep):
    exp = require(session, Experiment, experiment_id)
    data = as_dict(exp)
    data["dataset"] = dataset_dict(session.get(Dataset, exp.dataset_id))
    data["checkpoint"] = as_dict(session.get(PolicyCheckpoint, exp.checkpoint_id)) if exp.checkpoint_id else None
    data["results"] = [as_dict(r) for r in session.scalars(select(EvaluationResult).where(EvaluationResult.experiment_id == experiment_id).order_by(EvaluationResult.created_at.desc()))]
    data["artifacts"] = [as_dict(a) for a in session.scalars(select(Artifact).where(Artifact.experiment_id == experiment_id))]
    data["jobs"] = [job_dict(session, j) for j in session.scalars(select(Job).where(Job.experiment_id == experiment_id).order_by(Job.created_at.desc()))]
    return data


@app.post("/api/experiments/{experiment_id}/results", status_code=201)
def import_result(experiment_id: str, body: EvaluationBundle, session: Session = SessionDep):
    require(session, Experiment, experiment_id)
    # Raw bytes and DB rows are separate transactions; compensate the file if commit fails.
    file = settings.storage_root / "artifacts" / "results" / f"{uid()}.json"
    try:
        write_json(file, body.model_dump())
        artifact = Artifact(experiment_id=experiment_id, kind="evaluation_bundle", filename="evaluation.json",
            storage_path=file.relative_to(settings.storage_root).as_posix(), sha256=sha_file(file), size=file.stat().st_size,
            source_kind=body.source_kind)
        session.add(artifact)
        session.flush()
        count = len(body.trials)
        successes = sum(t.success for t in body.trials)
        result = EvaluationResult(experiment_id=experiment_id, artifact_id=artifact.id, source_kind=body.source_kind,
            task_set=sorted(body.task_set), seed=body.seed, metric_definition=body.metric_definition, count=count,
            successes=successes, success_rate=successes / count,
            mean_steps=sum(t.steps for t in body.trials) / count, mean_latency_ms=sum(t.latency_ms for t in body.trials) / count)
        session.add(result)
        session.commit()
        return as_dict(result)
    except Exception:
        session.rollback()
        file.unlink(missing_ok=True)
        raise
