"""Key hygiene before the delta merge. Duplicate NPIs would explode a merge
into a cartesian product; null NPIs cannot be joined at all.

Order matters: duplicate_rate is a *quality metric of the pipeline output*,
so it is computed on the RAW snapshot before anything is dropped."""
from __future__ import annotations

import pandas as pd

from candor_qa.config import SchemaConfig
from candor_qa.contracts import DedupeReport


def dedupe_snapshot(df: pd.DataFrame, schema: SchemaConfig) -> tuple[pd.DataFrame, DedupeReport]:
    key = schema.key_field
    raw_count = len(df)

    null_key = df[key].isna()
    keyed = df[~null_key]

    dup_mask = keyed.duplicated(subset=key, keep=False)
    duplicate_npi_count = keyed.loc[dup_mask, key].nunique()

    deduped = keyed.drop_duplicates(subset=key, keep="first").reset_index(drop=True)

    report = DedupeReport(
        raw_count=raw_count,
        duplicate_npi_count=int(duplicate_npi_count),
        dropped_duplicate_rows=len(keyed) - len(deduped),
        null_npi_count=int(null_key.sum()),
    )
    return deduped, report
