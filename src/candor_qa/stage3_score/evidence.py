"""Evidence access behind a protocol so the POC JSON file can be swapped for
a production provenance store without touching validation logic.

Production path: the ingest pipeline captures per-(npi, field) source
observations into a sidecar table (e.g. provider_provenance.parquet);
QA joins it only on CHANGED pairs — a few million rows, not 70M x fields."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Protocol

from candor_qa.config import SourceTiers
from candor_qa.contracts import SourceCandidate


class EvidenceProvider(Protocol):
    def get_candidates(self, npi: str, field: str) -> list[SourceCandidate]: ...


class JsonEvidenceProvider:
    """POC store: data/source_evidence.json -> {npi: {field: [candidates]}}."""

    def __init__(self, path: str | Path, tiers: SourceTiers):
        self._data = json.loads(Path(path).read_text()) if Path(path).exists() else {}
        self._tiers = tiers

    def get_candidates(self, npi: str, field: str) -> list[SourceCandidate]:
        raw = self._data.get(npi, {}).get(field, [])
        return [
            SourceCandidate(
                source=c["source"],
                value=c.get("value"),
                observed_at=datetime.fromisoformat(c["observed_at"])
                if c.get("observed_at") else None,
                source_tier=c.get("source_tier", self._tiers.tier(c["source"])),
            )
            for c in raw
        ]


class ParquetEvidenceProvider:
    """Production stub — reads a provenance sidecar table. Not implemented in
    the POC; exists to show the swap is an implementation, not a redesign."""

    def __init__(self, path: str | Path, tiers: SourceTiers):
        raise NotImplementedError(
            "Production evidence store. POC uses JsonEvidenceProvider."
        )
