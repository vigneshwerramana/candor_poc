"""Cross-source validation: does independent evidence agree with the new value?

Four states — the no-evidence case is deliberately its own state:
in production MOST real changes have zero corroborating evidence (evidence
lags reality). Treating no-evidence as a conflict would auto-reject nearly
everything; treating it as support would defeat validation entirely.
"""
from __future__ import annotations

from candor_qa.config import SchemaConfig
from candor_qa.contracts import CrossSourceStatus, FieldChange, SourceCandidate
from candor_qa.stage1_prepare.normalize import normalize_value


def classify(
    change: FieldChange,
    candidates: list[SourceCandidate],
    schema: SchemaConfig,
) -> tuple[CrossSourceStatus, list[str], list[str]]:
    """Returns (status, agreeing_sources, disagreeing_sources).

    Evidence values are normalized with the SAME normalizer as the field so
    that '(617) 555-0101' from a website agrees with '6175550101'.
    """
    if not candidates:
        return CrossSourceStatus.unverifiable, [], []

    normalize_kind = schema.fields[change.field].normalize
    new_value = change.new_value

    agreeing, disagreeing = [], []
    for candidate in candidates:
        value = normalize_value(candidate.value, normalize_kind, schema)
        if value == new_value:
            agreeing.append(candidate.source)
        else:
            disagreeing.append(candidate.source)

    if len(candidates) == 1:
        return CrossSourceStatus.weak_evidence, agreeing, disagreeing
    if agreeing and not disagreeing:
        return CrossSourceStatus.verified, agreeing, disagreeing
    return CrossSourceStatus.conflict, agreeing, disagreeing
