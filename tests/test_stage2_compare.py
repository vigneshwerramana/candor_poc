"""Stage 2: delta semantics and snapshot metrics."""
from datetime import date

import pandas as pd
import pytest

from candor_qa.contracts import ChangeKind, DedupeReport, NormalizationReport
from candor_qa.stage1_prepare.normalize import normalize_snapshot
from candor_qa.stage2_compare.delta import compute_delta
from candor_qa.stage2_compare.snapshot_metrics import compute_snapshot_metrics

COLUMNS = ["npi", "name", "phone", "address", "zip", "state", "specialty",
           "insurance_accepted", "last_updated", "pipeline_version"]


def make_frame(rows, config):
    df = pd.DataFrame(rows, columns=COLUMNS).astype("string")
    frame, _ = normalize_snapshot(df, config.data_schema)
    return frame


def row(npi, phone="6175550134", name="Dr. John Smith", address="1 Main St",
        zip_="02115", state="MA", specialty="cardiology",
        insurance="Aetna|BlueCross", updated="2026-06-01", version="v120"):
    return [npi, name, phone, address, zip_, state, specialty, insurance,
            updated, version]


@pytest.fixture()
def baseline(config):
    return make_frame([row("1"), row("2"), row("3")], config)


def test_taxonomy_wiped_filled_modified(config, baseline):
    candidate = make_frame([
        row("1", phone=None),                 # wiped — the NA-safety case
        row("2", phone="2125550199"),         # modified
        row("3", specialty=None),             # wiped (different field)
    ], config)
    delta = compute_delta(baseline, candidate, config.data_schema)
    kinds = {(c.npi, c.field): c.change_kind for c in delta.field_changes}
    assert kinds[("1", "phone")] is ChangeKind.wiped
    assert kinds[("2", "phone")] is ChangeKind.modified
    assert kinds[("3", "specialty")] is ChangeKind.wiped
    assert len(delta.field_changes) == 3

    # symmetric: filled
    delta_reverse = compute_delta(candidate, baseline, config.data_schema)
    kinds = {(c.npi, c.field): c.change_kind for c in delta_reverse.field_changes}
    assert kinds[("1", "phone")] is ChangeKind.filled


def test_format_noise_suppressed_not_flagged(config, baseline):
    candidate = make_frame([
        row("1", phone="(617) 555-0134"),             # same digits, new format
        row("2", insurance="BlueCross|Aetna"),        # order shuffle, same set
        row("3", name="SMITH, JOHN, MD"),             # name reformat
    ], config)
    delta = compute_delta(baseline, candidate, config.data_schema)
    assert delta.field_changes == []
    assert delta.suppressed_by_normalization == 2     # phone + name
    assert delta.suppressed_by_set_compare == 1       # insurance order


def test_set_membership_change_is_flagged(config, baseline):
    candidate = make_frame([
        row("1", insurance="Aetna|BlueCross|Cigna"),  # genuine addition
        row("2"), row("3"),
    ], config)
    delta = compute_delta(baseline, candidate, config.data_schema)
    assert [c.field for c in delta.field_changes] == ["insurance_accepted"]


def test_diff_false_fields_never_flood_delta(config, baseline):
    # every record's last_updated + pipeline_version changed: zero field changes
    candidate = make_frame([
        row("1", updated="2026-07-01", version="v121"),
        row("2", updated="2026-07-01", version="v121"),
        row("3", updated="2026-07-01", version="v121"),
    ], config)
    delta = compute_delta(baseline, candidate, config.data_schema)
    assert delta.field_changes == []
    assert delta.volume.unchanged_count == 3


def test_volume_summary(config, baseline):
    candidate = make_frame([row("1"), row("2"), row("4"), row("5")], config)
    volume = compute_delta(baseline, candidate, config.data_schema).volume
    assert volume.added_count == 2
    assert volume.deleted_count == 1
    assert volume.deleted_pct == pytest.approx(1 / 3)
    assert volume.added_pct == pytest.approx(2 / 3)


def test_placeholder_phone_counts_as_invalid(config):
    frame = make_frame([row("1", phone="9999999999"), row("2")], config)
    metrics = compute_snapshot_metrics(
        frame, DedupeReport(raw_count=2, duplicate_npi_count=0,
                            dropped_duplicate_rows=0, null_npi_count=0),
        NormalizationReport(row_count=2), config.data_schema, config.metrics,
        reference_date=date(2026, 7, 13))
    by_name = {m.name: m for m in metrics}
    assert by_name["phone_validity"].score == pytest.approx(0.5)
    assert by_name["completeness"].score == 1.0   # gaming guard: full but invalid


def test_state_zip_consistency(config):
    frame = make_frame([
        row("1"),                                  # 02115 + MA: consistent
        row("2", zip_="02115", state="TX"),        # known prefix, wrong state
        row("3", zip_="00000", state="NY"),        # unknown prefix: excluded
        row("4", zip_=None, state="MA"),           # missing zip: excluded
    ], config)
    metrics = compute_snapshot_metrics(
        frame, DedupeReport(raw_count=4, duplicate_npi_count=0,
                            dropped_duplicate_rows=0, null_npi_count=0),
        NormalizationReport(row_count=4), config.data_schema, config.metrics,
        reference_date=date(2026, 7, 13), zip_prefixes=config.zip_prefixes)
    metric = next(m for m in metrics if m.name == "state_zip_consistency")
    assert metric.score == pytest.approx(0.5)      # 1 of 2 known-prefix rows
    assert metric.sample_size == 2                 # unknown/missing excluded
    assert metric.weight == 0.05


def test_freshness_window(config):
    frame = make_frame([
        row("1", updated="2026-07-01"),   # fresh
        row("2", updated="2025-06-01"),   # stale (>180 days before ref)
        row("3", updated=None),           # missing counts stale
    ], config)
    metrics = compute_snapshot_metrics(
        frame, DedupeReport(raw_count=3, duplicate_npi_count=0,
                            dropped_duplicate_rows=0, null_npi_count=0),
        NormalizationReport(row_count=3), config.data_schema, config.metrics,
        reference_date=date(2026, 7, 13))
    by_name = {m.name: m for m in metrics}
    assert by_name["freshness"].score == pytest.approx(1 / 3)
