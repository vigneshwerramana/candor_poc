"""EvalReport assembly, judge meta-QA, markdown rendering, persistence.

The markdown leads with the decision and top findings in plain English —
the audience for the first ten lines is a release manager, not an engineer.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from candor_qa.contracts import (
    Alert,
    EvalReport,
    JudgeAccuracy,
    JudgedAddition,
    JudgedChange,
    ReleaseDecision,
)


def make_run_id() -> str:
    """UTC ISO-8601 + random suffix: lexicographically sortable, collision-free."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


def compute_judge_accuracy(
    judged: list[JudgedChange],
    labels_path: str | Path | None,
    total_field_changes: int,
    judged_additions: list[JudgedAddition] | None = None,
) -> JudgeAccuracy | None:
    """Judge meta-QA vs generator ground truth, when labels.json is available.

    Detection semantics: a planted regression counts as CAUGHT if it ended in
    a suspicious disposition (judged regression, auto-flagged, or uncertain).
    A false-positive trap counts as FLAGGED (bad) if it surfaced as a field
    change at all — traps are supposed to be suppressed by normalization.
    """
    judged_additions = judged_additions or []
    if not labels_path or not Path(labels_path).exists() \
            or not (judged or judged_additions):
        return None   # meta-QA is meaningless when the diff contains no changes
    labels_data = json.loads(Path(labels_path).read_text())
    labels = labels_data.get("field_changes", [])
    truth = {(l["npi"], l["field"]): l["truth_label"] for l in labels}
    planted_regressions = sum(1 for l in labels if l["truth_label"] == "regression")

    addition_truth = labels_data.get("record_events", {}).get("additions", {})
    planted_invalid = sum(1 for t in addition_truth.values()
                          if t == "invalid_addition")
    invalid_caught = 0
    for item in judged_additions:
        if addition_truth.get(item.scored.event.npi) != "invalid_addition":
            continue
        suspicious = (
            item.route in ("structural_reject", "auto_flag")
            or (item.route == "judged"
                and item.verdict.verdict in ("invalid_addition", "uncertain"))
        )
        if suspicious:
            invalid_caught += 1

    tp = fp = fn = 0
    caught = 0
    traps_flagged = 0
    n_labeled_judged = 0
    seen_regression_keys = set()

    for item in judged:
        key = (item.scored.change.npi, item.scored.change.field)
        label = truth.get(key)
        if label == "false_positive_trap":
            traps_flagged += 1     # a trap surfaced as a change at all
            continue
        if label is None:
            continue
        suspicious = (
            item.route == "auto_flag"
            or (item.route == "judged" and item.verdict.verdict in ("regression", "uncertain"))
        )
        if label == "regression" and suspicious and key not in seen_regression_keys:
            caught += 1
            seen_regression_keys.add(key)
        if item.route == "judged":
            n_labeled_judged += 1
            judged_regression = item.verdict.verdict == "regression"
            if judged_regression and label == "regression":
                tp += 1
            elif judged_regression and label == "valid_update":
                fp += 1
            elif not judged_regression and label == "regression":
                fn += 1

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return JudgeAccuracy(
        n_labeled_judged=n_labeled_judged,
        regression_precision=precision,
        regression_recall=recall,
        planted_regressions=planted_regressions,
        planted_regressions_caught=caught,
        traps_flagged=traps_flagged,
        planted_invalid_additions=planted_invalid,
        planted_invalid_additions_caught=invalid_caught,
    )


def top_findings(report: EvalReport) -> list[str]:
    findings: list[str] = []
    for alert in report.critical_alerts[:3]:
        findings.append(alert.message)
    if len(findings) < 3:
        for alert in report.warnings[: 3 - len(findings)]:
            findings.append(alert.message)
    if not findings:
        if report.total_field_changes == 0:
            findings.append("No field changes between snapshots; metrics stable.")
        else:
            cq = report.change_quality
            findings.append(
                f"{report.total_field_changes} field changes; "
                f"{cq.numerator}/{cq.denominator} judged/verified as valid."
            )
    suppressed = report.suppressed_by_normalization + report.suppressed_by_set_compare
    if suppressed:
        findings.append(
            f"{suppressed} format-only differences suppressed by normalization/"
            "set-compare (not counted as changes)."
        )
    return findings[:4]


