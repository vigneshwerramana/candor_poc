"""Evidence-weighted scoring for ADDED records.

An addition has no old value to compare, so it is scored on:
1. Structure — bad NPI checksum, placeholder phone, or a near-empty record is
   provably junk (structural failure -> confidence 0, rejected downstream
   without spending evidence lookups or judge budget).
2. Evidence — each key field with a value is checked against the evidence
   store like a field change. Status = worst state among evidenced fields;
   confidence = trust-weighted mean over evidenced fields, capped by status.
   No evidence on any field -> unverifiable (0.5), exactly like changes.
"""
from __future__ import annotations

from candor_qa.config import SchemaConfig, SourceTiers
from candor_qa.contracts import (
    CrossSourceStatus,
    RecordEvent,
    RecordEventKind,
    ScoredAddition,
    SourceCandidate,
)
from candor_qa.stage1_prepare.normalize import normalize_value
from candor_qa.stage3_score.confidence import CONFIDENCE_CAPS
from candor_qa.stage3_score.evidence import EvidenceProvider

EVIDENCE_FIELDS = ["phone", "address", "specialty"]
_SEVERITY = {
    CrossSourceStatus.verified: 0,
    CrossSourceStatus.weak_evidence: 1,
    CrossSourceStatus.conflict: 2,
}


def _field_confidence(value, candidates: list[SourceCandidate],
                      normalize_kind: str, schema: SchemaConfig,
                      tiers: SourceTiers) -> tuple[CrossSourceStatus, float]:
    agree = disagree = 0.0
    for candidate in candidates:
        weight = candidate.source_tier or tiers.default_tier
        if normalize_value(candidate.value, normalize_kind, schema) == value:
            agree += weight
        else:
            disagree += weight
    if len(candidates) == 1:
        status = CrossSourceStatus.weak_evidence
    elif disagree == 0:
        status = CrossSourceStatus.verified
    else:
        status = CrossSourceStatus.conflict
    share = agree / (agree + disagree) if (agree + disagree) else 0.0
    return status, min(share, CONFIDENCE_CAPS[status])


def score_additions(
    events: list[RecordEvent],
    evidence: EvidenceProvider,
    tiers: SourceTiers,
    schema: SchemaConfig,
) -> list[ScoredAddition]:
    scored = []
    for event in events:
        if event.kind is not RecordEventKind.added:
            continue

        failures = [flag for flag, ok in event.validity_flags.items()
                    if not ok and (flag != "phone_ok" or event.record.get("phone"))]
        # a null phone is incompleteness, not a structural lie — only a present
        # but junk phone fails phone_ok structurally

        if failures:
            scored.append(ScoredAddition(
                event=event, cross_source_status=CrossSourceStatus.unverifiable,
                confidence=0.0, structural_failures=failures,
            ))
            continue

        statuses: list[CrossSourceStatus] = []
        confidences: list[float] = []
        checked: list[str] = []
        all_candidates: list[SourceCandidate] = []
        for field in EVIDENCE_FIELDS:
            value = event.record.get(field)
            if value is None:
                continue
            candidates = evidence.get_candidates(event.npi, field)
            if not candidates:
                continue
            status, confidence = _field_confidence(
                value, candidates, schema.fields[field].normalize, schema, tiers)
            statuses.append(status)
            confidences.append(confidence)
            checked.append(field)
            all_candidates.extend(candidates)

        if not checked:
            scored.append(ScoredAddition(
                event=event, cross_source_status=CrossSourceStatus.unverifiable,
                confidence=0.5, checked_fields=[],
            ))
            continue

        worst = max(statuses, key=lambda s: _SEVERITY[s])
        confidence = min(sum(confidences) / len(confidences),
                         CONFIDENCE_CAPS[worst])
        scored.append(ScoredAddition(
            event=event, cross_source_status=worst,
            confidence=round(confidence, 4), checked_fields=checked,
            evidence=all_candidates,
        ))
    return scored
