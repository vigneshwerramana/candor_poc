"""Record events: extraction from the delta merge, validity flags, Luhn."""
import pandas as pd

from candor_qa.contracts import RecordEventKind
from candor_qa.stage1_prepare.normalize import (
    normalize_snapshot,
    npi_check_digit,
    npi_checksum_ok,
)
from candor_qa.stage2_compare.delta import compute_delta

COLUMNS = ["npi", "name", "phone", "address", "zip", "state", "specialty",
           "insurance_accepted", "last_updated", "pipeline_version"]
VALID_NPI = "1234567893"          # canonical CMS example
OTHER_NPI = "123456788" + npi_check_digit("123456788")

PLACEHOLDERS = {"phone": ["9999999999"]}


def make_frame(rows, config):
    df = pd.DataFrame(rows, columns=COLUMNS).astype("string")
    frame, _ = normalize_snapshot(df, config.data_schema)
    return frame


def row(npi, phone="6175550134", name="Dr. John Smith", address="1 Main St",
        zip_="02115", state="MA", specialty="cardiology",
        insurance="Aetna|BlueCross", updated="2026-06-01", version="v1"):
    return [npi, name, phone, address, zip_, state, specialty, insurance,
            updated, version]


def test_luhn_checksum():
    assert npi_checksum_ok(VALID_NPI)
    assert not npi_checksum_ok("1234567890")     # wrong check digit
    assert not npi_checksum_ok("123456789")      # too short
    assert not npi_checksum_ok(None)


def test_added_and_deleted_events_extracted(config):
    baseline = make_frame([row(VALID_NPI)], config)
    candidate = make_frame([row(OTHER_NPI)], config)
    delta = compute_delta(baseline, candidate, config.data_schema, PLACEHOLDERS)
    by_kind = {e.kind: e for e in delta.record_events}
    assert by_kind[RecordEventKind.added].npi == OTHER_NPI
    assert by_kind[RecordEventKind.deleted].npi == VALID_NPI
    assert by_kind[RecordEventKind.deleted].completeness == 1.0
    assert by_kind[RecordEventKind.added].record["phone"] == "6175550134"


def test_validity_flags(config):
    baseline = make_frame([row(VALID_NPI)], config)
    candidate = make_frame([
        row(VALID_NPI),
        row("1234567890", phone="9999999999"),          # bad luhn + placeholder
        row(OTHER_NPI, phone=None, address=None, zip_=None, state=None,
            specialty=None, insurance=None),            # near-empty
    ], config)
    delta = compute_delta(baseline, candidate, config.data_schema, PLACEHOLDERS)
    events = {e.npi: e for e in delta.record_events}

    junk = events["1234567890"]
    assert not junk.validity_flags["npi_luhn_ok"]
    assert not junk.validity_flags["phone_ok"]          # placeholder
    assert junk.validity_flags["required_ok"]

    ghost = events[OTHER_NPI]
    assert ghost.validity_flags["npi_luhn_ok"]
    assert not ghost.validity_flags["required_ok"]      # 1/7 fields
    assert ghost.completeness < 0.5
