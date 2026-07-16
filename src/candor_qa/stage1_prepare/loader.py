"""Schema-driven CSV loading. Pandas type inference is never trusted:
it strips leading-zero zips and turns NPI columns into floats, which would
corrupt the data before evaluation starts (dedupe and the delta merge join
on the raw NPI string)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from candor_qa.config import SchemaConfig


def load_snapshot(path: str | Path, schema: SchemaConfig) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        dtype=str,
        na_values=schema.load.na_values,
        keep_default_na=schema.load.keep_default_na,
    )
    missing = [c for c in schema.columns if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing schema columns {missing}")
    return df[schema.columns]
