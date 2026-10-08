"""Run claims-to-network checks."""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from candor_qa.claims.diagnose import diagnose
from candor_qa.claims.exhibit_a import UNMAPPED, load_exhibit_a, lookup_network
from candor_qa.claims.loader import (
    claim_npi,
    claim_provider_name,
    has_service_address,
    load_claims,
)
from candor_qa.contracts import ClaimCheckResult, ClaimsCacheCoverage, ClaimsReport
from candor_qa.sources import (
    address_matches_claim,
    candor_locations,
    load_candor_detail,
    load_nppes,
    nppes_locations,
)

ASSIGNMENT_NPIS = {
    "1407146699",  # Q1 Sullivan
    "1093191249",  # Q2 Lamb
    "1407275217",  # Q3 Uddin
    "1891140760",  # Q4 Porter
    "1114993748",  # Q5 Thai
    "1558387852",  # Q6 Galatz
}


@dataclass
class ClaimsRunConfig:
    claims_path: str
    nppes_dir: str = "data/candor_live/nppes"
    candor_details_dir: str | None = "data/candor_live/details"
    exhibit_a_path: str = "config/exhibit_a.yaml"
    output_dir: str = "data/evaluation_history/claims"
    persist: bool = True


def _make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


def _reference_locations(npi: str, cfg: ClaimsRunConfig) -> tuple[list, list | None, bool]:
    nppes_data = load_nppes(cfg.nppes_dir, npi)
    nppes_refs = nppes_locations(nppes_data)
    candor_refs: list | None = None
    has_candor = False
    if cfg.candor_details_dir:
        detail = load_candor_detail(cfg.candor_details_dir, npi)
        if detail:
            candor_refs = candor_locations(detail)
            has_candor = True
    all_refs = nppes_refs + (candor_refs or [])
    reference_available = bool(nppes_data) or has_candor
    return all_refs, candor_refs, reference_available


def _compute_cache_coverage(
    df: pd.DataFrame,
    cfg: ClaimsRunConfig,
    results: list[ClaimCheckResult],
) -> ClaimsCacheCoverage:
    npis = sorted({claim_npi(row) for _, row in df.iterrows() if claim_npi(row)})
    nppes_dir = Path(cfg.nppes_dir)
    details_dir = Path(cfg.candor_details_dir) if cfg.candor_details_dir else None
    nppes_cached = sum(1 for n in npis if (nppes_dir / f"{n}.json").exists())
    candor_cached = 0
    if details_dir and details_dir.exists():
        candor_cached = sum(1 for n in npis if (details_dir / f"{n}.json").exists())
    with_ref = sum(1 for r in results if r.reference_available)
    return ClaimsCacheCoverage(
        unique_npis=len(npis),
        nppes_cached=nppes_cached,
        candor_detail_cached=candor_cached,
        claims_with_reference=with_ref,
    )


def check_claim_row(
    row: pd.Series,
    idx: int,
    table: dict,
    cfg: ClaimsRunConfig,
) -> ClaimCheckResult:
    npi = claim_npi(row)
    contract = str(row.get("contract", "")).strip()
    agreement_type = str(row.get("agreement_type", "")).strip()
    mapped = lookup_network(contract, agreement_type, table)
    reference_available = False

    if not has_service_address(row):
        address_match = "NO_SERVICE_ADDRESS"
    else:
        refs, candor_refs, reference_available = (
            _reference_locations(npi, cfg) if npi else ([], None, False)
        )
        if not reference_available:
            address_match = "NO_REFERENCE"
        else:
            matched = address_matches_claim(
                str(row.get("service_address_1", "")),
                str(row.get("service_city", "")),
                str(row.get("service_state", "")),
                str(row.get("service_zip", "")),
                refs,
                candor_refs=candor_refs,
            )
            address_match = "MATCH" if matched else "NO_MATCH"

    diagnosis, explanation, flagged = diagnose(
        mapped,
        address_match,
        contract,
        agreement_type,
        assignment_npi=npi in ASSIGNMENT_NPIS,
    )
    if diagnosis == "ok" and mapped == UNMAPPED:
        flagged = True

    claim_id = f"{npi or 'unknown'}-{idx}"
    service_date = str(row.get("service_date", "")).strip()
    if service_date:
        claim_id = f"{npi or 'unknown'}-{service_date[:10]}-{idx}"

    return ClaimCheckResult(
        claim_id=claim_id,
        npi=npi,
        provider_name=claim_provider_name(row) or None,
        contract=contract,
        agreement_type=agreement_type,
        service_city=str(row.get("service_city", "")).strip() or None,
        service_state=str(row.get("service_state", "")).strip() or None,
        mapped_network=mapped,
        address_match=address_match,
        diagnosis=diagnosis,
        explanation=explanation,
        flagged=flagged,
        reference_available=reference_available,
    )


