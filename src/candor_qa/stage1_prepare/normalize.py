"""Format canonicalization so the delta compares meaning, not formatting.
"555-0101" vs "(555) 010-1" and "Aetna|BlueCross" vs "BlueCross|Aetna" are
not changes; without this layer they would flood the delta as false updates.

Rules:
- Nulls are canonicalized: NaN / "" / "N/A" / "None" all become <NA>.
- Normalizers NEVER null-out an unparseable value — the original is kept and
  the failure counted (normalization_failure_rate is a report-only metric).
  Nulling would silently move completeness and validity.

POC note: normalizers are pure per-value functions applied column-wise via
.map(). At production scale, port each to an engine-native expression
(Polars/DuckDB/Spark) — the schema.yaml contract stays the same.
"""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from candor_qa.config import SchemaConfig
from candor_qa.contracts import NormalizationReport

NULL_SENTINELS = {"", "n/a", "na", "null", "none", "nan"}

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}

# Title/suffix tokens stripped from names: "Dr. John Smith" == "SMITH, JOHN, MD"
NAME_NOISE_TOKENS = {
    "dr", "md", "do", "phd", "np", "pa", "rn", "dds", "dpm", "od", "dnp",
    "jr", "sr", "ii", "iii", "iv",
}


def npi_check_digit(base9: str) -> str:
    """Luhn check digit per the CMS NPI spec: computed over '80840' + the
    9-digit base (the card-issuer prefix assigned to US health providers)."""
    total = 0
    for i, ch in enumerate(reversed("80840" + base9)):
        digit = int(ch)
        if i % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def npi_checksum_ok(npi) -> bool:
    npi = str(npi or "")
    return len(npi) == 10 and npi.isdigit() and npi_check_digit(npi[:9]) == npi[9]


def normalize_phone(value: str) -> tuple[str, bool]:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return digits, True
    return value, False


def normalize_zip(value: str) -> tuple[str, bool]:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 9:          # ZIP+4
        digits = digits[:5]
    if 1 <= len(digits) <= 5:
        return digits.zfill(5), True
    return value, False


def normalize_state(value: str) -> tuple[str, bool]:
    state = value.strip().upper()
    if state in US_STATES:
        return state, True
    return value, False


def normalize_name(value: str) -> tuple[str, bool]:
    cleaned = re.sub(r"[^\w\s]", " ", value.lower())
    tokens = [t for t in cleaned.split() if t not in NAME_NOISE_TOKENS]
    if not tokens:
        return value, False
    return " ".join(sorted(tokens)), True   # token order-insensitive


def normalize_text(value: str) -> tuple[str, bool]:
    return " ".join(value.lower().split()), True


def normalize_date(value: str) -> tuple[str, bool]:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return value, False
    return parsed.date().isoformat(), True


def make_list_normalizer(delimiter: str):
    def normalize_list(value: str) -> tuple[str, bool]:
        items = [i.strip().lower() for i in value.split(delimiter)]
        items = [i for i in items if i]
        if not items:
            return value, False
        # Element canonicalization only; ORDER IS PRESERVED so that the
        # set-aware delta can count order-only diffs as suppressed_by_set_compare.
        return delimiter.join(items), True
    return normalize_list


def normalizer_registry(schema: SchemaConfig) -> dict:
    return {
        "phone": normalize_phone,
        "zip": normalize_zip,
        "state": normalize_state,
        "name": normalize_name,
        "text": normalize_text,
        "date": normalize_date,
        "list": make_list_normalizer(schema.list_delimiter),
        "none": lambda v: (v, True),
    }


def canonicalize_nulls(series: pd.Series) -> pd.Series:
    stripped = series.str.strip()
    mask = stripped.str.lower().isin(NULL_SENTINELS)
    return stripped.mask(mask | series.isna())


def normalize_value(value, normalize_kind: str, schema: SchemaConfig):
    """Normalize a single value (used for evidence candidates in Stage 3)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text.lower() in NULL_SENTINELS:
        return None
    normalized, _ok = normalizer_registry(schema)[normalize_kind](text)
    return normalized


def normalize_snapshot(
    df: pd.DataFrame, schema: SchemaConfig
) -> tuple[pd.DataFrame, NormalizationReport]:
    """Returns the normalized frame; raw values are preserved alongside as
    '<col>__raw' so the delta can count changes suppressed by normalization."""
    registry = normalizer_registry(schema)
    out = df.copy()
    failures: dict[str, int] = {}

    for column, spec in schema.fields.items():
        out[f"{column}__raw"] = df[column]
        cleaned = canonicalize_nulls(out[column].astype("string"))
        if spec.normalize == "none":
            out[column] = cleaned
            continue
        func = registry[spec.normalize]
        results = cleaned.map(lambda v: func(v) if pd.notna(v) else (v, True))
        out[column] = results.map(lambda r: r[0]).astype("string")
        failure_count = int((~results.map(lambda r: r[1])).sum())
        if failure_count:
            failures[column] = failure_count

    report = NormalizationReport(failure_counts=failures, row_count=len(df))
    return out, report
