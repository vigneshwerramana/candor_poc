"""Deletion plausibility — heuristic only in the POC.

Deleting a COMPLETE record is suspicious: real retirements usually follow
staleness (old last_updated, decaying completeness), while accidental
pipeline drops hit healthy records indiscriminately. Full judge-based
deletion scoring (registry deactivation lookups, obituary/closure evidence)
is a production extension."""
from __future__ import annotations

from candor_qa.config import MetricsConfig
from candor_qa.contracts import DeletionVerdict, MetricResult, RecordEvent, RecordEventKind

COMPLETENESS_SUSPICION_THRESHOLD = 0.9


def score_deletions(
    events: list[RecordEvent], cfg: MetricsConfig
) -> tuple[MetricResult, list[DeletionVerdict]]:
    verdicts = []
    for event in events:
        if event.kind is not RecordEventKind.deleted:
            continue
        plausible = event.completeness <= COMPLETENESS_SUSPICION_THRESHOLD
        verdicts.append(DeletionVerdict(
            npi=event.npi,
            was_complete=event.completeness,
            likely_valid_retirement=plausible,
            recommended_action="accept" if plausible else "review",
        ))

    spec = cfg.metrics.get("deletion_quality")
    accepted = sum(1 for v in verdicts if v.recommended_action == "accept")
    metric = MetricResult(
        name="deletion_quality",
        score=(accepted / len(verdicts)) if verdicts else None,
        weight=spec.weight if spec else 0.0,
        direction=spec.direction if spec else "higher_is_better",
        status="ok" if verdicts else "n/a",
        sample_size=len(verdicts),
    )
    return metric, verdicts
