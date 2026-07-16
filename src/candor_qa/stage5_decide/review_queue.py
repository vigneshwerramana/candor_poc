"""Human review queue with a real lifecycle.

Decisions persist to reviews.json keyed (npi, field, old, new). Later runs
consult them BEFORE routing: an adjudicated change counts as human-labeled
in change_quality and is never re-queued. Without this loop the queue would
be a screenshot, not a feature.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from candor_qa.contracts import JudgedAddition, JudgedChange, ReviewItem

QUEUE_REASONS = {
    "auto_flag": "auto_flagged",
    "judged": "judge_review",       # only when the judge asks for review
}


def load_prior_reviews(path: str | Path) -> dict[tuple, str]:
    path = Path(path)
    if not path.exists():
        return {}
    entries = json.loads(path.read_text())
    return {
        (e["npi"], e["field"], str(e["old_value"]), str(e["new_value"])): e["decision"]
        for e in entries
    }


def append_review(path: str | Path, item: ReviewItem, decision: str) -> None:
    """Writer used by the dashboard's approve/reject buttons."""
    path = Path(path)
    entries = json.loads(path.read_text()) if path.exists() else []
    entries.append({
        "npi": item.npi, "field": item.field,
        "old_value": item.old_value, "new_value": item.new_value,
        "decision": decision,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    })
    path.write_text(json.dumps(entries, indent=2))


def build_review_queue(
    judged: list[JudgedChange],
    judged_additions: list[JudgedAddition] | None = None,
) -> list[ReviewItem]:
    queue: list[ReviewItem] = []
    for item in judged:
        reason = None
        rationale = None
        if item.route == "auto_flag":
            reason = "auto_flagged"
        elif item.route == "judged":
            if item.verdict.verdict == "uncertain":
                reason = "uncertain"
            elif item.verdict.recommended_action == "human_review":
                reason = "judge_review"
            rationale = item.verdict.rationale
        if reason is None:
            continue
        change = item.scored.change
        queue.append(ReviewItem(
            npi=change.npi, field=change.field,
            old_value=change.old_value, new_value=change.new_value,
            change_kind=change.change_kind, reason=reason,
            confidence=item.scored.confidence,
            cross_source_status=item.scored.cross_source_status,
            judge_rationale=rationale,
        ))

    for item in judged_additions or []:
        reason = None
        rationale = None
        if item.route == "auto_flag":
            reason = "addition_flagged"
        elif item.route == "judged" and item.verdict.verdict == "uncertain":
            reason = "addition_uncertain"
            rationale = item.verdict.rationale
        if reason is None:
            continue
        event = item.scored.event
        queue.append(ReviewItem(
            npi=event.npi, field="__record__",
            old_value=None, new_value=event.kind.value,
            change_kind=event.kind, reason=reason,
            confidence=item.scored.confidence,
            cross_source_status=item.scored.cross_source_status,
            judge_rationale=rationale,
        ))
    return queue
