#!/usr/bin/env python3
"""Regenerate RECONCILIATION.csv from DQI validation rules."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from candor_qa.sources import load_candor_search  # noqa: E402
from candor_qa.validation.rules import run_all_rules  # noqa: E402


def main() -> int:
    candor_json = ROOT / "data" / "candor_live" / "raw_page1.json"
    nppes_dir = ROOT / "data" / "candor_live" / "nppes"
    details_dir = ROOT / "data" / "candor_live" / "details"
    out_csv = ROOT / "data" / "findings" / "RECONCILIATION.csv"

    if not candor_json.exists():
        print(f"Missing {candor_json}", file=sys.stderr)
        return 1

    individuals = load_candor_search(candor_json)
    findings = run_all_rules(
        individuals,
        nppes_dir=str(nppes_dir),
        details_dir=str(details_dir) if details_dir.exists() else None,
    )
    by_npi: dict[str, list] = {}
    for f in findings:
        if f.npi != "*":
            by_npi.setdefault(f.npi, []).append(f)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["npi", "name", "dqi_ids", "finding_count", "top_message"])
        for ind in individuals:
            npi = str(ind["npi"])
            name = f"{ind.get('first_name', '')} {ind.get('last_name', '')}".strip()
            fs = by_npi.get(npi, [])
            w.writerow([
                npi, name,
                "|".join(sorted({f.dqi_id for f in fs})),
                len(fs),
                fs[0].message if fs else "",
            ])

    print(f"Wrote {out_csv} ({len(individuals)} providers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
