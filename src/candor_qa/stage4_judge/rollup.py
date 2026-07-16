"""change_quality rollup and the overall weighted score.

change_quality — the one rule (see ChangeQualityBreakdown in contracts.py):
counts only changes with a definitive disposition. Auto-flagged, uncertain,
and budget-deferred changes are excluded from the denominator and reported
as counts — an unresolved change is unknown, not invalid.

Overall score: weighted average over scoreable metrics. N/A metrics (e.g.
change_quality on a zero-change run) are EXCLUDED and the remaining weights
renormalized — never silently defaulted to 0 or 1, which would distort the
exact number this system exists to produce.
"""
from __future__ import annotations

from candor_qa.config import MetricsConfig
from candor_qa.contracts import (
    AdditionQualityBreakdown,
    ChangeQualityBreakdown,
    JudgedAddition,
    JudgedChange,
    MetricResult,
)


def compute_change_quality(
    judged: list[JudgedChange], cfg: MetricsConfig
) -> tuple[MetricResult, ChangeQualityBreakdown]:
    breakdown = ChangeQualityBreakdown()
    for item in judged:
        if item.route == "auto_accept":
            breakdown.auto_accepted += 1
        elif item.route == "auto_flag":
            breakdown.auto_flagged += 1
        elif item.route == "budget_deferred":
            breakdown.budget_deferred += 1
        elif item.route == "prior_review":
            if item.prior_review_decision == "approved":
                breakdown.review_approved += 1
            else:
                breakdown.review_rejected += 1
        elif item.route == "judged":
            if item.verdict.verdict == "valid_update":
                breakdown.judged_valid += 1
            elif item.verdict.verdict == "regression":
                breakdown.judged_regression += 1
            else:
                breakdown.uncertain += 1

    spec = cfg.metrics["change_quality"]
    min_sample = spec.min_sample or 0
    denominator = breakdown.denominator

    if denominator == 0:
        score, status = None, "n/a"
    else:
        score = breakdown.numerator / denominator
        status = "low_confidence" if denominator < min_sample else "ok"

    metric = MetricResult(
        name="change_quality", score=score, weight=spec.weight,
        direction=spec.direction, status=status, sample_size=denominator,
    )
    return metric, breakdown


def compute_addition_quality(
    judged: list[JudgedAddition], cfg: MetricsConfig
) -> tuple[MetricResult, AdditionQualityBreakdown]:
    breakdown = AdditionQualityBreakdown()
    for item in judged:
        if item.route == "auto_accept":
            breakdown.auto_accepted += 1
        elif item.route == "structural_reject":
            breakdown.structural_rejected += 1
        elif item.route == "auto_flag":
            breakdown.auto_flagged += 1
        elif item.route == "budget_deferred":
            breakdown.budget_deferred += 1
        elif item.route == "prior_review":
            if item.prior_review_decision == "approved":
                breakdown.review_approved += 1
            else:
                breakdown.review_rejected += 1
        elif item.route == "judged":
            if item.verdict.verdict == "valid_addition":
                breakdown.judged_valid += 1
            elif item.verdict.verdict == "invalid_addition":
                breakdown.judged_invalid += 1
            else:
                breakdown.uncertain += 1

    spec = cfg.metrics["addition_quality"]
    min_sample = spec.min_sample or 0
    denominator = breakdown.denominator
    if denominator == 0:
        score, status = None, "n/a"
    else:
        score = breakdown.numerator / denominator
        status = "low_confidence" if denominator < min_sample else "ok"

    metric = MetricResult(
        name="addition_quality", score=score, weight=spec.weight,
        direction=spec.direction, status=status, sample_size=denominator,
    )
    return metric, breakdown


def compute_overall_score(
    metrics: list[MetricResult], cfg: MetricsConfig
) -> tuple[float | None, list[str], list[str]]:
    """Returns (overall_score, metrics_included, metrics_excluded)."""
    scoreable, excluded = [], []
    for metric in metrics:
        if metric.weight > 0 and metric.score is not None:
            scoreable.append(metric)
        else:
            excluded.append(metric.name)

    if not scoreable:
        return None, [], excluded

    total_weight = sum(m.weight for m in scoreable)
    score = sum(m.normalized_score * m.weight for m in scoreable) / total_weight
    return round(score, 4), [m.name for m in scoreable], excluded
