"""The ship/no-ship answer. The entire system exists to produce this line."""
from __future__ import annotations

from candor_qa.config import DecisionPolicy
from candor_qa.contracts import Alert, ReleaseDecision


def compute_release_decision(
    alerts: list[Alert],
    review_queue_size: int,
    policy: DecisionPolicy,
    regression_mode: str,
) -> ReleaseDecision:
    critical = [a for a in alerts if a.severity == "critical"]
    warnings = [a for a in alerts if a.severity == "warning"]

    if critical:
        return ReleaseDecision.REJECT
    if regression_mode == "baseline":
        # First run: nothing to compare against. Alerts (if any within-run
        # movement existed) were already checked above.
        return ReleaseDecision.BASELINE_ESTABLISHED
    if warnings or review_queue_size > policy.review_queue_threshold:
        return ReleaseDecision.REVIEW
    return ReleaseDecision.APPROVE
