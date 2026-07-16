"""Thin HTTP wrapper around the pipeline. Install: pip install -e ".[api]"

    .venv/bin/uvicorn api.main:app --port 8600

POST /evaluate           run an evaluation (body: EvaluateRequest)
GET  /reports/latest     most recent persisted report
GET  /reports/{run_id}   a specific report
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from candor_qa.pipeline import RunConfig, run_evaluation  # noqa: E402

DEFAULT_HISTORY = ROOT / "data" / "evaluation_history"

app = FastAPI(title="Candor QA", version="0.1.0")


class EvaluateRequest(BaseModel):
    baseline_path: str
    candidate_path: str
    judge_mode: str = "mock"
    regression_mode: str = "compare"
    comparison_mode: str | None = None
    baseline_label: str = "baseline"
    candidate_label: str = "candidate"
    labels_path: str | None = "data/labels.json"
    output_dir: str = "data/evaluation_history"


@app.post("/evaluate")
def evaluate(request: EvaluateRequest) -> dict:
    report = run_evaluation(RunConfig(
        baseline_path=request.baseline_path,
        candidate_path=request.candidate_path,
        config_dir=str(ROOT / "config"),
        labels_path=request.labels_path,
        judge_mode=request.judge_mode,
        regression_mode=request.regression_mode,
        comparison_mode=request.comparison_mode,
        baseline_label=request.baseline_label,
        candidate_label=request.candidate_label,
        output_dir=request.output_dir,
    ))
    return json.loads(report.model_dump_json())


def _find_reports(history_dir: Path) -> list[Path]:
    return sorted(history_dir.rglob("*.json"))


@app.get("/reports/latest")
def latest_report() -> dict:
    reports = _find_reports(DEFAULT_HISTORY)
    if not reports:
        raise HTTPException(404, "no evaluation runs yet")
    return json.loads(reports[-1].read_text())


@app.get("/reports/{run_id}")
def report_by_id(run_id: str) -> dict:
    for path in _find_reports(DEFAULT_HISTORY):
        if path.stem == run_id:
            return json.loads(path.read_text())
    raise HTTPException(404, f"run {run_id} not found")
