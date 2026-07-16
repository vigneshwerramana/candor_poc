import json

from candor_qa.contracts import EvalReport
from candor_qa.pipeline import RunConfig, run_evaluation
from candor_qa.stage1_prepare.normalize import npi_check_digit


def _make_valid_npi(i: int) -> str:
    base9 = f"{100_000_000 + i:09d}"
    return base9 + npi_check_digit(base9)


def test_field_change_summary_cap_truncates(tmp_path):
    baseline_path = tmp_path / "baseline.csv"
    candidate_path = tmp_path / "candidate.csv"

    rows = []
    for i in range(501):
        npi = _make_valid_npi(i)
        rows.append(
            {
                "npi": npi,
                "name": f"Dr Example {i}",
                "phone": "5555555555",
                "address": "1 Main St",
                "zip": "12345",
                "state": "CA",
                "specialty": "cardiology",
                "insurance_accepted": "aetna|bluecross",
                "last_updated": "2026-01-01",
                "pipeline_version": "v1",
            }
        )
    # Candidate differs on one diff field for every row -> 501 field changes.
    cand_rows = [dict(r, specialty="dermatology") for r in rows]

    import pandas as pd

    pd.DataFrame(rows).to_csv(baseline_path, index=False)
    pd.DataFrame(cand_rows).to_csv(candidate_path, index=False)

    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps({"field_changes": [], "record_events": {"additions": {}}}))

    run = RunConfig(
        baseline_path=str(baseline_path),
        candidate_path=str(candidate_path),
        config_dir="config",
        evidence_path=str(tmp_path / "missing_evidence.json"),
        labels_path=str(labels_path),
        reviews_path=str(tmp_path / "reviews.json"),
        output_dir=str(tmp_path / "history"),
        regression_mode="baseline",
        persist=False,
    )
    report = run_evaluation(run)

    assert report.total_field_changes == 501
    assert report.field_change_summaries_truncated is True
    assert len(report.field_change_summaries) == 500

    round_tripped = EvalReport.model_validate_json(report.model_dump_json())
    assert round_tripped.field_change_summaries_truncated is True
    assert len(round_tripped.field_change_summaries) == 500

