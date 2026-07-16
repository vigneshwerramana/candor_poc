"""Per-snapshot health metrics, computed on BOTH snapshots so the report can
show movement (completeness 96% -> 97%). All vectorized column operations.

Gaming guards baked in:
- phone_validity counts placeholder values (999-999-9999) as INVALID, so
  completeness cannot be gamed by placeholder-filling.
- duplicate_rate comes from the RAW snapshot (before dedupe).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from candor_qa.config import MetricsConfig, SchemaConfig, ZipPrefixIndex
from candor_qa.contracts import DedupeReport, MetricResult, NormalizationReport


def _result(name: str, score: float | None, cfg: MetricsConfig,
            sample_size: int | None = None) -> MetricResult:
    spec = cfg.metrics.get(name)
    return MetricResult(
        name=name,
        score=score,
        weight=spec.weight if spec else 0.0,
        direction=spec.direction if spec else "higher_is_better",
        status="ok" if score is not None else "n/a",
        sample_size=sample_size,
    )


def compute_snapshot_metrics(
    frame: pd.DataFrame,
    dedupe_report: DedupeReport,
    normalization_report: NormalizationReport,
    schema: SchemaConfig,
    cfg: MetricsConfig,
    reference_date: date | None = None,
    zip_prefixes: ZipPrefixIndex | None = None,
) -> list[MetricResult]:
    n = len(frame)
    reference_date = reference_date or date.today()
    value_fields = [f for f in schema.diff_fields]   # fields that carry data

    if n == 0:
        return [_result(name, None, cfg) for name in cfg.metrics]

    results: list[MetricResult] = []

    # completeness: mean non-null share across data-carrying fields
    non_null = sum(frame[f].notna().sum() for f in value_fields)
    results.append(_result("completeness", non_null / (n * len(value_fields)), cfg, n))

    # phone_validity: valid 10-digit AND not a placeholder, over ALL rows
    # (a wiped phone hurts validity — that is intentional)
    placeholders = set(cfg.placeholder_values.get("phone", []))
    phone = frame["phone"]
    valid_phone = phone.notna() & phone.str.fullmatch(r"\d{10}").fillna(False) \
        & ~phone.isin(placeholders)
    results.append(_result("phone_validity", valid_phone.sum() / n, cfg, n))

    # npi_validity (report-only): 10-digit key
    npi = frame[schema.key_field]
    valid_npi = npi.notna() & npi.str.fullmatch(r"\d{10}").fillna(False)
    results.append(_result("npi_validity", valid_npi.sum() / n, cfg, n))

    # duplicate_rate: surplus duplicate rows over raw rows (pre-dedupe)
    results.append(_result("duplicate_rate", dedupe_report.duplicate_rate, cfg,
                           dedupe_report.raw_count))

    # address_completeness (report-only): address AND zip AND state present
    addr_ok = frame["address"].notna() & frame["zip"].notna() & frame["state"].notna()
    results.append(_result("address_completeness", addr_ok.sum() / n, cfg, n))

    # freshness: last_updated within the window
    cutoff = (reference_date - timedelta(days=cfg.freshness_window_days)).isoformat()
    updated = frame["last_updated"]
    fresh = updated.notna() & (updated >= cutoff)    # ISO strings compare lexicographically
    results.append(_result("freshness", fresh.sum() / n, cfg, n))

    # state_zip_consistency: does the 3-digit ZIP prefix belong to the state?
    # Cross-column consistency — a valid zip next to a valid state can still
    # be a contradiction. Unknown prefixes and rows missing either value are
    # excluded from the denominator (that's completeness's job, not this one's).
    if zip_prefixes is not None:
        both = frame["zip"].notna() & frame["state"].notna()
        prefix = frame["zip"].str[:3]
        state_sets = prefix.map(zip_prefixes.states_for)
        known = both & (state_sets.map(len) > 0)
        matches = pd.Series(
            [s in states for s, states in zip(frame["state"], state_sets)],
            index=frame.index)
        consistent = known & matches
        known_count = int(known.sum())
        score = consistent.sum() / known_count if known_count else None
        results.append(_result("state_zip_consistency", score, cfg, known_count))
    else:
        results.append(_result("state_zip_consistency", None, cfg))

    # normalization_failure_rate (report-only)
    results.append(_result("normalization_failure_rate",
                           normalization_report.failure_rate, cfg, n))

    return results