def run_claims_check(cfg: ClaimsRunConfig) -> ClaimsReport:
    df = load_claims(cfg.claims_path)
    table = load_exhibit_a(cfg.exhibit_a_path)
    results = [check_claim_row(row, i, table, cfg) for i, row in df.iterrows()]

    unmapped = sum(1 for r in results if r.mapped_network == UNMAPPED)
    flagged = [r for r in results if r.flagged]
    exact = [r for r in results if r.diagnosis == "ok"]
    map_and_addr = [
        r for r in results
        if r.mapped_network != UNMAPPED and r.address_match == "MATCH"
    ]
    summary = dict(Counter(r.diagnosis for r in results))
    cache_coverage = _compute_cache_coverage(df, cfg, results)

    report = ClaimsReport(
        run_id=_make_run_id(),
        total_claims=len(results),
        unmapped_count=unmapped,
        flagged_count=len(flagged),
        exact_match_count=len(exact),
        mapping_and_address_match_count=len(map_and_addr),
        results=results,
        summary_by_diagnosis=summary,
        assignment_npis=sorted(ASSIGNMENT_NPIS),
        cache_coverage=cache_coverage,
    )

    if cfg.persist:
        save_claims_report(report, cfg.output_dir)
    return report


def render_claims_markdown(report: ClaimsReport) -> str:
    lines = [
        f"# Claims-to-Network Report — `{report.run_id}`",
        "",
        f"- Total claims: **{report.total_claims}**",
        f"- Unmapped (Exhibit A gap): **{report.unmapped_count}**",
        f"- Flagged: **{report.flagged_count}**",
        f"- Exact matches: **{report.exact_match_count}**",
        f"- Mapping + address match: **{report.mapping_and_address_match_count}**",
    ]
    if report.cache_coverage:
        cc = report.cache_coverage
        lines += [
            "",
            "## Reference cache coverage",
            f"- Unique NPIs in claims: **{cc.unique_npis}**",
            f"- NPPES cached locally: **{cc.nppes_cached}/{cc.unique_npis}**",
            f"- Candor detail cached: **{cc.candor_detail_cached}/{cc.unique_npis}**",
            f"- Claims with reference data: **{cc.claims_with_reference}/{report.total_claims}**",
        ]
    lines += [
        "",
        "## Summary by diagnosis",
    ]
    for code, count in sorted(report.summary_by_diagnosis.items()):
        lines.append(f"- `{code}`: {count}")
    lines += ["", "## Flagged claims (sample)"]
    for r in [x for x in report.results if x.flagged][:30]:
        lines.append(
            f"- `{r.npi}` {r.provider_name or ''}: {r.diagnosis} — {r.explanation}"
        )
    return "\n".join(lines) + "\n"


def save_claims_report(report: ClaimsReport, output_dir: str | Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    json_path.write_text(report.model_dump_json(indent=2))
    (output_dir / f"{report.run_id}.md").write_text(render_claims_markdown(report))
    return json_path
