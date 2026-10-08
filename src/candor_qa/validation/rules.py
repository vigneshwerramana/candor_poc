"""DQI validation rules for list 2111 Candor roster."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from candor_qa.contracts import ValidationFinding
from candor_qa.sources import (
    candor_locations,
    candor_practice_states,
    city_state_overlap,
    load_candor_detail,
    load_nppes,
    nppes_locations,
    nppes_primary_taxonomy,
)


def _name(ind: dict[str, Any]) -> str:
    return f"{ind.get('first_name', '')} {ind.get('last_name', '')}".strip()


def _active_license_states(ind: dict[str, Any], ref: date | None = None) -> set[str]:
    ref = ref or date.today()
    states: set[str] = set()
    for lic in ind.get("licenses") or []:
        st = (lic.get("state") or "").upper()[:2]
        if not st:
            continue
        status = lic.get("status")
        end = lic.get("end_date")
        if status is False:
            continue
        if end and str(end)[:10] < ref.isoformat():
            continue
        states.add(st)
    return states


def _primary_specialty(ind: dict[str, Any]) -> str:
    specs = ind.get("specialties") or []
    for s in specs:
        if s.get("is_primary"):
            return s.get("display_name") or s.get("classification") or ""
    return specs[0].get("display_name", "") if specs else ""


def rule_practice_without_license(ind: dict[str, Any]) -> ValidationFinding | None:
    practice = candor_practice_states(ind)
    licensed = _active_license_states(ind)
    missing = practice - licensed
    if not missing:
        return None
    return ValidationFinding(
        dqi_id="DQI-1",
        rule_id="practice_without_license",
        npi=str(ind["npi"]),
        provider_name=_name(ind),
        severity="critical",
        message=f"Practices in {sorted(missing)} with no valid license in Candor.",
        observed=str(sorted(missing)),
        expected="License for each practice state",
        source="candor",
    )


def rule_stale_license(ind: dict[str, Any], ref: date | None = None) -> list[ValidationFinding]:
    ref = ref or date.today()
    findings: list[ValidationFinding] = []
    for lic in ind.get("licenses") or []:
        stale = False
        reason = ""
        if lic.get("status") is False:
            stale = True
            reason = "status=false"
        end = lic.get("end_date")
        if end and str(end)[:10] < ref.isoformat():
            stale = True
            reason = reason or f"end_date={end}"
        if stale:
            findings.append(ValidationFinding(
                dqi_id="DQI-2",
                rule_id="stale_license_status",
                npi=str(ind["npi"]),
                provider_name=_name(ind),
                severity="warning",
                message=f"License {lic.get('code')} ({lic.get('state')}) appears stale ({reason}).",
                observed=str(lic),
                expected="Active license per state board",
                source="candor",
            ))
    return findings


def rule_nppes_location_mismatch(ind: dict[str, Any], nppes_dir: str) -> ValidationFinding | None:
    nppes = load_nppes(nppes_dir, str(ind["npi"]))
    if not nppes:
        return None
    candor_cs = {loc.city_state for loc in candor_locations(ind) if loc.city_state[0]}
    nppes_cs = {loc.city_state for loc in nppes_locations(nppes) if loc.city_state[0]}
    if not candor_cs or not nppes_cs:
        return None
    if candor_cs & nppes_cs:
        return None
    return ValidationFinding(
        dqi_id="DQI-3",
        rule_id="nppes_location_mismatch",
        npi=str(ind["npi"]),
        provider_name=_name(ind),
        severity="critical",
        message="No (city, state) overlap between Candor facilities and NPPES.",
        observed=f"Candor: {sorted(candor_cs)}",
        expected=f"Overlap with NPPES: {sorted(nppes_cs)}",
        source="nppes",
    )


def rule_specialty_missing(ind: dict[str, Any], nppes_dir: str) -> ValidationFinding | None:
    specialty = _primary_specialty(ind)
    nppes = load_nppes(nppes_dir, str(ind["npi"]))
    nppes_tax = nppes_primary_taxonomy(nppes)
    if specialty:
        return None
    if not nppes_tax:
        return None
    return ValidationFinding(
        dqi_id="DQI-4",
        rule_id="specialty_missing_or_mismatch",
        npi=str(ind["npi"]),
        provider_name=_name(ind),
        severity="warning",
        message="Structured specialty empty but NPPES lists a primary taxonomy.",
        observed="(empty)",
        expected=nppes_tax,
        source="nppes",
    )


def rule_roster_geography(ind: dict[str, Any], rules_cfg: dict) -> ValidationFinding | None:
    roster = rules_cfg.get("roster") or {}
    needle = roster.get("list_name_contains", "Columbus")
    exp_city = roster.get("expected_city", "Columbus")
    exp_state = roster.get("expected_state", "OH")
    lists = ind.get("lists") or []
    if not any(needle.lower() in (lst.get("name") or "").lower() for lst in lists):
        return None
    for loc in candor_locations(ind):
        if loc.city.lower() == exp_city.lower() and loc.state == exp_state:
            return None
    return ValidationFinding(
        dqi_id="DQI-5",
        rule_id="roster_geography_mismatch",
        npi=str(ind["npi"]),
        provider_name=_name(ind),
        severity="warning",
        message=f"Provider in '{needle}' roster but no facility in {exp_city}, {exp_state}.",
        observed=str([loc.label() for loc in candor_locations(ind)]),
        expected=f"Facility in {exp_city}, {exp_state}",
        source="candor",
    )


def rule_facility_quality(ind: dict[str, Any], rules_cfg: dict) -> list[ValidationFinding]:
    patterns = [p.lower() for p in (rules_cfg.get("placeholder_address_patterns") or [])]
    findings: list[ValidationFinding] = []
    for fac in ind.get("facilities") or []:
        line1 = (fac.get("line1") or "").lower()
        name = fac.get("name")
        lat, lng = fac.get("lat"), fac.get("long")
        issues: list[str] = []
        if any(p in line1 for p in patterns):
            issues.append("PO Box or placeholder address")
        if lat == 0 and lng == 0:
            issues.append("(0,0) coordinates")
        if not name:
            issues.append("missing facility name")
        if "call for appointment" in line1:
            issues.append("placeholder address pattern")
        if issues:
            findings.append(ValidationFinding(
                dqi_id="DQI-6",
                rule_id="facility_quality",
                npi=str(ind["npi"]),
                provider_name=_name(ind),
                severity="warning",
                message=f"Facility quality: {', '.join(issues)}.",
                observed=fac.get("formattedAddress") or line1,
                expected="Valid clinical facility record",
                source="candor",
            ))
    return findings


def rule_missing_credential(ind: dict[str, Any], rules_cfg: dict) -> ValidationFinding | None:
    prefixes = rules_cfg.get("allied_health_taxonomy_prefixes") or []
    specs = ind.get("specialties") or []
    if not specs:
        return None
    codes = [s.get("code") or "" for s in specs]
    if not any(any(code.startswith(p) for p in prefixes) for code in codes):
        return None
    if ind.get("licenses"):
        return None
    return ValidationFinding(
        dqi_id="DQI-7",
        rule_id="missing_credential_by_taxonomy",
        npi=str(ind["npi"]),
        provider_name=_name(ind),
        severity="info",
        message="Allied-health provider with no license/credential in Candor (may be expected).",
        observed="no licenses",
        expected="Profession-specific credential if required",
        source="candor",
    )


def rule_recently_moved(ind: dict[str, Any], details_dir: str | None) -> ValidationFinding | None:
    if not details_dir:
        return None
    detail = load_candor_detail(details_dir, str(ind["npi"]))
    if not detail or not detail.get("recently_moved"):
        return None
    return ValidationFinding(
        dqi_id="DQI-8",
        rule_id="recently_moved_unverified",
        npi=str(ind["npi"]),
        provider_name=_name(ind),
        severity="info",
        message="recently_moved=true with no visible re-verification indicator.",
        observed="recently_moved=true",
        expected="Address re-verification status",
        source="candor_detail",
    )


def rule_attribute_coverage(individuals: list[dict[str, Any]]) -> list[ValidationFinding]:
    n = len(individuals) or 1
    lgbtq_null = sum(1 for i in individuals if i.get("is_lgbtq_friendly") is None)
    ages_empty = sum(1 for i in individuals if not i.get("ages_seen"))
    findings: list[ValidationFinding] = []
    if lgbtq_null == n:
        findings.append(ValidationFinding(
            dqi_id="DQI-9",
            rule_id="attribute_coverage",
            npi="*",
            provider_name=None,
            severity="info",
            message=f"is_lgbtq_friendly null for all {n} providers in roster.",
            observed=f"{lgbtq_null}/{n}",
            expected="Populated or documented as N/A",
            source="candor_search",
        ))
    if ages_empty >= n * 0.5:
        findings.append(ValidationFinding(
            dqi_id="DQI-9",
            rule_id="attribute_coverage",
            npi="*",
            provider_name=None,
            severity="info",
            message=f"ages_seen empty for {ages_empty}/{n} providers.",
            observed=f"{ages_empty}/{n}",
            expected="Higher coverage",
            source="candor_search",
        ))
    findings.append(ValidationFinding(
        dqi_id="DQI-9",
        rule_id="api_field_naming",
        npi="*",
        provider_name=None,
        severity="info",
        message="Search API uses is_lgbtq_friendly; detail API uses lgbtq_friendly.",
        observed="is_lgbtq_friendly vs lgbtq_friendly",
        expected="Consistent field name across APIs",
        source="candor",
    ))
    return findings


def run_all_rules(
    individuals: list[dict[str, Any]],
    *,
    nppes_dir: str,
    details_dir: str | None = None,
    rules_path: str = "config/rules.yaml",
    reference_date: date | None = None,
) -> list[ValidationFinding]:
    rules_cfg = yaml.safe_load(Path(rules_path).read_text())
    findings: list[ValidationFinding] = []
    for ind in individuals:
        r = rule_practice_without_license(ind)
        if r:
            findings.append(r)
        findings.extend(rule_stale_license(ind, reference_date))
        r = rule_nppes_location_mismatch(ind, nppes_dir)
        if r:
            findings.append(r)
        r = rule_specialty_missing(ind, nppes_dir)
        if r:
            findings.append(r)
        r = rule_roster_geography(ind, rules_cfg)
        if r:
            findings.append(r)
        findings.extend(rule_facility_quality(ind, rules_cfg))
        r = rule_missing_credential(ind, rules_cfg)
        if r:
            findings.append(r)
        r = rule_recently_moved(ind, details_dir)
        if r:
            findings.append(r)
    findings.extend(rule_attribute_coverage(individuals))
    return findings
