"""Model competition summary — pure assembly over results the pipeline
already produced. No new judging happens here.

When baseline = model A's output and candidate = model B's output on the
same frozen input, the change_quality breakdown over their disagreements IS
the head-to-head verdict: every definitive disposition says whose value the
evidence (or judge) sided with. field_win_rate simply reads it as a contest.

Agreements between the models are uninformative (both right or both wrong);
the entire signal lives in the disagreement set — hence the min-sample guard
before declaring a winner."""
from __future__ import annotations

from candor_qa.config import MetricsConfig
from candor_qa.contracts import (
    ChangeQualityBreakdown,
    CompetitionSummary,
    MetricResult,
    VolumeSummary,
)

MIN_DISAGREEMENTS = 20
WIN_THRESHOLD = 0.55   # candidate_wins above; baseline_wins below (1 - this)

# change_quality/addition_quality have no baseline counterpart, so the
# symmetric score compares snapshot health only — apples to apples.
SNAPSHOT_ONLY = {"completeness", "phone_validity", "duplicate_rate", "freshness"}


def snapshot_only_score(metrics: list[MetricResult]) -> float | None:
    scoreable = [m for m in metrics
                 if m.name in SNAPSHOT_ONLY and m.score is not None]
    if not scoreable:
        return None
    total = sum(m.weight for m in scoreable)
    return sum(m.normalized_score * m.weight for m in scoreable) / total


def build_competition_summary(
    baseline_label: str,
    candidate_label: str,
    breakdown: ChangeQualityBreakdown,
    metrics_baseline: list[MetricResult],
    metrics_candidate: list[MetricResult],
    addition_quality: MetricResult,
    volume: VolumeSummary,
    cfg: MetricsConfig,
) -> CompetitionSummary:
    disagreements = breakdown.denominator
    win_rate = (breakdown.numerator / disagreements
                if disagreements >= MIN_DISAGREEMENTS else None)

    score_a = snapshot_only_score(metrics_baseline) or 0.0
    score_b = snapshot_only_score(metrics_candidate) or 0.0
    delta = round(score_b - score_a, 4)

    if win_rate is None:
        recommendation = "inconclusive"
    elif win_rate >= WIN_THRESHOLD and delta >= 0:
        recommendation = "candidate_wins"
    elif win_rate <= (1 - WIN_THRESHOLD) and delta <= 0:
        recommendation = "baseline_wins"
    else:
        recommendation = "inconclusive"

    return CompetitionSummary(
        baseline_label=baseline_label,
        candidate_label=candidate_label,
        shared_npi_count=volume.unchanged_count + volume.updated_count,
        disagreement_count=disagreements,
        field_win_rate=round(win_rate, 4) if win_rate is not None else None,
        snapshot_score_delta=delta,
        addition_quality=addition_quality.score,
        coverage_delta=volume.candidate_count - volume.baseline_count,
        regressions_in_candidate=breakdown.judged_regression,
        recommendation=recommendation,
    )


def prod_guidance(summary: CompetitionSummary) -> tuple[str | None, list[str]]:
    """Decision-first copy for demos: translate a compete result into a PROD action."""
    if summary.recommendation == "candidate_wins":
        rec = f"Deploy {summary.candidate_label} to PROD"
    elif summary.recommendation == "baseline_wins":
        rec = f"Keep {summary.baseline_label} in PROD"
    else:
        return None, []

    rationale: list[str] = []
    if summary.field_win_rate is not None:
        rationale.append(
            f"Candidate won {summary.field_win_rate:.0%} of evidence/judge-decided disagreements."
        )
    rationale.append(f"Snapshot health Δ: {summary.snapshot_score_delta:+.1%}.")
    rationale.append(f"Extra coverage: {summary.coverage_delta:+d} records.")
    if summary.addition_quality is not None:
        rationale.append(f"Addition quality: {summary.addition_quality:.0%}.")
    return rec, rationale[:3]
