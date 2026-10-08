"""Claims CSV/XLSX loader — all columns as strings."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

NPI_COLUMNS = ("rendering_npi", "provider_npi", "npi")
NAME_COLUMNS = (("rendering_first_name", "rendering_last_name"), ("provider_name",))


def load_claims(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df.fillna("")
    return df


def claim_npi(row: pd.Series) -> str:
    for col in NPI_COLUMNS:
        if col in row.index and str(row[col]).strip():
            return str(row[col]).strip()
    return ""


def claim_provider_name(row: pd.Series) -> str:
    if "rendering_first_name" in row.index:
        parts = [str(row.get("rendering_first_name", "")).strip(),
                 str(row.get("rendering_last_name", "")).strip()]
        name = " ".join(p for p in parts if p)
        if name:
            return name
    if "provider_name" in row.index:
        return str(row["provider_name"]).strip()
    return ""


def has_service_address(row: pd.Series) -> bool:
    for col in ("service_address_1", "service_city", "service_state", "service_zip"):
        val = str(row.get(col, "")).strip()
        if val and val.lower() not in ("nan", "none"):
            return True
    return False
