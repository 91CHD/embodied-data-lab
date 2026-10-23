"""A typed extension boundary. These commands are NOT executed on this local CPU workbench.

CLI options checked against huggingface/lerobot v0.4.3 scripts and config classes.
Keep the argv list separate from a shell: user strings never become shell syntax.
"""
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .schemas import StrictModel

PINNED_LEROBOT_VERSION = "0.4.3"


class TrainParams(StrictModel):
    repo_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    steps: int = Field(default=1000, ge=1, le=1000000)
    seed: int = Field(default=42, ge=0, le=2147483647)
    batch_size: int = Field(default=8, ge=1, le=256)


class EvaluateParams(StrictModel):
    policy_path: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    task: Literal["libero_spatial", "libero_object", "libero_goal", "libero_10"] = "libero_spatial"
    episodes: int = Field(default=10, ge=1, le=1000)
    seed: int = Field(default=42, ge=0, le=2147483647)


def build_train_command(params: TrainParams, output: Path) -> list[str]:
    return ["lerobot-train", f"--dataset.repo_id={params.repo_id}", "--policy.type=smolvla",
            "--policy.device=cuda", "--policy.push_to_hub=false", f"--output_dir={output}",
            f"--steps={params.steps}", f"--batch_size={params.batch_size}", f"--seed={params.seed}",
            "--wandb.enable=false"]


def build_evaluate_command(params: EvaluateParams, output: Path) -> list[str]:
    return ["lerobot-eval", f"--policy.path={params.policy_path}", "--env.type=libero",
            f"--env.task={params.task}", "--eval.batch_size=1", f"--eval.n_episodes={params.episodes}",
            f"--seed={params.seed}", f"--output_dir={output}"]


def capabilities():
    try:
        installed = version("lerobot")
    except PackageNotFoundError:
        installed = None
    reason = "Linux/GPU 训练 worker 未配置；本机只执行数据任务，可导入外部真实评测记录。"
    return {"cpu": True, "platform": platform.system(), "lerobot_version": installed,
            "target_lerobot_version": PINNED_LEROBOT_VERSION,
            "train": {"available": False, "reason": reason,
                      "command_template": "lerobot-train --dataset.repo_id=<repo> --policy.type=smolvla --policy.push_to_hub=false --output_dir=<attempt>"},
            "evaluate": {"available": False, "reason": reason,
                         "command_template": "lerobot-eval --policy.path=<checkpoint> --env.type=libero --env.task=libero_spatial"}}
