"""Alert evaluation and run-over-run regression detection.

Two comparison planes, both config-driven (metrics.yaml):
- Within-run: baseline -> candidate metric movement. When comparison_mode is
  same_input this IS the pipeline regression signal.
- Vs history: candidate metrics vs a prior persisted run — release over
  release. Default comparand is the most recent run with a MATCHING config
  fingerprint (comparing scores computed under different weights is a lie);
  if only mismatched history exists, the latest run is used with a warning.

Also guards against metric gaming: a sudden large improvement raises a
warning (improvement_anomaly) — +10% completeness overnight is as suspicious
as a drop.
"""
from __future__ import annotations

import json
from pathlib import Path

from candor_qa.config import MetricsConfig
from candor_qa.contracts import Alert, MetricResult, RegressionResult, VolumeSummary


def _metric_map(metrics: list[MetricResult]) -> dict[str, float]:
    """low_confidence metrics (below min_sample) are excluded: a change_quality
    of 0.67 over 3 changes must not trigger alerts."""
    return {
        m.name: m.score
        for m in metrics
        if m.score is not None and m.status != "low_confidence"
    }


def _movement_alerts(
    before: dict[str, float], after: dict[str, float],
    cfg: MetricsConfig, plane: str,
) -> list[Alert]:
    alerts: list[Alert] = []
    for spec in cfg.alerts:
        if spec.scope not in ("both", plane):
            continue
        if spec.metric not in before or spec.metric not in after:
            continue
        old, new = before[spec.metric], after[spec.metric]
        if spec.drop_threshold is not None and (old - new) >= spec.drop_threshold:
            alerts.append(Alert(
                metric=spec.metric, severity=spec.severity, kind="drop",
                message=f"{spec.metric} dropped {old - new:.1%} "
                        f"({old:.1%} -> {new:.1%}) [{plane}]",
                baseline_value=old, candidate_value=new,
            ))
        if spec.rise_threshold is not None and (new - old) >= spec.rise_threshold:
            alerts.append(Alert(
                metric=spec.metric, severity=spec.severity, kind="rise",
                message=f"{spec.metric} rose {new - old:.1%} "
                        f"({old:.1%} -> {new:.1%}) [{plane}]",
                baseline_value=old, candidate_value=new,
            ))

    # improvement anomaly: large gains on weighted higher-is-better metrics
    anomaly = cfg.improvement_anomaly
    for name, spec in cfg.metrics.items():
        if spec.weight <= 0 or spec.direction != "higher_is_better":
            continue
        if name in anomaly.exclude:
            continue
        if name in before and name in after:
            gain = after[name] - before[name]
            if gain >= anomaly.rise_threshold:
                alerts.append(Alert(
                    metric=name, severity=anomaly.severity,
                    kind="improvement_anomaly",
                    message=f"{name} improved {gain:.1%} in one run [{plane}] — "
                            "sudden gains can indicate metric gaming "
                            "(placeholder fills, over-merging); verify before trusting.",
                    baseline_value=before[name], candidate_value=after[name],
                ))
    return alerts


def _volume_alerts(volume: VolumeSummary, cfg: MetricsConfig) -> list[Alert]:
    alerts = []
    for spec in cfg.volume_alerts:
        value = getattr(volume, spec.metric)
        if value > spec.max:
            count = (volume.deleted_count if spec.metric == "deleted_pct"
                     else volume.added_count)
            alerts.append(Alert(
                metric=spec.metric, severity=spec.severity, kind="volume",
                message=f"{spec.metric} = {value:.1%} ({count} records) exceeds "
                        f"{spec.max:.0%} — rate metrics cannot see silent row "
                        "drops; this alert can.",
                candidate_value=value,
            ))
    return alerts


ACCEPTED_DECISIONS = {"APPROVE", "BASELINE_ESTABLISHED"}


def _load_history(history_dir: Path) -> list[dict]:
    runs = []
    for path in sorted(history_dir.glob("*.json")):
        try:
            runs.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return runs


def detect_regression(
    metrics_baseline: list[MetricResult],
    metrics_candidate: list[MetricResult],
    overall_score: float | None,
    volume: VolumeSummary,
    cfg: MetricsConfig,
    history_dir: str | Path,
    mode: str,
    fingerprint: str,
    baseline_run_id: str | None = None,
    downgrade_volume: bool = False,
) -> tuple[RegressionResult, list[Alert]]:
    alerts: list[Alert] = []
    notes: list[str] = []

    before = _metric_map(metrics_baseline)
    after = _metric_map(metrics_candidate)
    alerts += _movement_alerts(before, after, cfg, plane="within_run")
    if downgrade_volume:
        # model_compete: a model finding more or fewer providers is coverage —
        # part of the contest, not a row-drop regression.
        notes.append(
            f"Coverage difference: +{volume.added_count} / -{volume.deleted_count} "
            "records (volume alerts suppressed in model_compete mode)."
        )
    else:
        alerts += _volume_alerts(volume, cfg)

    compared_run_id = None
    config_mismatch = False
    metric_deltas: dict[str, float] = {}

    if mode != "baseline":
        history = _load_history(Path(history_dir))
        prior = None
        if mode == "compare_to":
            prior = next((r for r in history if r.get("run_id") == baseline_run_id), None)
            if prior is None:
                notes.append(f"Run '{baseline_run_id}' not found in history; "
                             "falling back to baseline mode.")
                mode = "baseline"
        else:  # compare
            # Only ACCEPTED runs set the quality bar: a rejected candidate never
            # shipped, and comparing against it would let a chain of
            # slightly-less-bad releases ratchet quality downward.
            accepted = [r for r in history
                        if r.get("release_decision") in ACCEPTED_DECISIONS]
            matching = [r for r in accepted
                        if r.get("config_fingerprint") == fingerprint]
            if matching:
                prior = matching[-1]
            elif accepted:
                prior = accepted[-1]
                config_mismatch = True
                alerts.append(Alert(
                    metric="config_fingerprint", severity="warning",
                    kind="config_mismatch",
                    message="Compared run used a different config (weights/"
                            "thresholds); score comparison is not apples-to-apples.",
                ))
            else:
                notes.append("No evaluation history found; baseline established.")
                mode = "baseline"

        if prior is not None:
            compared_run_id = prior.get("run_id")
            prior_metrics = {
                m["name"]: m["score"]
                for m in prior.get("metrics_candidate", [])
                if m.get("score") is not None
            }
            if prior.get("overall_score") is not None:
                prior_metrics["overall_score"] = prior["overall_score"]
            current = dict(after)
            if overall_score is not None:
                current["overall_score"] = overall_score
            metric_deltas = {
                name: round(current[name] - prior_metrics[name], 4)
                for name in current if name in prior_metrics
            }
            alerts += _movement_alerts(prior_metrics, current, cfg, plane="history")

    result = RegressionResult(
        mode=mode, compared_run_id=compared_run_id,
        config_mismatch=config_mismatch, metric_deltas=metric_deltas, notes=notes,
    )
    return result, alerts
