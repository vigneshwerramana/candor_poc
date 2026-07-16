"""Competition summary assembly: win rate is change_quality read as a contest,
snapshot delta is symmetric, thresholds gate the recommendation."""
import pytest

from candor_qa.contracts import (
    ChangeQualityBreakdown,
    MetricResult,
    VolumeSummary,
)
from candor_qa.stage5_decide.compete import (
    build_competition_summary,
    snapshot_only_score,
)


def metrics(completeness, phone, freshness, dup=0.0):
    return [
        MetricResult(name="completeness", score=completeness, weight=0.25),
        MetricResult(name="phone_validity", score=phone, weight=0.20),
        MetricResult(name="duplicate_rate", score=dup, weight=0.15,
                     direction="lower_is_better"),
        MetricResult(name="freshness", score=freshness, weight=0.15),
        # transition metrics must NOT leak into the symmetric snapshot score
        MetricResult(name="change_quality", score=0.5, weight=0.25),
    ]


def breakdown(wins, losses):
    return ChangeQualityBreakdown(auto_accepted=wins, judged_regression=losses)


def volume(base=400, candidate=410, added=10, deleted=0):
    return VolumeSummary(baseline_count=base, candidate_count=candidate,
                         added_count=added, deleted_count=deleted,
                         updated_count=35, unchanged_count=base - 35,
                         added_pct=added / base, deleted_pct=deleted / base)


def addition_metric(score):
    return MetricResult(name="addition_quality", score=score, weight=0.10)


def build(config, wins, losses, delta_metrics=None):
    return build_competition_summary(
        "A", "B", breakdown(wins, losses),
        metrics(0.99, 0.99, 0.85),
        delta_metrics or metrics(0.99, 0.99, 0.86),
        addition_metric(1.0), volume(), config.metrics)


def test_snapshot_only_score_excludes_transition_metrics(config):
    score = snapshot_only_score(metrics(1.0, 1.0, 1.0, dup=0.0))
    assert score == pytest.approx(1.0)          # change_quality 0.5 ignored


def test_candidate_wins(config):
    summary = build(config, wins=30, losses=5)
    assert summary.field_win_rate == pytest.approx(30 / 35, abs=1e-4)
    assert summary.disagreement_count == 35
    assert summary.snapshot_score_delta > 0
    assert summary.recommendation == "candidate_wins"
    assert summary.winner_name() == "B"
    assert summary.coverage_delta == 10


def test_baseline_wins(config):
    summary = build(config, wins=5, losses=30,
                    delta_metrics=metrics(0.99, 0.99, 0.84))
    assert summary.recommendation == "baseline_wins"
    assert summary.winner_name() == "A"


def test_inconclusive_on_split_signals(config):
    # candidate wins fields but its snapshot health is WORSE -> inconclusive
    summary = build(config, wins=30, losses=5,
                    delta_metrics=metrics(0.90, 0.90, 0.70))
    assert summary.snapshot_score_delta < 0
    assert summary.recommendation == "inconclusive"


def test_inconclusive_below_min_disagreements(config):
    summary = build(config, wins=10, losses=2)   # 12 < 20
    assert summary.field_win_rate is None
    assert summary.recommendation == "inconclusive"
    assert summary.winner_name() is None
