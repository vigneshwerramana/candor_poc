"""Typed loaders for the config YAMLs. Behavior lives in config, not code."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

CONFIG_FILES = ("schema.yaml", "source_tiers.yaml", "metrics.yaml",
                "judge_prompts.yaml", "zip_prefixes.yaml")


# ------------------------------------------------------------------ schema.yaml

class LoadSpec(BaseModel):
    dtype: str = "str"
    na_values: list[str]
    keep_default_na: bool = False


class FieldSpec(BaseModel):
    type: str = "string"
    normalize: str = "none"
    compare_as: Literal["scalar", "set"] = "scalar"
    diff: bool = True


class SchemaConfig(BaseModel):
    load: LoadSpec
    list_delimiter: str = "|"
    key_field: str
    fields: dict[str, FieldSpec]

    @property
    def diff_fields(self) -> list[str]:
        return [name for name, spec in self.fields.items() if spec.diff]

    @property
    def columns(self) -> list[str]:
        return list(self.fields)


# ------------------------------------------------------------- source_tiers.yaml

class SourceTiers(BaseModel):
    tiers: dict[str, float]
    default_tier: float = 0.4

    def tier(self, source: str) -> float:
        return self.tiers.get(source, self.default_tier)


# ------------------------------------------------------------------ metrics.yaml

class MetricSpec(BaseModel):
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"
    weight: float = 0.0
    report_only: bool = False
    min_sample: int | None = None


class AlertSpec(BaseModel):
    metric: str
    drop_threshold: float | None = None
    rise_threshold: float | None = None
    severity: Literal["critical", "warning"] = "warning"
    scope: Literal["within_run", "history", "both"] = "both"


class VolumeAlertSpec(BaseModel):
    metric: Literal["added_pct", "deleted_pct"]
    max: float
    severity: Literal["critical", "warning"] = "warning"


class ImprovementAnomalySpec(BaseModel):
    rise_threshold: float = 0.10
    severity: Literal["critical", "warning"] = "warning"
    # Transition metrics measure the changes themselves, not snapshot health;
    # they legitimately swing run-to-run and must not trip the gaming guard.
    exclude: list[str] = ["change_quality", "addition_quality", "deletion_quality"]


class JudgeBands(BaseModel):
    auto_accept: dict
    must_judge: dict
    auto_flag: dict


class JudgePolicy(BaseModel):
    max_judge_calls: int = 100
    cost_per_call: float = 0.002     # rough $/call for the estimated-cost line
    bands: JudgeBands
    budget_overflow: str = "exclude_from_denominator"

    @property
    def auto_accept_min_confidence(self) -> float:
        return float(self.bands.auto_accept.get("min_confidence", 0.95))

    @property
    def auto_flag_max_confidence(self) -> float:
        return float(self.bands.auto_flag.get("max_confidence", 0.50))

    @property
    def always_judge_kinds(self) -> set[str]:
        return set(self.bands.must_judge.get("change_kinds", []))

    @property
    def always_judge_statuses(self) -> set[str]:
        return set(self.bands.must_judge.get("cross_source", []))


class DecisionPolicy(BaseModel):
    review_queue_threshold: int = 20


class MetricsConfig(BaseModel):
    metrics: dict[str, MetricSpec]
    overall_score: dict
    freshness_window_days: int = 180
    alerts: list[AlertSpec] = []
    improvement_anomaly: ImprovementAnomalySpec = ImprovementAnomalySpec()
    volume_alerts: list[VolumeAlertSpec] = []
    judge_policy: JudgePolicy
    decision: DecisionPolicy = DecisionPolicy()
    comparison_mode: Literal["same_input", "temporal", "model_compete"] = "same_input"
    placeholder_values: dict[str, list[str]] = {}


# ------------------------------------------------------------ zip_prefixes.yaml

class ZipPrefixIndex(BaseModel):
    """Reverse lookup: 3-digit ZIP prefix -> states it belongs to.
    Prefixes absent from the reference table are UNKNOWN — the consistency
    metric excludes them rather than counting them as inconsistent."""
    ranges: dict[str, list[list[str]]]

    def states_for(self, prefix: str) -> frozenset[str]:
        return self._lookup.get(prefix, frozenset())

    @property
    def _lookup(self) -> dict[str, frozenset[str]]:
        if not hasattr(self, "_lookup_cache"):
            table: dict[str, set[str]] = {}
            for state, spans in self.ranges.items():
                for start, end in spans:
                    for value in range(int(start), int(end) + 1):
                        table.setdefault(f"{value:03d}", set()).add(state)
            object.__setattr__(self, "_lookup_cache",
                               {k: frozenset(v) for k, v in table.items()})
        return self._lookup_cache


# ---------------------------------------------------------------------- bundle

class Config(BaseModel):
    data_schema: SchemaConfig
    source_tiers: SourceTiers
    metrics: MetricsConfig
    judge_prompts: dict
    zip_prefixes: ZipPrefixIndex
    fingerprint: str


def config_fingerprint(config_dir: Path) -> str:
    """SHA256 over the config files. Two runs are only comparable when their
    fingerprints match — different weights make different scores."""
    digest = hashlib.sha256()
    for name in CONFIG_FILES:
        path = config_dir / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def load_config(config_dir: str | Path) -> Config:
    config_dir = Path(config_dir)

    def read(name: str) -> dict:
        return yaml.safe_load((config_dir / name).read_text())

    return Config(
        data_schema=SchemaConfig(**read("schema.yaml")),
        source_tiers=SourceTiers(**read("source_tiers.yaml")),
        metrics=MetricsConfig(**read("metrics.yaml")),
        judge_prompts=read("judge_prompts.yaml"),
        zip_prefixes=ZipPrefixIndex(**read("zip_prefixes.yaml")),
        fingerprint=config_fingerprint(config_dir),
    )
