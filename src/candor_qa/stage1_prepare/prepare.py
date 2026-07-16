"""Stage 1 entry point: one function, one typed result."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from candor_qa.config import SchemaConfig
from candor_qa.contracts import DedupeReport, NormalizationReport
from candor_qa.stage1_prepare.dedupe import dedupe_snapshot
from candor_qa.stage1_prepare.loader import load_snapshot
from candor_qa.stage1_prepare.normalize import normalize_snapshot


@dataclass
class PreparedSnapshot:
    frame: pd.DataFrame              # deduped + normalized; '<col>__raw' preserved
    dedupe_report: DedupeReport
    normalization_report: NormalizationReport


def prepare_snapshot(path: str | Path, schema: SchemaConfig) -> PreparedSnapshot:
    raw = load_snapshot(path, schema)
    deduped, dedupe_report = dedupe_snapshot(raw, schema)
    normalized, norm_report = normalize_snapshot(deduped, schema)
    return PreparedSnapshot(
        frame=normalized,
        dedupe_report=dedupe_report,
        normalization_report=norm_report,
    )
