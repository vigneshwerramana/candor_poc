"""CLI entry point.

    python scripts/run_evaluation.py \
        --baseline data/providers_v1.csv --candidate data/providers_v2.csv \
        --judge-mode mock --regression-mode compare

Exit codes: 0 = APPROVE / BASELINE_ESTABLISHED, 1 = REVIEW, 2 = REJECT.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from candor_qa.pipeline import RunConfig, run_evaluation
from candor_qa.stage5_decide import render_markdown

EXIT_CODES = {"APPROVE": 0, "BASELINE_ESTABLISHED": 0, "REVIEW": 1, "REJECT": 2}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate candidate snapshot quality")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--evidence", default="data/source_evidence.json")
    parser.add_argument("--labels", default="data/labels.json",
                        help="ground-truth labels for judge meta-QA ('' to disable)")
    parser.add_argument("--reviews", default="data/reviews.json")
    parser.add_argument("--output", default="data/evaluation_history")
    parser.add_argument("--judge-mode", choices=["mock", "real"], default="mock")
    parser.add_argument("--regression-mode",
                        choices=["compare", "baseline", "compare_to"], default="compare")
    parser.add_argument("--baseline-run-id", default=None)
    parser.add_argument("--comparison-mode",
                        choices=["same_input", "temporal", "model_compete"],
                        default=None)
    parser.add_argument("--baseline-label", default="baseline",
                        help="display name for the baseline (model_compete)")
    parser.add_argument("--candidate-label", default="candidate",
                        help="display name for the candidate (model_compete)")
    args = parser.parse_args()

    report = run_evaluation(RunConfig(
        baseline_path=args.baseline,
        candidate_path=args.candidate,
        config_dir=args.config_dir,
        evidence_path=args.evidence,
        labels_path=args.labels or None,
        reviews_path=args.reviews,
        output_dir=args.output,
        judge_mode=args.judge_mode,
        regression_mode=args.regression_mode,
        baseline_run_id=args.baseline_run_id,
        comparison_mode=args.comparison_mode,
        baseline_label=args.baseline_label,
        candidate_label=args.candidate_label,
    ))
    print(render_markdown(report))
    print(f"Report saved to {args.output}/{report.run_id}.json")
    return EXIT_CODES[report.release_decision.value]


if __name__ == "__main__":
    sys.exit(main())
