"""Stage 5: alerts, regression modes, the release decision, review lifecycle."""
import json

import pytest

from candor_qa.contracts import (
    Alert,
    ChangeKind,
    CrossSourceStatus,
    MetricResult,
    ReleaseDecision,
    ReviewItem,
    VolumeSummary,
)
from candor_qa.stage5_decide.decision import compute_release_decision
from candor_qa.stage5_decide.regression import detect_regression
from candor_qa.stage5_decide.review_queue import append_review, load_prior_reviews


def metric(name, score, weight=0.2, direction="higher_is_better", status="ok"):
    return MetricResult(name=name, score=score, weight=weight,
                        direction=direction, status=status)


def volume(added=0, deleted=0, base=100):
    return VolumeSummary(
        baseline_count=base, candidate_count=base + added - deleted,
        added_count=added, deleted_count=deleted, updated_count=0,
        unchanged_count=base - deleted, added_pct=added / base,
        deleted_pct=deleted / base)


def run_detect(config, before, after, vol=None, tmp_path=None, mode="baseline",
               fingerprint="fp", **kwargs):
    return detect_regression(before, after, kwargs.get("overall"), vol or volume(),
                             config.metrics, tmp_path or ".", mode, fingerprint,
                             kwargs.get("baseline_run_id"))


def test_drop_alert_fires(config, tmp_path):
    _, alerts = run_detect(
        config, [metric("phone_validity", 0.99)], [metric("phone_validity", 0.90)],
        tmp_path=tmp_path)
    assert any(a.metric == "phone_validity" and a.severity == "critical"
               for a in alerts)


def test_volume_alert_catches_silent_row_drop(config, tmp_path):
    # every rate metric identical — only the volume alert can see this
    _, alerts = run_detect(config, [metric("phone_validity", 0.99)],
                           [metric("phone_validity", 0.99)],
                           vol=volume(deleted=5), tmp_path=tmp_path)
    assert any(a.kind == "volume" and a.severity == "critical" for a in alerts)


def test_improvement_anomaly_is_suspicious(config, tmp_path):
    _, alerts = run_detect(config, [metric("completeness", 0.80, weight=0.25)],
                           [metric("completeness", 0.95, weight=0.25)],
                           tmp_path=tmp_path)
    assert any(a.kind == "improvement_anomaly" for a in alerts)


def test_low_confidence_metric_never_alerts(config, tmp_path):
    _, alerts = run_detect(
        config,
        [metric("phone_validity", 0.99)],
        [metric("phone_validity", 0.50, status="low_confidence")],
        tmp_path=tmp_path)
    assert alerts == []


def test_first_run_falls_back_to_baseline(config, tmp_path):
    result, _ = run_detect(config, [], [], tmp_path=tmp_path, mode="compare")
    assert result.mode == "baseline"
    assert "No evaluation history" in result.notes[0]


def _write_history(tmp_path, run_id, fingerprint, phone_validity, overall,
                   decision="APPROVE"):
    (tmp_path / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id, "config_fingerprint": fingerprint,
        "overall_score": overall,
        "release_decision": decision,
        "metrics_candidate": [
            {"name": "phone_validity", "score": phone_validity}],
    }))


def test_compare_uses_matching_fingerprint_history(config, tmp_path):
    _write_history(tmp_path, "run1", "fp", 0.99, 0.95)
    result, alerts = run_detect(
        config, [], [metric("phone_validity", 0.90)],
        tmp_path=tmp_path, mode="compare", fingerprint="fp", overall=0.90)
    assert result.mode == "compare" and result.compared_run_id == "run1"
    assert result.metric_deltas["phone_validity"] == pytest.approx(-0.09)
    assert any(a.metric == "phone_validity" for a in alerts)      # history plane
    assert any(a.metric == "overall_score" for a in alerts)


def test_config_mismatch_warns(config, tmp_path):
    _write_history(tmp_path, "run1", "OTHER", 0.99, 0.95)
    result, alerts = run_detect(config, [], [metric("phone_validity", 0.99)],
                                tmp_path=tmp_path, mode="compare", fingerprint="fp")
    assert result.config_mismatch
    assert any(a.kind == "config_mismatch" for a in alerts)


def test_compare_to_specific_run(config, tmp_path):
    _write_history(tmp_path, "run1", "fp", 0.99, 0.95)
    _write_history(tmp_path, "run2", "fp", 0.80, 0.85)
    result, _ = run_detect(config, [], [metric("phone_validity", 0.99)],
                           tmp_path=tmp_path, mode="compare_to", fingerprint="fp",
                           baseline_run_id="run1")
    assert result.compared_run_id == "run1"


def test_rejected_runs_never_set_the_quality_bar(config, tmp_path):
    # A rejected candidate never shipped: comparing against it would let a
    # chain of slightly-less-bad releases ratchet quality downward.
    _write_history(tmp_path, "run1", "fp", 0.99, 0.95, decision="APPROVE")
    _write_history(tmp_path, "run2", "fp", 0.50, 0.60, decision="REJECT")
    result, alerts = run_detect(config, [], [metric("phone_validity", 0.99)],
                                tmp_path=tmp_path, mode="compare", fingerprint="fp")
    assert result.compared_run_id == "run1"
    assert alerts == []          # vs run2 a huge "improvement" would have fired


def test_decision_rule(config):
    policy = config.metrics.decision
    critical = Alert(metric="x", severity="critical", kind="drop", message="m")
    warning = Alert(metric="x", severity="warning", kind="drop", message="m")
    assert compute_release_decision([critical], 0, policy, "compare") \
        is ReleaseDecision.REJECT
    assert compute_release_decision([warning], 0, policy, "compare") \
        is ReleaseDecision.REVIEW
    assert compute_release_decision([], 50, policy, "compare") \
        is ReleaseDecision.REVIEW      # queue overflow
    assert compute_release_decision([], 0, policy, "compare") \
        is ReleaseDecision.APPROVE
    assert compute_release_decision([], 0, policy, "baseline") \
        is ReleaseDecision.BASELINE_ESTABLISHED
    # criticals dominate even on a first run
    assert compute_release_decision([critical], 0, policy, "baseline") \
        is ReleaseDecision.REJECT


def test_review_lifecycle_roundtrip(tmp_path):
    path = tmp_path / "reviews.json"
    item = ReviewItem(npi="1", field="phone", old_value="a", new_value="b",
                      change_kind=ChangeKind.modified, reason="auto_flagged",
                      confidence=0.3,
                      cross_source_status=CrossSourceStatus.weak_evidence)
    append_review(path, item, "approved")
    reviews = load_prior_reviews(path)
    assert reviews[("1", "phone", "a", "b")] == "approved"
