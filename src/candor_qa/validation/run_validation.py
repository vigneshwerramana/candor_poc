"""Single-pass DQI validation for Candor roster."""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from candor_qa.contracts import ValidationFinding, ValidationReport
from candor_qa.sources import load_candor_search
from candor_qa.validation.rules import run_all_rules


@dataclass
class ValidationRunConfig:
    candor_json: str = "data/candor_live/raw_page1.json"
    nppes_dir: str = "data/candor_live/nppes"
    details_dir: str | None = "data/candor_live/details"
    rules_path: str = "config/rules.yaml"
    output_dir: str = "data/evaluation_history/validation"
    reference_date: date | None = None
    persist: bool = True


def _make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


def _overall_score(findings: list[ValidationFinding], provider_count: int) -> float | None:
    if not provider_count:
        return None
    penalty = sum(
        1.0 if f.severity == "critical" else 0.5 if f.severity == "warning" else 0.1
        for f in findings if f.npi != "*"
    )
    per_provider = penalty / provider_count
    return max(0.0, min(1.0, 1.0 - per_provider * 0.15))


def run_validation(cfg: ValidationRunConfig) -> ValidationReport:
    individuals = load_candor_search(cfg.candor_json)
    findings = run_all_rules(
        individuals,
        nppes_dir=cfg.nppes_dir,
        details_dir=cfg.details_dir,
        rules_path=cfg.rules_path,
        reference_date=cfg.reference_date,
    )

    report = ValidationReport(
        run_id=_make_run_id(),
        provider_count=len(individuals),
        overall_score=_overall_score(findings, len(individuals)),
        findings=findings,
        summary_by_dqi=dict(Counter(f.dqi_id for f in findings)),
        summary_by_severity=dict(Counter(f.severity for f in findings)),
    )

    if cfg.persist:
        save_validation_report(report, cfg.output_dir)
    return report


def render_validation_markdown(report: ValidationReport) -> str:
    score = f"{report.overall_score:.1%}" if report.overall_score is not None else "N/A"
    lines = [
        f"# DQI Validation Report — `{report.run_id}`",
        "",
        f"- Providers: **{report.provider_count}**",
        f"- Findings: **{len(report.findings)}**",
        f"- Overall score: **{score}**",
        "",
        "## By DQI",
    ]
    for dqi, count in sorted(report.summary_by_dqi.items()):
        lines.append(f"- **{dqi}**: {count}")
    lines += ["", "## Findings (sample)"]
    for f in report.findings[:40]:
        lines.append(f"- [{f.dqi_id}] `{f.npi}` {f.provider_name or ''}: {f.message}")
    return "\n".join(lines) + "\n"


def save_validation_report(report: ValidationReport, output_dir: str | Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    json_path.write_text(report.model_dump_json(indent=2))
    (output_dir / f"{report.run_id}.md").write_text(render_validation_markdown(report))
    return json_path
