"""Evidence-weighted confidence per change.

confidence = (trust-weighted agreement share) capped by cross-source state:
  verified      no cap    — multiple independent sources corroborate
  conflict      cap 0.70  — sources disagree; the judge decides
  weak_evidence cap 0.60  — one source is not corroboration
  unverifiable  0.50 flat — no evidence either way: maximum uncertainty
"""
from __future__ import annotations

from candor_qa.config import SchemaConfig, SourceTiers
from candor_qa.contracts import (
    CrossSourceStatus,
    FieldChange,
    ScoredChange,
)
from candor_qa.stage3_score.cross_source import classify
from candor_qa.stage3_score.evidence import EvidenceProvider

CONFIDENCE_CAPS = {
    CrossSourceStatus.verified: 1.0,
    CrossSourceStatus.conflict: 0.70,
    CrossSourceStatus.weak_evidence: 0.60,
    CrossSourceStatus.unverifiable: 0.50,
}


def score_changes(
    changes: list[FieldChange],
    evidence: EvidenceProvider,
    tiers: SourceTiers,
    schema: SchemaConfig,
) -> list[ScoredChange]:
    scored = []
    for change in changes:
        candidates = evidence.get_candidates(change.npi, change.field)
        status, agreeing, disagreeing = classify(change, candidates, schema)

        if status is CrossSourceStatus.unverifiable:
            confidence = 0.50
        else:
            total_weight = sum(c.source_tier or tiers.default_tier for c in candidates)
            agree_weight = sum(
                c.source_tier or tiers.default_tier
                for c in candidates if c.source in agreeing
            )
            share = agree_weight / total_weight if total_weight else 0.0
            confidence = min(share, CONFIDENCE_CAPS[status])

        scored.append(ScoredChange(
            change=change,
            cross_source_status=status,
            confidence=round(confidence, 4),
            agreeing_sources=agreeing,
            disagreeing_sources=disagreeing,
            evidence=candidates,
        ))
    return scored
