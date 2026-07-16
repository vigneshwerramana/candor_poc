"""Export persisted delta summaries from an EvalReport JSON to CSV.

Usage:
  python scripts/export_delta.py --report data/evaluation_history/release/<run>.json
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from candor_qa.contracts import EvalReport


def _write_field_changes(report: EvalReport, path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "npi",
                "field",
                "old_value",
                "new_value",
                "change_kind",
                "disposition",
                "confidence",
            ]
        )
        for s in report.field_change_summaries:
            w.writerow(
                [
                    s.npi,
                    s.field,
                    repr(s.old_value),
                    repr(s.new_value),
                    s.change_kind.value,
                    s.disposition or "",
                    "" if s.confidence is None else f"{s.confidence:.4f}",
                ]
            )


def _write_record_events(report: EvalReport, path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["npi", "kind", "disposition", "confidence"])
        for e in report.record_event_summaries:
            w.writerow(
                [
                    e.npi,
                    e.kind.value,
                    e.disposition or "",
                    "" if e.confidence is None else f"{e.confidence:.4f}",
                ]
            )


def main() -> int:
    p = argparse.ArgumentParser(description="Export EvalReport delta summaries to CSV.")
    p.add_argument("--report", required=True, help="Path to EvalReport .json file")
    p.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write CSV files (default: report's directory)",
    )
    args = p.parse_args()

    report_path = Path(args.report)
    report = EvalReport.model_validate_json(report_path.read_text())

    out_dir = Path(args.output_dir) if args.output_dir else report_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    field_path = out_dir / f"{report.run_id}_field_changes.csv"
    events_path = out_dir / f"{report.run_id}_record_events.csv"

    _write_field_changes(report, field_path)
    _write_record_events(report, events_path)

    print(f"Wrote {field_path}")
    print(f"Wrote {events_path}")
    if report.field_change_summaries_truncated:
        print(
            f"NOTE: field change summaries were truncated in this run "
            f"({len(report.field_change_summaries)} rows persisted; total changes "
            f"{report.total_field_changes})."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

