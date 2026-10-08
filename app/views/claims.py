"""Claims Explorer Streamlit view."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from candor_qa.claims.exhibit_a import UNMAPPED
from candor_qa.contracts import ClaimsReport

ROOT = Path(__file__).resolve().parent.parent.parent

DISPLAY_COLS = [
    "npi", "provider_name", "contract", "agreement_type",
    "service_city", "service_state", "mapped_network",
    "address_match", "diagnosis", "explanation", "reference_available",
]


def render_claims_view(report: ClaimsReport) -> None:
    st.subheader("Claims-to-Network Explorer")

    exhibit_path = ROOT / "config" / "exhibit_a.yaml"
    if exhibit_path.exists():
        with st.expander("Exhibit A — network mappings"):
            mappings = yaml.safe_load(exhibit_path.read_text()).get("mappings", [])
            st.dataframe(pd.DataFrame(mappings), hide_index=True, width="stretch")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total claims", report.total_claims)
    c2.metric("Exact matches", report.exact_match_count)
    c3.metric("Map + address match", report.mapping_and_address_match_count)
    c4.metric("Flagged", report.flagged_count)
    c5.metric("Unmapped", report.unmapped_count)

    if report.cache_coverage:
        cc = report.cache_coverage
        st.subheader("Reference data coverage")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Unique NPIs", cc.unique_npis)
        r2.metric("NPPES cached", f"{cc.nppes_cached}/{cc.unique_npis}")
        r3.metric("Candor detail cached", f"{cc.candor_detail_cached}/{cc.unique_npis}")
        r4.metric("Claims with reference", f"{cc.claims_with_reference}/{report.total_claims}")
        if cc.nppes_cached < cc.unique_npis:
            st.warning(
                f"Only **{cc.nppes_cached} of {cc.unique_npis}** claim NPIs have NPPES cached under "
                f"`data/candor_live/nppes/`. Claims without reference data show "
                f"`reference_missing` (not verified), not `address_mismatch`."
            )

    df = pd.DataFrame([r.model_dump() for r in report.results])
    if df.empty:
        st.info("No claim results in this report.")
        return

    st.caption("Summary by diagnosis")

    items = sorted(report.summary_by_diagnosis.items())
    cols = st.columns(len(items))

    for col, (code, count) in zip(cols, items):
        with col:
            st.metric(label=code, value=count)

    st.subheader("Exact matches")
    st.caption(
        "Exhibit A maps to a network, service address matches NPPES/Candor, "
        "and agreement type is consistent (`diagnosis = ok`)."
    )
    exact = df[df["diagnosis"] == "ok"]
    if exact.empty:
        st.info("No exact matches in this run — usually because NPPES/Candor detail is not cached for most NPIs.")
    else:
        st.dataframe(exact[DISPLAY_COLS], hide_index=True, width="stretch", height=280)

    st.subheader("Mapping + address match")
    st.caption(
        "Exhibit A resolved and address verified against reference data. "
        "May still have other issues (e.g. wrong agreement type, likely matching bug)."
    )
    partial = df[
        (df["mapped_network"] != UNMAPPED) & (df["address_match"] == "MATCH")
    ]
    if partial.empty:
        st.info("No claims with both network mapping and address match.")
    else:
        st.dataframe(partial[DISPLAY_COLS], hide_index=True, width="stretch", height=280)

    st.subheader("Assignment providers (Q1–Q6)")
    assignment = df[df["npi"].isin(report.assignment_npis)].copy()
    if assignment.empty:
        st.warning("No rows for the six assignment NPIs in this run.")
    else:
        st.dataframe(assignment[DISPLAY_COLS], hide_index=True, width="stretch")

        bugs = assignment[assignment["diagnosis"] == "likely_matching_bug"]
        if not bugs.empty:
            st.error(
                f"**Likely matching bug:** {bugs.iloc[0]['npi']} "
                f"({bugs.iloc[0]['provider_name']}) — mapping and address pass but provider absent."
            )

    st.subheader("Flagged claims")
    flagged = df[df["flagged"]]
    f_npi = st.text_input("Filter NPI", value="", key="claims_npi_filter")
    f_diag = st.selectbox(
        "Diagnosis",
        ["All"] + sorted(df["diagnosis"].unique().tolist()),
        key="claims_diag_filter",
    )
    filtered = flagged
    if f_npi.strip():
        filtered = filtered[filtered["npi"].str.contains(f_npi.strip(), na=False)]
    if f_diag != "All":
        filtered = filtered[filtered["diagnosis"] == f_diag]
    st.caption(f"Showing {len(filtered)} of {len(flagged)} flagged claims.")
    st.dataframe(filtered[DISPLAY_COLS], hide_index=True, width="stretch", height=400)

    unverified = df[df["diagnosis"] == "reference_missing"]
    if not unverified.empty:
        with st.expander(f"Unverified claims — no local reference ({len(unverified)})"):
            st.caption("These need NPPES/Candor detail fetched before address can be checked.")
            st.dataframe(unverified[DISPLAY_COLS].head(50), hide_index=True, width="stretch")
