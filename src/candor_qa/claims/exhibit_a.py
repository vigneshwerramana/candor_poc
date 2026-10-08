"""Exhibit A network mapping lookup."""
from __future__ import annotations

from pathlib import Path

import yaml

UNMAPPED = "UNMAPPED"


def load_exhibit_a(config_path: str | Path = "config/exhibit_a.yaml") -> dict[tuple[str, str], str]:
    data = yaml.safe_load(Path(config_path).read_text())
    table: dict[tuple[str, str], str] = {}
    for entry in data.get("mappings") or []:
        key = (str(entry["contract"]).strip().lower(),
               str(entry["agreement_type"]).strip().lower())
        table[key] = entry["network"]
    return table


def lookup_network(contract: str, agreement_type: str,
                   table: dict[tuple[str, str], str] | None = None) -> str:
    if table is None:
        table = load_exhibit_a()
    key = (str(contract or "").strip().lower(),
           str(agreement_type or "").strip().lower())
    return table.get(key, UNMAPPED)
