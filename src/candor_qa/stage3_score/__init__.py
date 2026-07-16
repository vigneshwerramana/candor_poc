"""Stage 3 — SCORE: evidence-weighted confidence for every change."""
from candor_qa.stage3_score.confidence import score_changes
from candor_qa.stage3_score.evidence import (
    EvidenceProvider,
    JsonEvidenceProvider,
    ParquetEvidenceProvider,
)

__all__ = [
    "score_changes",
    "EvidenceProvider",
    "JsonEvidenceProvider",
    "ParquetEvidenceProvider",
]
