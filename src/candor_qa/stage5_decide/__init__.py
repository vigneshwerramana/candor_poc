"""Stage 5 — DECIDE: regression vs history, alerts, the ship/no-ship answer."""
from candor_qa.stage5_decide.compete import build_competition_summary, prod_guidance
from candor_qa.stage5_decide.decision import compute_release_decision
from candor_qa.stage5_decide.deletions import score_deletions
from candor_qa.stage5_decide.regression import detect_regression
from candor_qa.stage5_decide.report import (
    compute_judge_accuracy,
    make_run_id,
    render_markdown,
    save_report,
    top_findings,
)
from candor_qa.stage5_decide.review_queue import build_review_queue, load_prior_reviews

__all__ = [
    "detect_regression", "compute_release_decision",
    "build_competition_summary", "score_deletions",
    "prod_guidance",
    "compute_judge_accuracy", "make_run_id", "render_markdown", "save_report",
    "top_findings", "build_review_queue", "load_prior_reviews",
]
