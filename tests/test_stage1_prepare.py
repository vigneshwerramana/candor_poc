"""Stage 1: safe loading, key hygiene, format normalization."""
import pandas as pd
import pytest

from candor_qa.stage1_prepare.dedupe import dedupe_snapshot
from candor_qa.stage1_prepare.loader import load_snapshot
from candor_qa.stage1_prepare.normalize import (
    canonicalize_nulls,
    normalize_name,
    normalize_phone,
    normalize_snapshot,
    normalize_state,
    normalize_value,
    normalize_zip,
)

CSV = """npi,name,phone,address,zip,state,specialty,insurance_accepted,last_updated,pipeline_version
1000000001,Dr. John Smith,(617) 555-0134,1 Main St,02115,MA,cardiology,Aetna|BlueCross,2026-06-01,v120
1000000001,Dr. John Smith,(617) 555-0134,1 Main St,02115,MA,cardiology,Aetna|BlueCross,2026-06-01,v120
,Dr. Null Key,6175550000,2 Oak Ave,10001,NY,pediatrics,Cigna,2026-06-01,v120
N/A,Dr. Sentinel Key,6175550001,3 Elm St,60601,IL,neurology,Humana,2026-06-01,v120
1000000002,"SMITH, JOHN, MD",617-555-0134,4 Park Blvd,2115,ma,oncology,BlueCross|Aetna,06/01/2026,v120
"""


@pytest.fixture()
def loaded(tmp_path, config):
    path = tmp_path / "snap.csv"
    path.write_text(CSV)
    return load_snapshot(path, config.data_schema)


def test_loader_preserves_strings_and_nulls(loaded):
    # leading-zero zip survives; NPI stays a string; sentinels become NA
    assert loaded.at[0, "zip"] == "02115"
    assert loaded.at[0, "npi"] == "1000000001"
    assert pd.isna(loaded.at[2, "npi"])          # empty string
    assert pd.isna(loaded.at[3, "npi"])          # "N/A" sentinel


def test_dedupe_order_and_buckets(loaded, config):
    deduped, report = dedupe_snapshot(loaded, config.data_schema)
    assert report.raw_count == 5
    assert report.duplicate_npi_count == 1       # NPI ...001 appears twice
    assert report.dropped_duplicate_rows == 1    # keep-first
    assert report.null_npi_count == 2            # "" and "N/A"
    assert len(deduped) == 2
    assert report.duplicate_rate == pytest.approx(1 / 5)


def test_normalizers_single_values():
    assert normalize_phone("(617) 555-0134") == ("6175550134", True)
    assert normalize_phone("1-617-555-0134") == ("6175550134", True)
    assert normalize_phone("call the office") == ("call the office", False)  # kept
    assert normalize_zip("2115") == ("02115", True)
    assert normalize_zip("02115-4801") == ("02115", True)                    # ZIP+4
    assert normalize_state(" ma ") == ("MA", True)
    assert normalize_state("Massachusetts") == ("Massachusetts", False)
    # the name trap: two formats of the same person are equal after normalization
    assert normalize_name("Dr. John Smith")[0] == normalize_name("SMITH, JOHN, MD")[0]


def test_null_canonicalization():
    series = pd.Series(["ok", "", "N/A", "None", "  null  ", None], dtype="string")
    out = canonicalize_nulls(series)
    assert out.notna().tolist() == [True, False, False, False, False, False]


def test_normalize_snapshot_keeps_failures_and_raw(loaded, config):
    deduped, _ = dedupe_snapshot(loaded, config.data_schema)
    frame, report = normalize_snapshot(deduped, config.data_schema)
    # same digits, three input formats -> one canonical form
    assert frame.at[0, "phone"] == frame.at[1, "phone"] == "6175550134"
    # raw values preserved for suppressed-change accounting
    assert frame.at[0, "phone__raw"] == "(617) 555-0134"
    # date reformatted to ISO
    assert frame.at[1, "last_updated"] == "2026-06-01"
    assert report.failure_counts == {}


def test_unparseable_value_kept_and_counted(tmp_path, config):
    path = tmp_path / "bad.csv"
    path.write_text(CSV.replace("(617) 555-0134,1 Main St", "no phone,1 Main St", 1))
    frame, report = normalize_snapshot(
        load_snapshot(path, config.data_schema), config.data_schema)
    assert report.failure_counts.get("phone") == 1
    assert (frame["phone"] == "no phone").any()   # kept, not nulled


def test_normalize_value_for_evidence(config):
    assert normalize_value("(617) 555-0134", "phone", config.data_schema) == "6175550134"
    assert normalize_value("N/A", "phone", config.data_schema) is None
