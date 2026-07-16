"""Addition routing + rollup: structural rejects are KNOWN-invalid (in the
denominator); unknowns are excluded; budget is shared with field changes."""
import pytest

from candor_qa.contracts import (
    AdditionVerdict,
    CrossSourceStatus,
    RecordEvent,
    RecordEventKind,
    ScoredAddition,
)
from candor_qa.stage4_judge.rollup import compute_addition_quality
from candor_qa.stage4_judge.router import route_and_judge_additions


class ScriptedAdditionJudge:
    def __init__(self, verdict="valid_addition"):
        self._verdict = verdict
        self.calls = 0

    def judge_addition(self, scored):
        self.calls += 1
        return AdditionVerdict(verdict=self._verdict, confidence=0.9,
                               rationale="scripted")


def scored_addition(npi="1234567893", confidence=0.99,
                    status=CrossSourceStatus.verified, failures=()):
    event = RecordEvent(
        npi=npi, kind=RecordEventKind.added,
        record={"npi": npi, "phone": "6175550134"},
        completeness=1.0,
        validity_flags={"npi_luhn_ok": True, "phone_ok": True, "required_ok": True},
    )
    return ScoredAddition(event=event, cross_source_status=status,
                          confidence=confidence,
                          structural_failures=list(failures))


def routes(judged):
    return {j.scored.event.npi: j.route for j in judged}


def test_addition_routing_bands(config):
    policy = config.metrics.judge_policy
    judged = route_and_judge_additions([
        scored_addition("auto", 0.99, CrossSourceStatus.verified),
        scored_addition("junk", 0.99, failures=["npi_luhn_ok"]),
        scored_addition("noev", 0.50, CrossSourceStatus.unverifiable),
        scored_addition("weak", 0.30, CrossSourceStatus.weak_evidence),
        scored_addition("conf", 0.30, CrossSourceStatus.conflict),
    ], ScriptedAdditionJudge(), policy, remaining_budget=10)
    r = routes(judged)
    assert r["auto"] == "auto_accept"
    assert r["junk"] == "structural_reject"     # provably invalid, no judge spend
    assert r["noev"] == "judged"
    assert r["weak"] == "auto_flag"
    assert r["conf"] == "judged"                # conflicts always judged


def test_structural_reject_counts_as_invalid_not_unknown(config):
    judged = route_and_judge_additions(
        [scored_addition("ok"), scored_addition("junk", failures=["phone_ok"])],
        ScriptedAdditionJudge(), config.metrics.judge_policy, remaining_budget=10)
    metric, breakdown = compute_addition_quality(judged, config.metrics)
    assert breakdown.structural_rejected == 1
    assert (breakdown.numerator, breakdown.denominator) == (1, 2)
    assert metric.score == pytest.approx(0.5)


def test_budget_shared_with_changes(config):
    # only 1 slot left after field changes consumed the rest
    items = [scored_addition(str(i), 0.6, CrossSourceStatus.weak_evidence)
             for i in range(3)]
    judge = ScriptedAdditionJudge()
    judged = route_and_judge_additions(items, judge, config.metrics.judge_policy,
                                       remaining_budget=1)
    assert judge.calls == 1
    _, breakdown = compute_addition_quality(judged, config.metrics)
    assert breakdown.budget_deferred == 2       # excluded, never counted invalid
    assert breakdown.denominator == 1


def test_na_on_zero_additions(config):
    metric, _ = compute_addition_quality([], config.metrics)
    assert metric.score is None
    assert metric.status == "n/a"


def test_min_sample_guard(config):
    judged = route_and_judge_additions([scored_addition("only")],
                                       ScriptedAdditionJudge(),
                                       config.metrics.judge_policy,
                                       remaining_budget=10)
    metric, _ = compute_addition_quality(judged, config.metrics)
    assert metric.status == "low_confidence"    # 1 < min_sample 5


def test_uncertain_excluded_from_denominator(config):
    judged = route_and_judge_additions(
        [scored_addition("mystery", 0.6, CrossSourceStatus.unverifiable)],
        ScriptedAdditionJudge("uncertain"), config.metrics.judge_policy,
        remaining_budget=10)
    metric, breakdown = compute_addition_quality(judged, config.metrics)
    assert breakdown.uncertain == 1
    assert metric.score is None                 # denominator empty -> N/A
