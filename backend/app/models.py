import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Identity, Index
from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc)


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[str] = mapped_column(String(30))
    source_uri: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[str | None] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    format_version: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="indexed")
    root_path: Mapped[str] = mapped_column(Text)
    features: Mapped[dict] = mapped_column(JSONB)
    summary: Mapped[dict] = mapped_column(JSONB)
    total_episodes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("dataset_id", "episode_index"), Index("ix_episode_search", "dataset_id", "episode_index"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"))
    episode_index: Mapped[int] = mapped_column(Integer)
    task: Mapped[str] = mapped_column(Text)
    length: Mapped[int] = mapped_column(Integer)
    duration: Mapped[float] = mapped_column(Float)
    cameras: Mapped[list] = mapped_column(JSONB)
    # SQLAlchemy reserves metadata for schema metadata.
    details: Mapped[dict] = mapped_column("metadata", JSONB)


class PolicyCheckpoint(Base):
    __tablename__ = "checkpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(Text)
    revision: Mapped[str | None] = mapped_column(String(200))
    checksum: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(Text)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"), index=True)
    checkpoint_id: Mapped[str | None] = mapped_column(ForeignKey("checkpoints.id", ondelete="RESTRICT"))
    config: Mapped[dict] = mapped_column(JSONB)
    code_version: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed','cancel_requested','cancelled')"),
        CheckConstraint("attempt >= 0 AND max_attempts >= 1"),
        Index("ix_jobs_claim", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    type: Mapped[str] = mapped_column(String(50))
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id", ondelete="RESTRICT"), index=True)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.id", ondelete="RESTRICT"), index=True)
    params: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    worker_id: Mapped[str | None] = mapped_column(String(200))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_cursor", "job_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    level: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    progress: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), index=True)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    filename: Mapped[str] = mapped_column(String(200))
    storage_path: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    source_kind: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="RESTRICT"), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="RESTRICT"))
    source_kind: Mapped[str] = mapped_column(String(30))
    task_set: Mapped[list] = mapped_column(JSONB)
    seed: Mapped[int] = mapped_column(Integer)
    metric_definition: Mapped[str] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer)
    successes: Mapped[int] = mapped_column(Integer)
    success_rate: Mapped[float] = mapped_column(Float)
    mean_steps: Mapped[float] = mapped_column(Float)
    mean_latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
