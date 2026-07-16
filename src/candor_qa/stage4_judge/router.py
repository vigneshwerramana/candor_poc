"""Routing: which changes are trusted, which are judged, which go straight
to humans — under a hard budget (the cost lever at 70M scale).

Band order (first match wins):
1. prior human review exists      -> resolved, never re-processed
2. wiped or conflicted            -> must judge (harsh path)
3. confidence >= 0.95 & verified  -> auto-accept, no judge needed
4. confidence < 0.50              -> auto-flag to human review
5. otherwise (ambiguous band)     -> judge, budget permitting

Budget overflow is deferred and EXCLUDED from the change_quality denominator
— an unjudged change is unknown, not invalid. Judging priority: wiped first,
then lowest confidence (most suspicious first).
"""
from __future__ import annotations

from candor_qa.config import JudgePolicy
from candor_qa.contracts import (
    ChangeKind,
    JudgedAddition,
    JudgedChange,
    ScoredAddition,
    ScoredChange,
)
from candor_qa.stage4_judge.judge import JudgeClient


def route_and_judge(
    scored_changes: list[ScoredChange],
    judge_client: JudgeClient,
    policy: JudgePolicy,
    prior_reviews: dict[tuple, str] | None = None,
) -> list[JudgedChange]:
    prior_reviews = prior_reviews or {}
    resolved: list[JudgedChange] = []
    to_judge: list[ScoredChange] = []

    for scored in scored_changes:
        key = scored.change.key()
        if key in prior_reviews:
            resolved.append(JudgedChange(
                scored=scored, route="prior_review",
                prior_review_decision=prior_reviews[key],
            ))
        elif (scored.change.change_kind.value in policy.always_judge_kinds
              or scored.cross_source_status.value in policy.always_judge_statuses):
            to_judge.append(scored)
        elif (scored.confidence >= policy.auto_accept_min_confidence
              and scored.cross_source_status.value == "verified"):
            resolved.append(JudgedChange(scored=scored, route="auto_accept"))
        elif scored.confidence < policy.auto_flag_max_confidence:
            resolved.append(JudgedChange(scored=scored, route="auto_flag"))
        else:
            to_judge.append(scored)

    to_judge.sort(key=lambda s: (s.change.change_kind is not ChangeKind.wiped,
                                 s.confidence))
    within_budget = to_judge[:policy.max_judge_calls]
    deferred = to_judge[policy.max_judge_calls:]

    for scored in within_budget:
        resolved.append(JudgedChange(
            scored=scored, route="judged", verdict=judge_client.judge(scored)
        ))
    for scored in deferred:
        resolved.append(JudgedChange(scored=scored, route="budget_deferred"))

    return resolved


def route_and_judge_additions(
    scored_additions: list[ScoredAddition],
    judge_client: JudgeClient,
    policy: JudgePolicy,
    remaining_budget: int,
    prior_reviews: dict[tuple, str] | None = None,
) -> list[JudgedAddition]:
    """Additions share the judge budget with field changes (changes are judged
    first; `remaining_budget` is what's left). One extra route vs changes:
    structural_reject — a bad NPI checksum or placeholder phone is provably
    junk, so it is rejected without evidence or judge spend and, unlike
    auto_flag, counts as INVALID in addition_quality."""
    prior_reviews = prior_reviews or {}
    resolved: list[JudgedAddition] = []
    to_judge: list[ScoredAddition] = []

    for scored in scored_additions:
        key = scored.event.key()
        if key in prior_reviews:
            resolved.append(JudgedAddition(
                scored=scored, route="prior_review",
                prior_review_decision=prior_reviews[key],
            ))
        elif scored.structural_failures:
            resolved.append(JudgedAddition(scored=scored, route="structural_reject"))
        elif (scored.confidence >= policy.auto_accept_min_confidence
              and scored.cross_source_status.value == "verified"):
            resolved.append(JudgedAddition(scored=scored, route="auto_accept"))
        elif scored.cross_source_status.value in policy.always_judge_statuses:
            to_judge.append(scored)
        elif scored.confidence < policy.auto_flag_max_confidence:
            resolved.append(JudgedAddition(scored=scored, route="auto_flag"))
        else:
            to_judge.append(scored)

    to_judge.sort(key=lambda s: s.confidence)
    remaining_budget = max(0, remaining_budget)
    for scored in to_judge[:remaining_budget]:
        resolved.append(JudgedAddition(
            scored=scored, route="judged",
            verdict=judge_client.judge_addition(scored),
        ))
    for scored in to_judge[remaining_budget:]:
        resolved.append(JudgedAddition(scored=scored, route="budget_deferred"))

    return resolved
