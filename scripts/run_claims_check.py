#!/usr/bin/env python3
"""Run claims-to-network checks against Exhibit A + NPPES/Candor locations."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from candor_qa.claims import ClaimsRunConfig, run_claims_check  # noqa: E402


def _default_claims_path() -> str:
    for candidate in (
        ROOT / "data" / "claims" / "claims.csv",
        ROOT / "data" / "claims" / "Solutions Engineering Assignment - Claims.xlsx",
    ):
        if candidate.exists():
            return str(candidate)
    return str(ROOT / "data" / "claims" / "claims.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description="Claims-to-network checker")
    parser.add_argument("--claims", default=_default_claims_path())
    parser.add_argument("--nppes-dir", default=str(ROOT / "data" / "candor_live" / "nppes"))
    parser.add_argument("--candor-details-dir", default=str(ROOT / "data" / "candor_live" / "details"))
    parser.add_argument("--exhibit-a", default=str(ROOT / "config" / "exhibit_a.yaml"))
    parser.add_argument("--output", default=str(ROOT / "data" / "evaluation_history" / "claims"))
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    if not Path(args.claims).exists():
        print(f"Claims file not found: {args.claims}", file=sys.stderr)
        return 1

    report = run_claims_check(ClaimsRunConfig(
        claims_path=args.claims,
        nppes_dir=args.nppes_dir,
        candor_details_dir=args.candor_details_dir,
        exhibit_a_path=args.exhibit_a,
        output_dir=args.output,
        persist=not args.no_persist,
    ))

    print(f"Run {report.run_id}: {report.total_claims} claims, "
          f"{report.unmapped_count} unmapped, {report.flagged_count} flagged")
    flagged = [r for r in report.results if r.flagged and r.npi in set(report.assignment_npis)]
    for r in flagged[:10]:
        print(f"  {r.npi}: {r.diagnosis} — {r.explanation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
