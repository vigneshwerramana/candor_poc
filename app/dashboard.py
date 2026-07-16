"""Decision-first quality dashboard.

    .venv/bin/streamlit run app/dashboard.py

Reads reports from data/evaluation_history/; approve/reject buttons on the
review queue write data/reviews.json, which the next evaluation run consults.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st
import pandas as pd

try:
    import altair as alt
except ModuleNotFoundError:  # pragma: no cover
    alt = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from candor_qa.contracts import EvalReport, ReviewItem  # noqa: E402
from candor_qa.stage5_decide.review_queue import append_review, load_prior_reviews  # noqa: E402

HISTORY_DIR = ROOT / "data" / "evaluation_history"
REVIEWS_PATH = ROOT / "data" / "reviews.json"

DECISION_STYLE = {
    "APPROVE": ("✅", "green"),
    "BASELINE_ESTABLISHED": ("📌", "blue"),
    "REVIEW": ("🟡", "orange"),
    "REJECT": ("🛑", "red"),
}

st.set_page_config(page_title="Candor QA", layout="wide")
st.title("Provider Data QA — Release Evaluation")

runs = sorted(HISTORY_DIR.rglob("*.json"), key=lambda p: p.stem, reverse=True)
if not runs:
    st.info("No evaluation runs yet. Run `python scripts/run_evaluation.py` first.")
    st.stop()

selected = st.sidebar.selectbox(
    "Run", runs, format_func=lambda p: f"{p.parent.name}/{p.stem}")
report = EvalReport.model_validate_json(selected.read_text())

# ---- Decision banner -------------------------------------------------------
icon, color = DECISION_STYLE[report.release_decision.value]
if report.competition_summary:
    comp = report.competition_summary
    winner = comp.winner_name()
    if winner:
        verdict = f"{winner} wins"
        headline_color = "green" if comp.recommendation == "candidate_wins" else "orange"
    else:
        verdict = "Inconclusive — not enough disagreements"
        headline_color = "orange"
    st.markdown(f"## 🏁 :{headline_color}[{verdict}]")
    if report.prod_recommendation:
        st.success(report.prod_recommendation)
        for bullet in (report.prod_rationale or [])[:3]:
            st.caption(f"- {bullet}")
    st.caption(
        f"Baseline: **{comp.baseline_label}** (`--baseline` file)  ·  "
        f"Candidate: **{comp.candidate_label}** (`--candidate` file)"
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Disagreements won",
              f"{comp.field_win_rate:.0%}" if comp.field_win_rate is not None
              else f"n/a (n={comp.disagreement_count})",
              help="Share of evidence/judge-decided disagreements won by the candidate")
    c2.metric("Snapshot health Δ", f"{comp.snapshot_score_delta:+.1%}")
    c3.metric("Extra coverage", f"{comp.coverage_delta:+d} records")
    c4.metric("Addition quality",
              f"{comp.addition_quality:.0%}" if comp.addition_quality is not None else "n/a")
else:
    st.markdown(f"## {icon} :{color}[{report.release_decision.value}]")
st.caption(
    f"Run `{report.run_id}` · regression mode `{report.regression_mode}` · "
    f"comparison `{report.comparison_mode}` · config `{report.config_fingerprint}`"
)
for finding in report.top_findings:
    st.markdown(f"- {finding}")

# ---- At-a-glance charts (comparison-first; trend is gated) -------------------
st.subheader("At-a-glance")

baseline_map = {m.name: m for m in report.metrics_baseline}
weighted = [m for m in report.metrics_candidate if m.weight > 0]

c_left, c_mid, c_right = st.columns(3)

with c_left:
    st.caption("Baseline vs candidate (snapshot metrics)")
    metric_rows = []
    for metric in weighted:
        base = baseline_map.get(metric.name)
        if not base or base.score is None or metric.score is None:
            continue
        metric_rows.append({"metric": metric.name, "dataset": "baseline", "score": base.score})
        metric_rows.append({"metric": metric.name, "dataset": "candidate", "score": metric.score})
    metric_df = pd.DataFrame(metric_rows)
    if metric_df.empty:
        st.info("No comparable baseline/candidate metrics in this run.")
    elif alt is None:
        pivot = metric_df.pivot(index="metric", columns="dataset", values="score")
        st.bar_chart(pivot, height=220)
    else:
        metric_order = [m for m in metric_df["metric"].unique().tolist()]
        chart = (
            alt.Chart(metric_df)
            .mark_bar()
            .encode(
                x=alt.X("metric:N", sort=metric_order, title=None),
                y=alt.Y("score:Q", title="Score", axis=alt.Axis(format="%")),
                color=alt.Color("dataset:N", title=None),
                tooltip=[
                    alt.Tooltip("metric:N"),
                    alt.Tooltip("dataset:N"),
                    alt.Tooltip("score:Q", format=".1%"),
                ],
            )
        ).properties(height=220)
        st.altair_chart(chart, width='stretch')

with c_mid:
    st.caption("Change outcomes (counts)")
    cq = report.change_quality
    outcome_rows = [
        {"outcome": "auto_accepted", "count": cq.auto_accepted},
        {"outcome": "judge_valid", "count": cq.judged_valid},
        {"outcome": "judge_regression", "count": cq.judged_regression},
        {"outcome": "prior_review_approved", "count": cq.review_approved},
        {"outcome": "prior_review_rejected", "count": cq.review_rejected},
        {"outcome": "auto_flagged", "count": cq.auto_flagged},
        {"outcome": "uncertain", "count": cq.uncertain},
        {"outcome": "budget_deferred", "count": cq.budget_deferred},
    ]
    outcome_df = pd.DataFrame([r for r in outcome_rows if r["count"] > 0])
    if outcome_df.empty:
        st.info("No change outcomes to chart for this run.")
    elif alt is None:
        st.bar_chart(outcome_df.set_index("outcome"), height=220)
    else:
        order = [r["outcome"] for r in outcome_rows]
        chart = (
            alt.Chart(outcome_df)
            .mark_bar()
            .encode(
                x=alt.X("outcome:N", sort=order, title=None),
                y=alt.Y("count:Q", title="Count"),
                tooltip=[alt.Tooltip("outcome:N"), alt.Tooltip("count:Q")],
            )
        ).properties(height=220)
        st.altair_chart(chart, width='stretch')

with c_right:
    st.caption("Volume (record churn)")
    v = report.volume_summary
    volume_df = pd.DataFrame(
        [
            {"kind": "added", "count": v.added_count},
            {"kind": "updated", "count": v.updated_count},
            {"kind": "deleted", "count": v.deleted_count},
            {"kind": "unchanged", "count": v.unchanged_count},
        ]
    )
    if alt is None:
        st.bar_chart(volume_df.set_index("kind"), height=220)
    else:
        chart = (
            alt.Chart(volume_df)
            .mark_bar()
            .encode(
                x=alt.X("kind:N", sort=["added", "updated", "deleted", "unchanged"], title=None),
                y=alt.Y("count:Q", title="Count"),
                tooltip=[alt.Tooltip("kind:N"), alt.Tooltip("count:Q")],
            )
        ).properties(height=220)
        st.altair_chart(chart, width='stretch')

with st.expander("History (optional)"):
    if report.comparison_mode == "model_compete":
        st.caption("Trend is hidden for model competition runs.")
    else:
        trend = []
        for path in runs[::-1]:
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if data.get("config_fingerprint") != report.config_fingerprint:
                continue
            if data.get("comparison_mode") != report.comparison_mode:
                continue
            if data.get("overall_score") is None:
                continue
            label = data.get("run_id", "")[:15]
            if data.get("baseline_label") and data.get("candidate_label"):
                label = f"{data['baseline_label']}→{data['candidate_label']}"
            trend.append(
                {
                    "label": label,
                    "overall_score": data["overall_score"],
                    "decision": data.get("release_decision", "unknown"),
                }
            )
        if len(trend) < 5:
            st.caption(
                f"Not shown (need 5+ runs with the same config and comparison mode; found {len(trend)})."
            )
        else:
            df = pd.DataFrame(trend)
            y_min = max(0.0, float(df["overall_score"].min()) - 0.05)
            y_max = min(1.0, float(df["overall_score"].max()) + 0.05)
            if alt is None:
                st.line_chart(df.set_index("label")[["overall_score"]], height=180)
            else:
                chart = (
                    alt.Chart(df)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("label:N", title=None, sort=None),
                        y=alt.Y("overall_score:Q", title="overall_score", scale=alt.Scale(domain=[y_min, y_max]),
                                axis=alt.Axis(format="%")),
                        color=alt.Color("decision:N", title=None),
                        tooltip=[
                            alt.Tooltip("label:N"),
                            alt.Tooltip("decision:N"),
                            alt.Tooltip("overall_score:Q", format=".1%"),
                        ],
                    )
                ).properties(height=180)
                st.altair_chart(chart, width='stretch')

# ---- Metric movement --------------------------------------------------------
st.subheader("Metric movement (baseline → candidate)")
columns = st.columns(min(5, max(1, len(report.metrics_included))))
for i, metric in enumerate(weighted):
    base = baseline_map.get(metric.name)
    with columns[i % len(columns)]:
        if metric.score is None:
            st.metric(metric.name, "N/A")
        else:
            delta = (metric.score - base.score) if base and base.score is not None else None
            inverse = metric.direction == "lower_is_better"
            st.metric(
                metric.name, f"{metric.score:.1%}",
                delta=f"{delta:+.1%}" if delta is not None else None,
                delta_color=("inverse" if inverse else "normal"),
            )
overall = f"{report.overall_score:.1%}" if report.overall_score is not None else "N/A"
st.metric("overall_score (weighted, renormalized on N/A)", overall)

# ---- Alerts ------------------------------------------------------------------
if report.critical_alerts or report.warnings:
    st.subheader("Alerts")
    for alert in report.critical_alerts:
        st.error(alert.message, icon="🛑")
    for alert in report.warnings:
        st.warning(alert.message, icon="⚠️")

# ---- Changes + evaluator meta-QA ---------------------------------------------
left, right = st.columns(2)
with left:
    st.subheader("Changes")
    cq = report.change_quality
    volume = report.volume_summary
    st.markdown(
        f"- **{report.total_field_changes} field changes** "
        f"({report.suppressed_by_normalization} suppressed by normalization, "
        f"{report.suppressed_by_set_compare} by set-compare)\n"
        f"- Volume: +{volume.added_count} / −{volume.deleted_count} rows "
        f"({volume.deleted_pct:.1%} deleted)\n"
        f"- change_quality: **{cq.numerator}/{cq.denominator}** valid — "
        f"auto-accepted {cq.auto_accepted}, judge-valid {cq.judged_valid}, "
        f"judge-regression {cq.judged_regression}\n"
        f"- Excluded: auto-flagged {cq.auto_flagged}, uncertain {cq.uncertain}, "
        f"budget-deferred {cq.budget_deferred} · judge coverage "
        f"{report.judge_coverage:.0%} ({report.judge_calls_made} calls, "
        f"~${report.estimated_judge_cost:.2f})"
    )
    aq = report.addition_quality
    if aq.denominator:
        st.markdown(
            f"- addition_quality: **{aq.numerator}/{aq.denominator}** valid — "
            f"auto-accepted {aq.auto_accepted}, judge-valid {aq.judged_valid}, "
            f"structurally rejected {aq.structural_rejected}"
        )
    if report.deletion_verdicts:
        suspicious = sum(1 for v in report.deletion_verdicts
                         if v.recommended_action == "review")
        st.markdown(
            f"- deletions: {len(report.deletion_verdicts)} total, "
            f"**{suspicious} flagged as possible accidental drops**"
        )
with right:
    if report.judge_accuracy:
        st.subheader("Evaluator meta-QA (vs planted ground truth)")
        accuracy = report.judge_accuracy
        st.markdown(
            f"- Planted regressions caught: "
            f"**{accuracy.planted_regressions_caught}/{accuracy.planted_regressions}**\n"
            f"- False-positive traps leaked: **{accuracy.traps_flagged}**\n"
            f"- Judge precision / recall: "
            f"{accuracy.regression_precision:.0%} / {accuracy.regression_recall:.0%} "
            f"(n={accuracy.n_labeled_judged})"
            if accuracy.regression_precision is not None else
            f"- Planted regressions caught: "
            f"{accuracy.planted_regressions_caught}/{accuracy.planted_regressions}"
        )

# ---- Delta Explorer ----------------------------------------------------------
st.subheader("Delta Explorer")
if not report.field_change_summaries:
    st.info(
        "This report has no persisted field-level delta summaries. "
        "Generate a fresh report via `scripts/run_evaluation.py` to populate them "
        "(running `pytest` does not create files in `data/evaluation_history/`)."
    )
else:
    df = pd.DataFrame(
        [
            {
                "npi": s.npi,
                "field": s.field,
                "old_value": repr(s.old_value),
                "new_value": repr(s.new_value),
                "change_kind": s.change_kind.value,
                "disposition": s.disposition or "unrouted",
                "confidence": s.confidence,
            }
            for s in report.field_change_summaries
        ]
    )
    if report.field_change_summaries_truncated:
        st.warning(
            f"Showing only the first {len(report.field_change_summaries)} field changes "
            f"(truncated; total changes in run: {report.total_field_changes})."
        )
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        npi_q = st.text_input("NPI contains", value="")
    with f2:
        fields = ["All"] + sorted(df["field"].unique().tolist())
        field_sel = st.selectbox("Field", fields)
    with f3:
        kinds = ["All"] + sorted(df["change_kind"].unique().tolist())
        kind_sel = st.selectbox("Change kind", kinds)
    with f4:
        dispositions = ["All"] + sorted(df["disposition"].unique().tolist())
        disp_sel = st.selectbox("Disposition", dispositions)

    filtered = df
    if npi_q.strip():
        filtered = filtered[filtered["npi"].str.contains(npi_q.strip(), na=False)]
    if field_sel != "All":
        filtered = filtered[filtered["field"] == field_sel]
    if kind_sel != "All":
        filtered = filtered[filtered["change_kind"] == kind_sel]
    if disp_sel != "All":
        filtered = filtered[filtered["disposition"] == disp_sel]

    st.caption(f"Showing {len(filtered)} of {len(df)} persisted field changes.")
    st.dataframe(
        filtered,
        width='stretch',
        hide_index=True,
        height=360,
        column_config={
            "confidence": st.column_config.NumberColumn(format="%.2f"),
        },
    )

if report.record_event_summaries:
    st.caption(f"Record events: {len(report.record_event_summaries)}")
    events_df = pd.DataFrame(
        [
            {
                "npi": e.npi,
                "kind": e.kind.value,
                "disposition": e.disposition or "unrouted",
                "confidence": e.confidence,
            }
            for e in report.record_event_summaries
        ]
    )
    st.dataframe(
        events_df,
        width='stretch',
        hide_index=True,
        height=180,
        column_config={
            "confidence": st.column_config.NumberColumn(format="%.2f"),
        },
    )

# ---- Review queue with a real lifecycle ---------------------------------------
st.subheader(f"Human review queue ({len(report.review_queue)})")
already_decided = load_prior_reviews(REVIEWS_PATH)
for i, item in enumerate(report.review_queue):
    key = (item.npi, item.field, str(item.old_value), str(item.new_value))
    decided = already_decided.get(key)
    text = (f"`{item.npi}` **{item.field}**: {item.old_value!r} → "
            f"{item.new_value!r}  ({item.change_kind.value}, {item.reason}, "
            f"confidence {item.confidence:.2f})")
    if decided:
        st.markdown(f"~~{text}~~ — **{decided}**")
        continue
    col_text, col_ok, col_no = st.columns([6, 1, 1])
    col_text.markdown(text)
    if col_ok.button("Approve", key=f"approve_{i}"):
        append_review(REVIEWS_PATH, item, "approved")
        st.rerun()
    if col_no.button("Reject", key=f"reject_{i}"):
        append_review(REVIEWS_PATH, item, "rejected")
        st.rerun()

if report.regression.compared_run_id:
    st.caption(f"Compared to run `{report.regression.compared_run_id}`"
               + (" — CONFIG MISMATCH" if report.regression.config_mismatch else ""))
