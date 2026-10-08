"""Tests for claims-to-network checker (Q1–Q6 assignment scenarios)."""
from __future__ import annotations

from pathlib import Path

import pytest

from candor_qa.claims import ClaimsRunConfig, run_claims_check

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def claims_report():
    details = FIXTURES / "candor_details"
    if not details.exists():
        details = ROOT / "data" / "candor_live" / "details"
    return run_claims_check(ClaimsRunConfig(
        claims_path=str(FIXTURES / "claims_sample.csv"),
        nppes_dir=str(FIXTURES / "nppes"),
        candor_details_dir=str(details) if details.exists() else None,
        exhibit_a_path=str(ROOT / "config" / "exhibit_a.yaml"),
        persist=False,
    ))


def _by_npi(report, npi: str):
    return [r for r in report.results if r.npi == npi]


def test_q1_sullivan_mapping_gap(claims_report):
    rows = _by_npi(claims_report, "1407146699")
    assert len(rows) == 1
    assert rows[0].mapped_network == "UNMAPPED"
    assert rows[0].diagnosis == "mapping_gap"


def test_q2_lamb_address_mismatch(claims_report):
    rows = _by_npi(claims_report, "1093191249")
    assert rows[0].mapped_network == "Preferred"
    assert rows[0].address_match == "NO_MATCH"
    assert rows[0].diagnosis == "address_mismatch"


def test_q3_uddin_wrong_agreement(claims_report):
    rows = _by_npi(claims_report, "1407275217")
    assert rows[0].mapped_network == "Bill"
    assert rows[0].diagnosis == "wrong_agreement_type"


def test_q4_porter_mapping_gap(claims_report):
    rows = _by_npi(claims_report, "1891140760")
    assert rows[0].mapped_network == "UNMAPPED"
    assert rows[0].diagnosis == "mapping_gap"


def test_q5_thai_address_mismatch(claims_report):
    rows = _by_npi(claims_report, "1114993748")
    assert rows[0].mapped_network == "Preferred"
    assert rows[0].address_match == "NO_MATCH"


def test_q6_galatz_matching_bug(claims_report):
    rows = _by_npi(claims_report, "1558387852")
    assert rows[0].mapped_network == "Preferred"
    assert rows[0].address_match == "MATCH"
    assert rows[0].diagnosis == "likely_matching_bug"
