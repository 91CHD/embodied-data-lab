import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(min_length=1, max_length=200)]
Key = Annotated[str, Field(min_length=8, max_length=200)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetImport(StrictModel):
    name: Name
    path: Annotated[str, Field(min_length=1, max_length=500)]
    source_kind: Literal["local", "public", "synthetic_demo"] = "local"
    source_uri: Annotated[str | None, Field(max_length=2000)] = None
    revision: Annotated[str | None, Field(max_length=200)] = None
    idempotency_key: Key

    @model_validator(mode="after")
    def provenance(self):
        if self.source_kind == "public" and (not self.revision or not self.source_uri):
            raise ValueError("公开数据需要 source_uri 和固定 revision")
        return self


class JobCreate(StrictModel):
    type: Literal["validate", "split", "statistics", "train", "evaluate"]
    dataset_id: str
    experiment_id: str | None = None
    params: dict = Field(default_factory=dict)
    idempotency_key: Key


class SplitParams(StrictModel):
    ratio: float = Field(default=0.8, gt=0, lt=1, allow_inf_nan=False)
    seed: int = Field(default=42, ge=0, le=2147483647)


class CheckpointCreate(StrictModel):
    name: Name
    source: Annotated[str, Field(min_length=1, max_length=2000)]
    revision: Annotated[str | None, Field(max_length=200)] = None
    checksum: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None


class ExperimentCreate(StrictModel):
    name: Name
    purpose: Annotated[str, Field(min_length=1, max_length=2000)]
    dataset_id: str
    checkpoint_id: str | None = None
    config: dict = Field(default_factory=dict)
    code_version: Annotated[str, Field(min_length=1, max_length=200)]
    notes: Annotated[str, Field(max_length=10000)] = ""


class Trial(StrictModel):
    task: Name
    success: bool = Field(strict=True)
    steps: int = Field(ge=0, le=10000000, strict=True)
    latency_ms: float = Field(ge=0, le=1e9, allow_inf_nan=False)


class EvaluationBundle(StrictModel):
    source_kind: Literal["imported", "synthetic_demo"]
    task_set: list[Name] = Field(min_length=1, max_length=500)
    seed: int = Field(ge=0, le=2147483647)
    metric_definition: Annotated[str, Field(min_length=1, max_length=2000)]
    trials: list[Trial] = Field(min_length=1, max_length=10000)
    notes: Annotated[str, Field(max_length=10000)] = ""

    @model_validator(mode="after")
    def consistent(self):
        if len(set(self.task_set)) != len(self.task_set):
            raise ValueError("task_set 不能重复")
        if {t.task for t in self.trials} != set(self.task_set):
            raise ValueError("task_set 必须与 trials 中实际任务完全一致")
        if not all(math.isfinite(t.latency_ms) for t in self.trials):
            raise ValueError("latency_ms 必须为有限数值")
        return self
