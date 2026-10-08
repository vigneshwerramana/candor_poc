"""Shared data sources for offline Candor + NPPES replay."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_STREET_ABBREV = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "street": "st", "avenue": "ave", "boulevard": "blvd",
    "drive": "dr", "road": "rd", "lane": "ln", "court": "ct",
    "place": "pl", "suite": "ste", "floor": "fl",
}


def _norm(s: str | None) -> str:
    if not s or str(s).lower() in ("nan", "none", ""):
        return ""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _norm_street(s: str) -> str:
    raw = _norm(s)
    for full, short in _STREET_ABBREV.items():
        raw = raw.replace(full, short)
    return raw


def normalize_zip(z: str | None) -> str:
    if not z or str(z).lower() == "nan":
        return ""
    digits = re.sub(r"\D", "", str(z))
    if not digits:
        return ""
    if len(digits) <= 5:
        return digits.zfill(5)
    return digits[:5]


class LocationRef:
    def __init__(self, line1: str, city: str, state: str, zip_code: str = "", source: str = ""):
        self.line1 = line1 or ""
        self.city = city or ""
        self.state = (state or "").upper()[:2]
        self.zip_code = normalize_zip(zip_code)
        self.source = source

    @property
    def city_state(self) -> tuple[str, str]:
        return (_norm(self.city), self.state)

    def label(self) -> str:
        parts = [p for p in [self.line1, self.city, self.state, self.zip_code] if p]
        return ", ".join(parts)


def load_nppes(nppes_dir: str | Path, npi: str) -> dict[str, Any] | None:
    path = Path(nppes_dir) / f"{npi}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def nppes_locations(data: dict[str, Any] | None) -> list[LocationRef]:
    if not data:
        return []
    results = data.get("results") or []
    if not results:
        return []
    provider = results[0]
    locs: list[LocationRef] = []
    for addr in provider.get("addresses") or []:
        if addr.get("address_purpose") == "LOCATION":
            locs.append(LocationRef(
                addr.get("address_1", ""),
                addr.get("city", ""),
                addr.get("state", ""),
                addr.get("postal_code", ""),
                "nppes",
            ))
    for addr in provider.get("practiceLocations") or []:
        locs.append(LocationRef(
            addr.get("address_1", ""),
            addr.get("city", ""),
            addr.get("state", ""),
            addr.get("postal_code", ""),
            "nppes_practice",
        ))
    return locs


def nppes_primary_taxonomy(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    results = data.get("results") or []
    if not results:
        return None
    for tax in results[0].get("taxonomies") or []:
        if tax.get("primary"):
            return tax.get("desc") or tax.get("code")
    taxes = results[0].get("taxonomies") or []
    return taxes[0].get("desc") if taxes else None


def nppes_name(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    results = data.get("results") or []
    if not results:
        return ""
    basic = results[0].get("basic") or {}
    parts = [basic.get("first_name"), basic.get("last_name")]
    return " ".join(p for p in parts if p)


def load_candor_search(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    return data.get("individuals") or []


def load_candor_detail(details_dir: str | Path, npi: str) -> dict[str, Any] | None:
    path = Path(details_dir) / f"{npi}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def candor_locations(individual: dict[str, Any]) -> list[LocationRef]:
    locs: list[LocationRef] = []
    for fac in individual.get("facilities") or []:
        locs.append(LocationRef(
            fac.get("line1") or "",
            fac.get("city") or "",
            fac.get("state") or "",
            fac.get("zip") or "",
            "candor",
        ))
    return locs


def candor_practice_states(individual: dict[str, Any]) -> set[str]:
    return {loc.state for loc in candor_locations(individual) if loc.state}


def city_state_overlap(claim_cs: tuple[str, str], refs: list[LocationRef]) -> bool:
    for ref in refs:
        if ref.city_state == claim_cs and claim_cs[0]:
            return True
    return False


def address_matches_claim(
    line1: str, city: str, state: str, zip_code: str,
    refs: list[LocationRef],
    *,
    candor_refs: list[LocationRef] | None = None,
) -> bool:
    """Match claim address. When Candor locations exist, they take precedence."""
    check_refs = candor_refs if candor_refs else refs
    if not city and not state and not line1:
        return False
    claim_cs = (_norm(city), (state or "").upper()[:2])
    if not claim_cs[0] and not claim_cs[1]:
        return False
    claim_street = _norm_street(line1)
    for ref in check_refs:
        if ref.city_state != claim_cs:
            continue
        if not claim_street:
            return True
        ref_street = _norm_street(ref.line1)
        if not ref_street:
            continue
        if claim_street in ref_street or ref_street in claim_street:
            return True
        if len(claim_street) >= 8 and len(ref_street) >= 8:
            if claim_street[:12] == ref_street[:12]:
                return True
    return False