def render_markdown(report: EvalReport) -> str:
    decision_word = report.release_decision.value
    competition = report.competition_summary
    if competition:
        winner = competition.winner_name()
        if winner:
            title = (f"# Model competition — **{winner} wins** "
                     f"({competition.baseline_label} vs {competition.candidate_label})")
        else:
            title = (f"# Model competition — INCONCLUSIVE "
                     f"({competition.baseline_label} vs {competition.candidate_label})")
    else:
        title = f"# Release evaluation — {decision_word}"
    lines = [
        title,
        "",
        f"**Run** `{report.run_id}` · mode `{report.regression_mode}` · "
        f"comparison `{report.comparison_mode}` · config `{report.config_fingerprint}`",
        "",
    ]
    if competition:
        win = (f"{competition.field_win_rate:.0%}"
               if competition.field_win_rate is not None
               else f"n/a (only {competition.disagreement_count} disagreements, "
                    "min 20 for a verdict)")
        add_q = (f"{competition.addition_quality:.0%}"
                 if competition.addition_quality is not None else "n/a")
        lines += [
            "## Head-to-head",
            f"- Shared providers: {competition.shared_npi_count}; "
            f"definitive disagreements: {competition.disagreement_count}",
            f"- **{competition.candidate_label} wins {win} of disagreements** "
            f"(evidence- and judge-decided); "
            f"regressions in candidate: {competition.regressions_in_candidate}",
            f"- Snapshot health delta (candidate - baseline): "
            f"{competition.snapshot_score_delta:+.1%}",
            f"- Extra coverage: {competition.coverage_delta:+d} records, "
            f"addition quality {add_q}",
            "",
        ]
    lines.append("## Top findings")
    lines += [f"- {finding}" for finding in report.top_findings]

    lines += ["", "## Metric movement (baseline -> candidate)", "",
              "| Metric | Baseline | Candidate | Weight |", "|---|---|---|---|"]
    baseline_map = {m.name: m for m in report.metrics_baseline}
    for metric in report.metrics_candidate:
        base = baseline_map.get(metric.name)
        base_str = f"{base.score:.1%}" if base and base.score is not None else "—"
        cand_str = f"{metric.score:.1%}" if metric.score is not None else "N/A"
        if metric.status == "low_confidence":
            cand_str += f" (low confidence, n={metric.sample_size})"
        lines.append(f"| {metric.name} | {base_str} | {cand_str} | {metric.weight} |")
    overall = f"{report.overall_score:.1%}" if report.overall_score is not None else "N/A"
    lines.append(f"| **overall_score** | | **{overall}** | |")
    if report.metrics_excluded:
        lines.append("")
        lines.append(f"Excluded from score (N/A or report-only): "
                     f"{', '.join(report.metrics_excluded)}")

    cq = report.change_quality
    lines += [
        "", "## Changes",
        f"- Field changes: {report.total_field_changes} "
        f"(suppressed as format noise: {report.suppressed_by_normalization} by "
        f"normalization, {report.suppressed_by_set_compare} by set-compare)",
        f"- Volume: +{report.volume_summary.added_count} added, "
        f"-{report.volume_summary.deleted_count} deleted "
        f"({report.volume_summary.deleted_pct:.1%} of baseline)",
        f"- change_quality: {cq.numerator}/{cq.denominator} valid "
        f"(auto-accepted {cq.auto_accepted}, judge-valid {cq.judged_valid}, "
        f"judge-regression {cq.judged_regression}, human-approved {cq.review_approved})",
        f"- Excluded from denominator: auto-flagged {cq.auto_flagged}, "
        f"uncertain {cq.uncertain}, budget-deferred {cq.budget_deferred}",
        f"- Judge coverage: {report.judge_coverage:.0%} "
        f"({report.judge_calls_made} calls, ~${report.estimated_judge_cost:.2f})",
    ]

    aq = report.addition_quality
    if aq.denominator or aq.auto_flagged or aq.uncertain or aq.budget_deferred:
        lines += [
            "", "## Additions",
            f"- addition_quality: {aq.numerator}/{aq.denominator} valid — "
            f"auto-accepted {aq.auto_accepted}, judge-valid {aq.judged_valid}, "
            f"judge-invalid {aq.judged_invalid}, "
            f"**structurally rejected {aq.structural_rejected}** "
            "(bad NPI checksum / placeholder phone / near-empty)",
            f"- Excluded: auto-flagged {aq.auto_flagged}, uncertain {aq.uncertain}, "
            f"budget-deferred {aq.budget_deferred}",
        ]

    if report.deletion_verdicts:
        suspicious = [v for v in report.deletion_verdicts
                      if v.recommended_action == "review"]
        lines += [
            "", f"## Deletions ({len(report.deletion_verdicts)})",
            f"- {len(suspicious)} deletions of COMPLETE records flagged for review "
            "(possible accidental drops); "
            f"{len(report.deletion_verdicts) - len(suspicious)} look like plausible "
            "retirements (incomplete/stale records)",
        ]

    if report.field_change_summaries:
        flagged_dispositions = {
            "regression",
            "auto_flagged",
            "uncertain",
            "budget_deferred",
            "review_rejected",
        }
        flagged = [
            s for s in report.field_change_summaries
            if (s.disposition or "unrouted") in flagged_dispositions
        ]
        lines += [
            "",
            "## Top flagged field changes (sample)",
            "",
            "| NPI | Field | Old | New | Kind | Disposition | Conf |",
            "|---|---|---|---|---|---|---|",
        ]
        if not flagged:
            lines.append("| — | — | — | — | — | — | — |")
        else:
            for s in flagged[:20]:
                disp = s.disposition or "unrouted"
                conf = f"{s.confidence:.2f}" if s.confidence is not None else "—"
                lines.append(
                    f"| {s.npi} | `{s.field}` | {s.old_value!r} | {s.new_value!r} | "
                    f"{s.change_kind.value} | {disp} | {conf} |"
                )
            if len(flagged) > 20:
                lines.append(f"| … | … | … | … | … | … | … |")
                lines.append(f"\nShowing 20 of {len(flagged)} flagged field changes.")
        if report.field_change_summaries_truncated:
            lines += [
                "",
                f"Note: field change summaries are truncated to "
                f"{len(report.field_change_summaries)} rows for this run; "
                f"totals may be incomplete.",
            ]

        counts: dict[str, dict[str, int]] = {}
        for s in report.field_change_summaries:
            by_kind = counts.setdefault(s.field, {"modified": 0, "filled": 0, "wiped": 0})
            by_kind[s.change_kind.value] = by_kind.get(s.change_kind.value, 0) + 1
        lines += [
            "",
            "## Field change counts by field (sample)",
            "",
            "| Field | modified | filled | wiped | total |",
            "|---|---:|---:|---:|---:|",
        ]
        for field, by_kind in sorted(
            counts.items(),
            key=lambda kv: sum(kv[1].values()),
            reverse=True,
        )[:50]:
            total = sum(by_kind.values())
            lines.append(
                f"| `{field}` | {by_kind.get('modified', 0)} | {by_kind.get('filled', 0)} | "
                f"{by_kind.get('wiped', 0)} | {total} |"
            )
        if len(counts) > 50:
            lines.append(f"\nShowing top 50 fields by change count.")

    if report.judge_accuracy:
        accuracy = report.judge_accuracy
        precision = (f"{accuracy.regression_precision:.0%}"
                     if accuracy.regression_precision is not None else "n/a")
        recall = (f"{accuracy.regression_recall:.0%}"
                  if accuracy.regression_recall is not None else "n/a")
        lines += [
            "", "## Evaluator meta-QA (vs planted ground truth)",
            f"- Planted regressions caught: {accuracy.planted_regressions_caught}"
            f"/{accuracy.planted_regressions}",
            f"- False-positive traps leaked: {accuracy.traps_flagged}",
            f"- Judge precision/recall on regressions: {precision} / {recall} "
            f"(n={accuracy.n_labeled_judged})",
        ]
        if accuracy.planted_invalid_additions:
            lines.append(
                f"- Planted invalid additions caught: "
                f"{accuracy.planted_invalid_additions_caught}"
                f"/{accuracy.planted_invalid_additions}")

    if report.critical_alerts or report.warnings:
        lines += ["", "## Alerts"]
        for alert in report.critical_alerts:
            lines.append(f"- **CRITICAL** {alert.message}")
        for alert in report.warnings:
            lines.append(f"- WARNING {alert.message}")

    lines += ["", f"## Review queue ({len(report.review_queue)} items)"]
    for item in report.review_queue[:10]:
        lines.append(
            f"- {item.npi} `{item.field}`: {item.old_value!r} -> {item.new_value!r} "
            f"({item.change_kind.value}, {item.reason}, conf {item.confidence:.2f})"
        )
    if len(report.review_queue) > 10:
        lines.append(f"- … and {len(report.review_queue) - 10} more")

    if report.regression.compared_run_id:
        lines += ["", f"Compared to run `{report.regression.compared_run_id}`"
                  + (" (CONFIG MISMATCH)" if report.regression.config_mismatch else "")]
    for note in report.regression.notes:
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def save_report(report: EvalReport, output_dir: str | Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    json_path.write_text(report.model_dump_json(indent=2))
    (output_dir / f"{report.run_id}.md").write_text(render_markdown(report))
    return json_path
