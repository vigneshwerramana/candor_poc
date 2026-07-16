"""The whole system, top to bottom.

    PREPARE -> COMPARE -> SCORE -> JUDGE -> DECIDE

Each stage is a pure function over typed contracts (contracts.py). In
production each call below becomes one DAG task; COMPARE parallelizes by
NPI-prefix partition. Nothing here holds state — the only side effect is
persisting the final report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from candor_qa.config import Config, load_config
from candor_qa.contracts import (
    EvalReport,
    FieldChangeSummary,
    RecordEventKind,
    RecordEventSummary,
)
from candor_qa.stage1_prepare import prepare_snapshot
from candor_qa.stage2_compare import compute_delta, compute_snapshot_metrics
from candor_qa.stage3_score import JsonEvidenceProvider, score_changes
from candor_qa.stage3_score.additions import score_additions
from candor_qa.stage4_judge import (
    MockJudgeClient,
    RealJudgeClient,
    compute_change_quality,
    compute_overall_score,
    route_and_judge,
)
from candor_qa.stage4_judge.rollup import compute_addition_quality
from candor_qa.stage4_judge.router import route_and_judge_additions
from candor_qa.stage5_decide import (
    build_competition_summary,
    build_review_queue,
    compute_judge_accuracy,
    compute_release_decision,
    detect_regression,
    load_prior_reviews,
    make_run_id,
    prod_guidance,
    save_report,
    score_deletions,
    top_findings,
)


@dataclass
class RunConfig:
    baseline_path: str
    candidate_path: str
    config_dir: str = "config"
    evidence_path: str = "data/source_evidence.json"
    labels_path: str | None = "data/labels.json"      # judge meta-QA, if present
    reviews_path: str = "data/reviews.json"
    output_dir: str = "data/evaluation_history"
    judge_mode: str = "mock"                          # mock | real
    regression_mode: str = "compare"                  # compare | baseline | compare_to
    baseline_run_id: str | None = None
    comparison_mode: str | None = None                # None -> from metrics.yaml
    baseline_label: str = "baseline"                  # model_compete display names
    candidate_label: str = "candidate"
    reference_date: date | None = None                # freshness anchor (tests)
    persist: bool = True


def _make_judge(run: RunConfig, config: Config):
    if run.judge_mode == "real":
        return RealJudgeClient(config.judge_prompts)
    return MockJudgeClient(run.labels_path)


def run_evaluation(run: RunConfig) -> EvalReport:
    config = load_config(run.config_dir)
    schema, metrics_cfg = config.data_schema, config.metrics
    comparison_mode = run.comparison_mode or metrics_cfg.comparison_mode
    compete = comparison_mode == "model_compete"

    # ---- Stage 1: PREPARE ------------------------------------------------
    baseline = prepare_snapshot(run.baseline_path, schema)
    candidate = prepare_snapshot(run.candidate_path, schema)

    # ---- Stage 2: COMPARE ------------------------------------------------
    delta = compute_delta(baseline.frame, candidate.frame, schema,
                          metrics_cfg.placeholder_values)
    metrics_baseline = compute_snapshot_metrics(
        baseline.frame, baseline.dedupe_report, baseline.normalization_report,
        schema, metrics_cfg, run.reference_date, config.zip_prefixes)
    metrics_candidate = compute_snapshot_metrics(
        candidate.frame, candidate.dedupe_report, candidate.normalization_report,
        schema, metrics_cfg, run.reference_date, config.zip_prefixes)

    # ---- Stage 3: SCORE ---------------------------------------------------
    evidence = JsonEvidenceProvider(run.evidence_path, config.source_tiers)
    scored = score_changes(delta.field_changes, evidence, config.source_tiers, schema)
    added_events = [e for e in delta.record_events
                    if e.kind is RecordEventKind.added]
    scored_additions = score_additions(added_events, evidence,
                                       config.source_tiers, schema)

    # ---- Stage 4: JUDGE ---------------------------------------------------
    judge_client = _make_judge(run, config)
    prior_reviews = load_prior_reviews(run.reviews_path)
    judged = route_and_judge(scored, judge_client,
                             metrics_cfg.judge_policy, prior_reviews)
    judged_additions = route_and_judge_additions(
        scored_additions, judge_client, metrics_cfg.judge_policy,
        remaining_budget=metrics_cfg.judge_policy.max_judge_calls - judge_client.calls,
        prior_reviews=prior_reviews)
    change_quality_metric, breakdown = compute_change_quality(judged, metrics_cfg)
    addition_metric, addition_breakdown = compute_addition_quality(
        judged_additions, metrics_cfg)
    deleted_events = [e for e in delta.record_events
                      if e.kind is RecordEventKind.deleted]
    deletion_metric, deletion_verdicts = score_deletions(deleted_events, metrics_cfg)
    metrics_candidate = metrics_candidate + [change_quality_metric,
                                             addition_metric, deletion_metric]
    overall, included, excluded = compute_overall_score(metrics_candidate, metrics_cfg)

    # ---- Stage 5: DECIDE --------------------------------------------------
    review_queue = build_review_queue(judged, judged_additions)
    regression, alerts = detect_regression(
        metrics_baseline, metrics_candidate, overall, delta.volume, metrics_cfg,
        run.output_dir, run.regression_mode, config.fingerprint,
        run.baseline_run_id, downgrade_volume=compete)
    decision = compute_release_decision(
        alerts, len(review_queue), metrics_cfg.decision, regression.mode)

    competition = None
    prod_recommendation = None
    prod_rationale: list[str] = []
    if compete:
        competition = build_competition_summary(
            run.baseline_label, run.candidate_label, breakdown,
            metrics_baseline, metrics_candidate, addition_metric,
            delta.volume, metrics_cfg)
        prod_recommendation, prod_rationale = prod_guidance(competition)

    # Persist a capped, browseable delta summary for demos and debugging.
    max_field_summaries = 500
    judged_by_key: dict[tuple[str, str, str, str], tuple[str | None, float | None]] = {}
    for item in judged:
        key = item.scored.change.key()
        disposition: str | None = item.route
        confidence: float | None = item.scored.confidence
        if item.route == "judged" and item.verdict:
            disposition = item.verdict.verdict
            confidence = item.verdict.confidence
        elif item.route == "auto_accept":
            disposition = "auto_accepted"
        elif item.route == "auto_flag":
            disposition = "auto_flagged"
        elif item.route == "prior_review":
            disposition = ("review_approved" if item.prior_review_decision == "approved"
                           else "review_rejected")
        judged_by_key[key] = (disposition, confidence)

    field_summaries: list[FieldChangeSummary] = []
    for change in delta.field_changes[:max_field_summaries]:
        disposition, confidence = judged_by_key.get(change.key(), (None, None))
        field_summaries.append(FieldChangeSummary(
            npi=change.npi,
            field=change.field,
            old_value=change.old_value,
            new_value=change.new_value,
            change_kind=change.change_kind,
            disposition=disposition,
            confidence=confidence,
        ))
    field_summaries_truncated = len(delta.field_changes) > max_field_summaries

    addition_by_npi: dict[str, tuple[str | None, float | None]] = {}
    for item in judged_additions:
        disposition: str | None = item.route
        confidence: float | None = item.scored.confidence
        if item.route == "judged" and item.verdict:
            disposition = item.verdict.verdict
            confidence = item.verdict.confidence
        elif item.route == "auto_accept":
            disposition = "auto_accepted"
        elif item.route == "auto_flag":
            disposition = "auto_flagged"
        elif item.route == "prior_review":
            disposition = ("review_approved" if item.prior_review_decision == "approved"
                           else "review_rejected")
        addition_by_npi[item.scored.event.npi] = (disposition, confidence)

    deletion_by_npi = {v.npi: (v.recommended_action, None) for v in deletion_verdicts}
    record_summaries: list[RecordEventSummary] = []
    for event in delta.record_events:
        if event.kind is RecordEventKind.added:
            disposition, confidence = addition_by_npi.get(event.npi, (None, None))
        else:
            disposition, confidence = deletion_by_npi.get(event.npi, (None, None))
        record_summaries.append(RecordEventSummary(
            npi=event.npi,
            kind=event.kind,
            disposition=disposition,
            confidence=confidence,
        ))

    report = EvalReport(
        run_id=make_run_id(),
        comparison_mode=comparison_mode,
        config_fingerprint=config.fingerprint,
        regression_mode=regression.mode,
        baseline_label=run.baseline_label if compete else None,
        candidate_label=run.candidate_label if compete else None,
        volume_summary=delta.volume,
        dedupe_report_baseline=baseline.dedupe_report,
        dedupe_report_candidate=candidate.dedupe_report,
        normalization_report_baseline=baseline.normalization_report,
        normalization_report_candidate=candidate.normalization_report,
        suppressed_by_normalization=delta.suppressed_by_normalization,
        suppressed_by_set_compare=delta.suppressed_by_set_compare,
        total_field_changes=len(delta.field_changes),
        metrics_baseline=metrics_baseline,
        metrics_candidate=metrics_candidate,
        overall_score=overall,
        metrics_included=included,
        metrics_excluded=excluded,
        change_quality=breakdown,
        addition_quality=addition_breakdown,
        deletion_verdicts=deletion_verdicts,
        judge_coverage=breakdown.judge_coverage,
        judge_calls_made=judge_client.calls,
        estimated_judge_cost=round(
            judge_client.calls * metrics_cfg.judge_policy.cost_per_call, 4),
        judge_accuracy=compute_judge_accuracy(
            judged, run.labels_path, len(delta.field_changes), judged_additions),
        competition_summary=competition,
        prod_recommendation=prod_recommendation,
        prod_rationale=prod_rationale,
        field_change_summaries=field_summaries,
        record_event_summaries=record_summaries,
        field_change_summaries_truncated=field_summaries_truncated,
        review_queue=review_queue,
        regression=regression,
        critical_alerts=[a for a in alerts if a.severity == "critical"],
        warnings=[a for a in alerts if a.severity == "warning"],
        release_decision=decision,
    )
    report.top_findings = top_findings(report)

    if run.persist:
        save_report(report, run.output_dir)
    return report
