# Architecture & Design Reference

The deep-dive companion to the [README](../README.md): design rules, stage detail,
extension recipes, the production scaling path, and the optional HTTP API.
For the score formula see [SCORING.md](SCORING.md).

## The five stages in detail

```
┌─ 1 PREPARE ──────────────────────────────────────────────────────┐
│ load safely (all-string, explicit nulls) → dedupe on NPI →       │
│ normalize formats (phones, zips, names, lists) + canonical nulls │
├─ 2 COMPARE ──────────────────────────────────────────────────────┤
│ delta: added / deleted / changed (wiped·filled·modified),        │
│ format noise suppressed & counted + health metrics on BOTH sides │
├─ 3 SCORE ────────────────────────────────────────────────────────┤
│ cross-source evidence: verified / conflict / weak / unverifiable │
│ → trust-weighted confidence per change                           │
├─ 4 JUDGE ────────────────────────────────────────────────────────┤
│ auto-accept the verified · judge the ambiguous (LLM, budgeted) · │
│ flag the weak → change_quality + overall weighted score          │
├─ 5 DECIDE ───────────────────────────────────────────────────────┤
│ alerts (drops, volume, anomalies) + regression vs history        │
│ → APPROVE / REVIEW / REJECT + human review queue                 │
└──────────────────────────────────────────────────────────────────┘
```

The code mirrors this exactly: `src/candor_qa/stage1_prepare/` …
`stage5_decide/`, orchestrated by [pipeline.py](../src/candor_qa/pipeline.py)
(five function calls, readable top to bottom). Every contract between stages
is a Pydantic model in [contracts.py](../src/candor_qa/contracts.py).
All behavior — fields, weights, thresholds, judge bands — lives in
[config/](../config/), not code.

## Design rules that keep it honest

- **Dataset diff ≠ quality.** Only changes are evaluated; format noise is
  suppressed *and counted* (`suppressed_by_normalization/set_compare`), so
  the false-positive savings are visible, not silent.
- **A wipe is not an update.** value→null is its own change kind and always
  goes to the judge.
- **Unknown ≠ invalid.** Auto-flagged, uncertain, and budget-deferred changes
  are excluded from the `change_quality` denominator and reported as counts.
- **N/A ≠ 0.** Metrics with no sample (zero changes) are excluded and weights
  renormalized; below `min_sample` they can't fire alerts.
- **Rates can't see row drops.** Volume alerts (absolute churn thresholds)
  catch what per-row metrics mathematically cannot.
- **Gaming guards.** Placeholder values count as invalid; a sudden +10%
  improvement raises a warning too.
- **Comparable runs only.** Reports embed a config fingerprint; regression
  comparisons across different weights warn loudly.
- **Rejected runs never set the quality bar.** Regression compares against
  the last *accepted* run — otherwise a chain of slightly-less-bad releases
  could ratchet quality downward.
- **Valid ≠ consistent.** `state_zip_consistency` checks that the 3-digit ZIP
  prefix belongs to the state (reference table in `config/zip_prefixes.yaml`);
  unknown prefixes are excluded from the denominator, never penalized.
- **comparison_mode.** `same_input` (same frozen input through two pipeline
  versions) makes alerts attributable to the pipeline; `temporal`
  (yesterday vs today) is drift monitoring — the report records which claim
  it is making.

## Extension recipes

| Want to…                          | Do this                                                          |
| --------------------------------- | ---------------------------------------------------------------- |
| Add a field                       | 1 entry in `config/schema.yaml` (reuse a normalizer)             |
| Add a metric                      | 1 function in `snapshot_metrics.py` + 1 entry in `metrics.yaml`  |
| Add an evidence source            | 1 entry in `config/source_tiers.yaml`                            |
| Change alert thresholds / weights | edit `metrics.yaml` (no code)                                    |
| Use a real LLM judge              | `--judge-mode real` with `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |

If an extension needs more than that, the design failed — file it as a bug.

## Scaling path: POC → 70M providers

The core scaling insight is **delta-only evaluation**: 70M rows × ~3% daily
change ≈ 2M deltas; only the ambiguous-confidence band reaches the judge
(~10% ≈ 200k), and `max_judge_calls` caps spend. Full-dataset work is limited
to vectorized column metrics.

| Component     | POC                    | Production                                                          | Contract that stays         |
| ------------- | ---------------------- | ------------------------------------------------------------------- | --------------------------- |
| Snapshots     | CSV + pandas           | partitioned Parquet + Polars/DuckDB/Spark                           | `schema.yaml`               |
| Delta         | in-process merge       | parallel merge by NPI-prefix partition                              | `DeltaSummary`              |
| Metrics       | vectorized pandas      | same expressions on the SQL engine                                  | `MetricResult`              |
| Evidence      | `source_evidence.json` | provenance sidecar captured at ingest, joined on changed pairs only | `EvidenceProvider` protocol |
| Judge         | mock from labels       | batched async LLM + cache + budget                                  | `JudgeVerdict`              |
| History       | JSON files             | append-only DB table                                                | `EvalReport`                |
| Orchestration | one CLI call           | one DAG task per stage                                              | stage signatures            |

Each stage is a stateless pure function, so the swap is mechanical, not a redesign.

## CI/CD release gate

Exit codes: `0` approve/baseline · `1` review · `2` reject.

```yaml
# .github/workflows/data-quality-gate.yml
jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e .
      - name: Evaluate candidate dataset
        run: |
          python scripts/run_evaluation.py \
            --baseline data/current_prod.csv \
            --candidate data/candidate.csv \
            --output data/evaluation_history/release
        # exit 2 (REJECT) or 1 (REVIEW) fails the job; 0 (APPROVE) ships
```

## Real assignment modes (DQI + Claims)

The dashboard supports three views via the sidebar **View** radio:

| Mode | Script | Input | Report path |
|------|--------|-------|-------------|
| **Release Evaluation** | `scripts/run_evaluation.py` | Synthetic CSV snapshots | `data/evaluation_history/release/` |
| **Data Quality** | `scripts/run_validation.py` | Candor list 2111 JSON + NPPES cache | `data/evaluation_history/validation/` |
| **Claims Explorer** | `scripts/run_claims_check.py` | Claims CSV/XLSX + Exhibit A + NPPES | `data/evaluation_history/claims/` |

**Data Quality** runs DQI-1..9 rules from [`config/rules.yaml`](../config/rules.yaml) against
offline Candor search/detail JSON and cached NPPES responses. No live API required.

**Claims Explorer** applies three checks per claim: (1) Exhibit A network mapping from
[`config/exhibit_a.yaml`](../config/exhibit_a.yaml), (2) service-address match vs Candor/NPPES,
(3) diagnosis (`mapping_gap`, `address_mismatch`, `wrong_agreement_type`, `likely_matching_bug`, etc.).

Regenerate reconciliation CSV: `python scripts/reconcile_sources.py`.

Live data paths (gitignored): `data/candor_live/`, `data/claims/`.

## HTTP API (optional)

```bash
pip install -e ".[api]"
.venv/bin/uvicorn api.main:app --port 8600
# POST /evaluate · GET /reports/latest · GET /reports/{run_id}
```

Real LLM judge: `pip install -e ".[llm]"`, copy `.env.example`, run with
`--judge-mode real`. Every report carries `judge_calls_made` and an
estimated cost (`judge_policy.cost_per_call` in metrics.yaml is the lever).
