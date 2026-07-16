"""Stage 4: routing bands, budget, the change_quality denominator rule,
overall-score renormalization, verdict caching."""
import pytest

from candor_qa.contracts import (
    ChangeKind,
    CrossSourceStatus,
    FieldChange,
    JudgeVerdict,
    MetricResult,
    ScoredChange,
)
from candor_qa.stage4_judge.rollup import compute_change_quality, compute_overall_score
from candor_qa.stage4_judge.router import route_and_judge


class ScriptedJudge:
    """Deterministic test double; counts calls like the real caching clients."""

    def __init__(self, verdict="valid_update", action="auto_accept"):
        self._verdict = verdict
        self._action = action
        self.calls = 0

    def judge(self, scored):
        self.calls += 1
        return JudgeVerdict(verdict=self._verdict, confidence=0.9,
                            rationale="scripted", recommended_action=self._action)


def scored(npi="1", field="phone", kind=ChangeKind.modified,
           status=CrossSourceStatus.verified, confidence=0.99):
    return ScoredChange(
        change=FieldChange(npi=npi, field=field, old_value="a", new_value="b",
                           change_kind=kind),
        cross_source_status=status, confidence=confidence,
    )


def routes(judged):
    return {j.scored.change.npi: j.route for j in judged}


def test_routing_bands(config):
    policy = config.metrics.judge_policy
    judged = route_and_judge([
        scored("auto", confidence=0.99, status=CrossSourceStatus.verified),
        scored("flag", confidence=0.30, status=CrossSourceStatus.weak_evidence),
        scored("ambiguous", confidence=0.60, status=CrossSourceStatus.weak_evidence),
        scored("wiped", kind=ChangeKind.wiped, confidence=0.99,
               status=CrossSourceStatus.verified),          # high conf, judged anyway
        scored("conflict", status=CrossSourceStatus.conflict, confidence=0.30),
    ], ScriptedJudge(), policy)
    r = routes(judged)
    assert r["auto"] == "auto_accept"
    assert r["flag"] == "auto_flag"
    assert r["ambiguous"] == "judged"
    assert r["wiped"] == "judged"        # wipes are never auto-accepted
    assert r["conflict"] == "judged"     # conflicts are never auto-flagged silently


def test_high_confidence_without_verification_is_not_auto_accepted(config):
    judged = route_and_judge(
        [scored("x", confidence=0.99, status=CrossSourceStatus.weak_evidence)],
        ScriptedJudge(), config.metrics.judge_policy)
    assert routes(judged)["x"] == "judged"


def test_budget_overflow_deferred_not_invalid(config):
    policy = config.metrics.judge_policy.model_copy(update={"max_judge_calls": 2})
    items = [scored(str(i), confidence=0.6,
                    status=CrossSourceStatus.weak_evidence) for i in range(5)]
    judge = ScriptedJudge()
    judged = route_and_judge(items, judge, policy)
    assert judge.calls == 2
    assert sum(1 for j in judged if j.route == "budget_deferred") == 3

    metric, breakdown = compute_change_quality(judged, config.metrics)
    assert breakdown.budget_deferred == 3
    assert metric.sample_size == 2                     # deferred excluded
    assert breakdown.judge_coverage == pytest.approx(2 / 5)


def test_wiped_judged_first_under_budget(config):
    policy = config.metrics.judge_policy.model_copy(update={"max_judge_calls": 1})
    items = [
        scored("mod", confidence=0.55, status=CrossSourceStatus.weak_evidence),
        scored("wipe", kind=ChangeKind.wiped, confidence=0.60,
               status=CrossSourceStatus.weak_evidence),
    ]
    judged = route_and_judge(items, ScriptedJudge(), policy)
    assert routes(judged)["wipe"] == "judged"
    assert routes(judged)["mod"] == "budget_deferred"


def test_prior_reviews_short_circuit(config):
    item = scored("seen", confidence=0.6, status=CrossSourceStatus.weak_evidence)
    judge = ScriptedJudge()
    judged = route_and_judge([item], judge, config.metrics.judge_policy,
                             prior_reviews={item.change.key(): "approved"})
    assert judge.calls == 0
    assert judged[0].route == "prior_review"
    _, breakdown = compute_change_quality(judged, config.metrics)
    assert breakdown.review_approved == 1


def test_denominator_rule_excludes_indeterminate(config):
    judged = (
        route_and_judge([scored("a")], ScriptedJudge(), config.metrics.judge_policy)
        + route_and_judge([scored("u", confidence=0.6,
                                  status=CrossSourceStatus.weak_evidence)],
                          ScriptedJudge("uncertain", "human_review"),
                          config.metrics.judge_policy)
        + route_and_judge([scored("f", confidence=0.1,
                                  status=CrossSourceStatus.weak_evidence)],
                          ScriptedJudge(), config.metrics.judge_policy)
    )
    metric, breakdown = compute_change_quality(judged, config.metrics)
    # only the auto-accept is definitive; uncertain + auto_flag are excluded
    assert (breakdown.numerator, breakdown.denominator) == (1, 1)
    assert breakdown.uncertain == 1 and breakdown.auto_flagged == 1
    assert metric.status == "low_confidence"           # below min_sample


def test_change_quality_na_on_zero_changes(config):
    metric, _ = compute_change_quality([], config.metrics)
    assert metric.score is None
    assert metric.status == "n/a"


def test_overall_score_renormalizes_on_na(config):
    metrics = [
        MetricResult(name="completeness", score=0.9, weight=0.25),
        MetricResult(name="phone_validity", score=0.8, weight=0.20),
        MetricResult(name="change_quality", score=None, weight=0.25, status="n/a"),
        MetricResult(name="npi_validity", score=1.0, weight=0.0),   # report-only
    ]
    overall, included, excluded = compute_overall_score(metrics, config.metrics)
    expected = (0.9 * 0.25 + 0.8 * 0.20) / 0.45
    assert overall == pytest.approx(expected, abs=1e-4)
    assert included == ["completeness", "phone_validity"]
    assert "change_quality" in excluded and "npi_validity" in excluded


def test_overall_score_inverts_lower_is_better(config):
    metrics = [
        MetricResult(name="duplicate_rate", score=0.02, weight=0.15,
                     direction="lower_is_better"),
    ]
    overall, _, _ = compute_overall_score(metrics, config.metrics)
    assert overall == pytest.approx(0.98)


def test_mock_judge_caches_identical_changes(config, sample_data):
    from candor_qa.stage4_judge.judge import MockJudgeClient
    client = MockJudgeClient(sample_data / "labels.json")
    item = scored()
    first = client.judge(item)
    second = client.judge(item)
    assert client.calls == 1
    assert first == second
