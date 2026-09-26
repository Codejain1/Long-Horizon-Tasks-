"""Harness config (``bench/config.yaml``). ``real_runs`` is the single switch for real runs."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "bench" / "config.yaml"


class Prices(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


class ModelCfg(BaseModel):
    name: str
    prices: Prices


class Limits(BaseModel):
    step_limit: int = 50
    cost_limit_usd: float = 1.0
    command_timeout_s: int = 60


class DatasetCfg(BaseModel):
    name: str
    split: str = "test"


class SelectionCfg(BaseModel):
    seed: int
    n_tasks: int = 50
    smoke_n: int = 10
    take_all: list[str] = Field(default_factory=list)
    weights: dict[str, float]
    task_list: str


class ExecutionCfg(BaseModel):
    environment: str = "auto"  # auto | docker | local
    repeats: int = 3


class EvaluationCfg(BaseModel):
    subset: str = "swe-bench_verified"
    split: str = "test"


class BenchConfig(BaseModel):
    real_runs: bool = False
    model: ModelCfg
    limits: Limits = Field(default_factory=Limits)
    dataset: DatasetCfg
    selection: SelectionCfg
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    evaluation: EvaluationCfg = Field(default_factory=EvaluationCfg)
    output_dir: str = "bench/runs"

    @property
    def dry_run(self) -> bool:
        return not self.real_runs

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else REPO_ROOT / p

    def check_real_run_ready(self) -> None:
        """Real runs need credentials; fail early and clearly instead of mid-run."""
        missing = [k for k in ("ANTHROPIC_API_KEY", "SWEBENCH_API_KEY") if not os.environ.get(k)]
        if missing:
            raise SystemExit(f"real_runs is true but {', '.join(missing)} is not set.")


def load_config(path: str | Path | None = None) -> BenchConfig:
    return BenchConfig.model_validate(yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text()))
