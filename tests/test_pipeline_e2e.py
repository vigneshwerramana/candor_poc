"""End-to-end: the framework provably catches every planted regression and
flags none of the planted false-positive traps, on the full generated data."""
import pytest

from candor_qa.contracts import ReleaseDecision
from candor_qa.pipeline import RunConfig, run_evaluation
from candor_qa.stage5_decide.review_queue import append_review


def make_run(sample_data, tmp_path, candidate="providers_v2.csv", **overrides):
    defaults = dict(
        baseline_path=str(sample_data / "providers_v1.csv"),
        candidate_path=str(sample_data / candidate),
        config_dir="config",
        evidence_path=str(sample_data / "source_evidence.json"),
        labels_path=str(sample_data / "labels.json"),
        reviews_path=str(tmp_path / "reviews.json"),
        output_dir=str(tmp_path / "history"),
        regression_mode="compare",
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def test_identical_snapshots_are_a_clean_baseline(sample_data, tmp_path):
    report = run_evaluation(make_run(sample_data, tmp_path,
                                     candidate="providers_v1.csv"))
    assert report.total_field_changes == 0
    assert report.regression.mode == "baseline"        # no history -> fallback
    assert report.release_decision is ReleaseDecision.BASELINE_ESTABLISHED
    assert report.critical_alerts == [] and report.warnings == []
    cq = next(m for m in report.metrics_candidate if m.name == "change_quality")
    assert cq.score is None and cq.status == "n/a"     # N/A, not a fake 1.0
    assert report.overall_score is not None            # renormalized without it


def test_planted_regressions_all_caught_and_no_traps_leak(sample_data, tmp_path):
    baseline_run = run_evaluation(make_run(sample_data, tmp_path,
                                           candidate="providers_v1.csv"))
    report = run_evaluation(make_run(sample_data, tmp_path))

    # the headline: ship/no-ship
    assert report.release_decision is ReleaseDecision.REJECT
    assert report.regression.compared_run_id == baseline_run.run_id

    # evaluator meta-QA vs ground truth
    accuracy = report.judge_accuracy
    assert accuracy.planted_regressions_caught == accuracy.planted_regressions
    assert accuracy.traps_flagged == 0
    assert accuracy.regression_precision == 1.0
    assert accuracy.regression_recall == 1.0

    # traps never surfaced as changes: visible in the suppression counters
    # (traps_flagged == 0 above proves none reached routing either)
    assert report.suppressed_by_normalization == 35    # 20 phones + 10 names + 5 zips
    assert report.suppressed_by_set_compare == 10      # insurance shuffles

    # every planted regression judged as such: 15 wiped + 8 placeholder
    # overwrites + 2 placeholder fills + 3 conflicts + 10 state corruptions
    assert report.change_quality.judged_regression == 38

    # additions: 4 evidence-verified + 2 judged valid, 3 provably junk rejected
    additions = report.addition_quality
    assert additions.auto_accepted == 4
    assert additions.judged_valid == 2
    assert additions.structural_rejected == 3
    assert (additions.numerator, additions.denominator) == (6, 9)
    accuracy = report.judge_accuracy
    assert accuracy.planted_invalid_additions_caught == 3

    # deletions: all 16 planted drops hit complete records -> flagged for review
    assert len(report.deletion_verdicts) == 16
    assert all(v.recommended_action == "review" for v in report.deletion_verdicts)

    # dedupe caught the planted duplicates and null keys, no cartesian blowup
    dedupe = report.dedupe_report_candidate
    assert dedupe.dropped_duplicate_rows == 10
    assert dedupe.null_npi_count == 2

    # volume alert caught the silent row drop
    assert report.volume_summary.deleted_count == 16
    assert report.volume_summary.added_count == 9
    assert any(a.kind == "volume" for a in report.critical_alerts)

    # the specific planted failures each fired their alert
    critical_metrics = {a.metric for a in report.critical_alerts}
    assert {"phone_validity", "duplicate_rate", "deleted_pct",
            "state_zip_consistency"} <= critical_metrics
    assert any(a.metric == "freshness" for a in report.warnings)

    # unlabeled changes were routed to humans, excluded from the denominator
    assert report.change_quality.uncertain == 4
    assert len(report.review_queue) == 4

    # judge cost accounting present
    assert report.judge_calls_made > 0
    assert report.estimated_judge_cost > 0
    # persisted, browseable delta summaries (used by dashboard + export)
    assert report.field_change_summaries_truncated is False
    assert len(report.field_change_summaries) == report.total_field_changes
    # round-trip serialization includes the new fields
    from candor_qa.contracts import EvalReport
    round_tripped = EvalReport.model_validate_json(report.model_dump_json())
    assert len(round_tripped.field_change_summaries) == report.total_field_changes


def test_clean_release_is_approved(sample_data, tmp_path):
    """The APPROVE path: only legitimate changes + traps + valid additions."""
    run_evaluation(make_run(sample_data, tmp_path, candidate="providers_v1.csv",
                            labels_path=str(sample_data / "labels_clean.json")))
    report = run_evaluation(make_run(
        sample_data, tmp_path, candidate="providers_v2_clean.csv",
        labels_path=str(sample_data / "labels_clean.json")))
    assert report.release_decision is ReleaseDecision.APPROVE
    assert report.critical_alerts == []
    assert report.change_quality.judged_regression == 0
    assert report.addition_quality.structural_rejected == 0
    cq = report.change_quality
    assert cq.numerator == cq.denominator          # every definitive change valid


def test_rejected_run_does_not_become_the_baseline(sample_data, tmp_path):
    """Bad release rejected, then a clean release compares against the
    ACCEPTED baseline — not the rejected run."""
    baseline = run_evaluation(make_run(sample_data, tmp_path,
                                       candidate="providers_v1.csv"))
    rejected = run_evaluation(make_run(sample_data, tmp_path))
    assert rejected.release_decision is ReleaseDecision.REJECT

    clean = run_evaluation(make_run(
        sample_data, tmp_path, candidate="providers_v2_clean.csv",
        labels_path=str(sample_data / "labels_clean.json")))
    assert clean.regression.compared_run_id == baseline.run_id
    assert clean.release_decision is ReleaseDecision.APPROVE


def test_model_competition_end_to_end(sample_data, tmp_path):
    report = run_evaluation(make_run(
        sample_data, tmp_path,
        baseline_path=str(sample_data / "dev_a.csv"),
        candidate="dev_b.csv",
        labels_path=str(sample_data / "labels_compete.json"),
        comparison_mode="model_compete",
        regression_mode="baseline",
        baseline_label="Scraper A", candidate_label="Scraper B"))
    competition = report.competition_summary
    assert competition is not None
    assert competition.recommendation == "candidate_wins"
    assert competition.disagreement_count == 35
    assert competition.field_win_rate == pytest.approx(30 / 35, abs=1e-4)
    assert competition.regressions_in_candidate == 5
    assert competition.coverage_delta == 10
    assert competition.addition_quality == 1.0
    # coverage is the contest, not a row-drop regression
    assert not any(a.kind == "volume"
                   for a in report.critical_alerts + report.warnings)
    assert any("Coverage difference" in n for n in report.regression.notes)
    assert report.prod_recommendation == "Deploy Scraper B to PROD"
    assert report.prod_rationale, "expected rationale bullets for the PROD recommendation"


def test_review_decisions_persist_across_runs(sample_data, tmp_path):
    run = make_run(sample_data, tmp_path)
    first = run_evaluation(run)
    assert first.review_queue, "expected review items to exercise the lifecycle"

    item = first.review_queue[0]
    append_review(run.reviews_path, item, "approved")

    second = run_evaluation(run)
    assert second.change_quality.review_approved == 1
    # the adjudicated change is not re-queued
    assert all((i.npi, i.field) != (item.npi, item.field)
               for i in second.review_queue)
    assert len(second.review_queue) == len(first.review_queue) - 1
