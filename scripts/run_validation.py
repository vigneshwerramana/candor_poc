#!/usr/bin/env python3
"""Run DQI validation on Candor list 2111 roster."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from candor_qa.validation import ValidationRunConfig, run_validation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="DQI validation for Candor roster")
    parser.add_argument(
        "--offline-candor-json",
        default=str(ROOT / "data" / "candor_live" / "raw_page1.json"),
    )
    parser.add_argument("--offline-nppes-dir", default=str(ROOT / "data" / "candor_live" / "nppes"))
    parser.add_argument("--details-dir", default=str(ROOT / "data" / "candor_live" / "details"))
    parser.add_argument("--rules", default=str(ROOT / "config" / "rules.yaml"))
    parser.add_argument("--output", default=str(ROOT / "data" / "evaluation_history" / "validation"))
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    if not Path(args.offline_candor_json).exists():
        print(f"Candor JSON not found: {args.offline_candor_json}", file=sys.stderr)
        return 1

    report = run_validation(ValidationRunConfig(
        candor_json=args.offline_candor_json,
        nppes_dir=args.offline_nppes_dir,
        details_dir=args.details_dir,
        rules_path=args.rules,
        output_dir=args.output,
        persist=not args.no_persist,
    ))

    print(f"Run {report.run_id}: {report.provider_count} providers, "
          f"{len(report.findings)} findings, score={report.overall_score}")
    for dqi, count in sorted(report.summary_by_dqi.items()):
        print(f"  {dqi}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
