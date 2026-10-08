"""Tests for DQI validation on list 2111 fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from candor_qa.validation import ValidationRunConfig, run_validation

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def validation_report():
    candor = FIXTURES / "candor_live" / "raw_page1.json"
    if not candor.exists():
        candor = ROOT / "data" / "candor_live" / "raw_page1.json"
    nppes = FIXTURES / "nppes"
    if not any(nppes.glob("*.json")):
        nppes = ROOT / "data" / "candor_live" / "nppes"
    details = ROOT / "data" / "candor_live" / "details"
    return run_validation(ValidationRunConfig(
        candor_json=str(candor),
        nppes_dir=str(nppes),
        details_dir=str(details) if details.exists() else None,
        persist=False,
    ))


def _findings_for(report, npi: str):
    return [f for f in report.findings if f.npi == npi]


def test_borowka_stale_license(validation_report):
    fs = _findings_for(validation_report, "1083980890")
    dqi2 = [f for f in fs if f.dqi_id == "DQI-2"]
    assert dqi2, "Expected DQI-2 stale license for Borowka"


def test_toth_missing_specialty(validation_report):
    fs = _findings_for(validation_report, "1730748385")
    dqi4 = [f for f in fs if f.dqi_id == "DQI-4"]
    assert dqi4, "Expected DQI-4 missing specialty for Toth"


def test_kinuthia_facility_quality(validation_report):
    fs = _findings_for(validation_report, "1982597399")
    dqi6 = [f for f in fs if f.dqi_id == "DQI-6"]
    assert dqi6, "Expected DQI-6 facility quality for Kinuthia"


def test_dataset_dqi9(validation_report):
    dqi9 = [f for f in validation_report.findings if f.dqi_id == "DQI-9"]
    assert dqi9, "Expected dataset-level DQI-9 findings"


def test_anderson_dqi7_emt_no_license(validation_report):
    fs = _findings_for(validation_report, "1003638248")
    dqi7 = [f for f in fs if f.dqi_id == "DQI-7"]
    assert dqi7, "Expected DQI-7 for Anderson (EMT taxonomy, no license)"
