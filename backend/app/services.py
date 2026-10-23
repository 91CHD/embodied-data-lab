import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.inspection import inspect

from .models import Artifact, Dataset, Job, JobEvent


def record_event(session, job, message, level="info", progress=None):
    event = JobEvent(job_id=job.id, level=level, message=message, progress=progress)
    session.add(event)
    return event


def enqueue(session, type_, params, key, dataset_id=None, experiment_id=None):
    signature = hashlib.sha256(json.dumps({"type": type_, "params": params, "dataset_id": dataset_id,
        "experiment_id": experiment_id}, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        if existing.request_hash != signature:
            raise HTTPException(409, "幂等键已用于不同请求，请生成新的键")
        return existing
    job = Job(type=type_, params=params, dataset_id=dataset_id, experiment_id=experiment_id,
              idempotency_key=key, request_hash=signature, status="queued")
    try:
        session.add(job)
        session.flush()
        record_event(session, job, "任务已入队", progress=0)
        session.commit()
    except IntegrityError:
        # Simultaneous duplicate requests race on the unique key, not on a Python memory lock.
        session.rollback()
        existing = session.scalar(select(Job).where(Job.idempotency_key == key))
        if not existing or existing.request_hash != signature:
            raise HTTPException(409, "幂等请求冲突")
        return existing
    return job


def require(session, cls, id_):
    obj = session.get(cls, id_)
    if obj is None:
        raise HTTPException(404, "记录不存在")
    return obj


def as_dict(obj):
    values = {a.key: getattr(obj, a.key) for a in inspect(obj).mapper.column_attrs}
    if "details" in values:
        values["metadata"] = values.pop("details")
    for private in ("root_path", "storage_path", "request_hash", "idempotency_key"):
        values.pop(private, None)
    if isinstance(obj, Artifact):
        values["url"] = f"/api/artifacts/{obj.id}/download"
    return values


def job_dict(session, job):
    data = as_dict(job)
    data["artifacts"] = [as_dict(a) for a in session.scalars(
        select(Artifact).where(Artifact.job_id == job.id).order_by(Artifact.created_at))]
    return data


def dataset_dict(ds: Dataset):
    return as_dict(ds)
