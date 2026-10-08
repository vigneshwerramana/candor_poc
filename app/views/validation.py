"""DQI validation Streamlit view."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from candor_qa.contracts import ValidationReport

# Full DQI-1..9 catalog — always show every row in the summary table.
DQI_CATALOG: list[tuple[str, str]] = [
    ("DQI-1", "Practice location in a state with no valid license"),
    ("DQI-2", "License status and expiry data is stale"),
    ("DQI-3", "Practice location conflicts across data sources"),
    ("DQI-4", "Specialty / provider information inconsistent"),
    ("DQI-5", "Provider outside Columbus in Columbus OH roster"),
    ("DQI-6", "Facility data quality (PO Box, (0,0), placeholders, missing name)"),
    ("DQI-7", "Missing license/credential for allied-health providers"),
    ("DQI-8", "Recently moved flag without visible re-verification"),
    ("DQI-9", "Attribute completeness and API field consistency"),
]


def render_validation_view(report: ValidationReport) -> None:
    st.subheader("Data Quality Validation (list 2111)")

    score = f"{report.overall_score:.1%}" if report.overall_score is not None else "N/A"
    c1, c2, c3 = st.columns(3)
    c1.metric("Providers", report.provider_count)
    c2.metric("Findings", len(report.findings))
    c3.metric("Overall score", score)

    summary = report.summary_by_dqi or {}
    dqi_rows = [
        {
            "DQI": dqi_id,
            "Issue": issue,
            "Findings": summary.get(dqi_id, 0),
        }
        for dqi_id, issue in DQI_CATALOG
    ]
    st.caption("Findings by DQI (DQI-1 through DQI-9)")
    st.dataframe(
        pd.DataFrame(dqi_rows),
        hide_index=True,
        width="stretch",
    )

    df = pd.DataFrame([f.model_dump() for f in report.findings])
    if df.empty:
        st.info("No findings in this report.")
        return

    f1, f2, f3 = st.columns(3)
    with f1:
        dqi_sel = st.selectbox("DQI", ["All"] + sorted(df["dqi_id"].unique().tolist()))
    with f2:
        sev_sel = st.selectbox("Severity", ["All"] + sorted(df["severity"].unique().tolist()))
    with f3:
        npi_q = st.text_input("NPI contains", value="")

    filtered = df
    if dqi_sel != "All":
        filtered = filtered[filtered["dqi_id"] == dqi_sel]
    if sev_sel != "All":
        filtered = filtered[filtered["severity"] == sev_sel]
    if npi_q.strip():
        filtered = filtered[filtered["npi"].str.contains(npi_q.strip(), na=False)]

    st.caption(f"Showing {len(filtered)} of {len(df)} findings.")
    st.dataframe(
        filtered[["dqi_id", "rule_id", "npi", "provider_name", "severity",
                  "message", "observed", "expected", "source"]],
        hide_index=True,
        width="stretch",
        height=420,
    )

    st.subheader("Known demo providers")
    demo_npis = {
        "1083980890": "Borowka — DQI-2 stale NY license",
        "1225983984": "Davia — DQI-3 location conflict; DQI-7 allied health, no credential",
        "1144157280": "Cousert — DQI-7 allied health, no credential",
        "1003638248": "Anderson — DQI-7 EMT with no license/credential (also DQI-1)",
        "1982597399": "Kinuthia — DQI-6 PO Box / (0,0)",
        "1730748385": "Toth — DQI-4 missing specialty",
    }
    for npi, label in demo_npis.items():
        sub = df[df["npi"] == npi]
        if sub.empty:
            st.markdown(f"- `{npi}` {label}: no finding in this run")
        else:
            findings_text = "; ".join(
                f"{row['dqi_id']}: {row['message']}" for _, row in sub.iterrows()
            )
            st.markdown(f"- **`{npi}`** {label} — {findings_text}")
