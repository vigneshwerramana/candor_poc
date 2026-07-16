# Candor Provider Data QA — Evaluation Framework POC **Goal:** automatically answer *"Is the candidate provider snapshot higher quality than the baseline snapshot?"* with a clear **APPROVE / REVIEW / REJECT** decision — **without permanent ground truth**. Provider data legitimately changes (doctors move, phones change), so a raw dataset diff is not a quality signal and a golden dataset decays. Instead, this framework **evaluates only the deltas**, scores each change with **cross-source evidence**, escalates only the ambiguous band to a **budgeted judge** (mock or real LLM), and leads every artifact with the decision. ## What's covered (and what's not) This is a **release gate / competition evaluator**, not a full enterprise data-quality catalog. **Covered:** completeness · validity (formats, NPI Luhn checksum, placeholder guards) · uniqueness (duplicate NPIs) · freshness · volume churn (silent row drops) · cross-column consistency (state↔ZIP) · **delta accuracy** on shared providers (change_quality) · added/deleted record validation (addition_quality, deletion heuristics) · **model competition** with an explicit PROD recommendation. **Not covered (deliberately, POC scope):** domain relevance manifests · entity resolution beyond NPI · judge-based deletion scoring · statistical confidence intervals. ## Quickstart — the four-act demo
bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,dashboard]"
.venv/bin/python scripts/generate_sample_data.py     # synthetic data + planted ground truth

# Act 1 — establish the baseline                     (expect: BASELINE_ESTABLISHED)
.venv/bin/python scripts/run_evaluation.py \
    --baseline data/providers_v1.csv --candidate data/providers_v1.csv \
    --regression-mode baseline --output data/evaluation_history/release

# Act 2 — bad release, planted regressions           (expect: REJECT, exit code 2)
.venv/bin/python scripts/run_evaluation.py \
    --baseline data/providers_v1.csv --candidate data/providers_v2.csv \
    --output data/evaluation_history/release

# Act 3 — two scrapers, same frozen input            (expect: "Deploy Scraper B to PROD")
.venv/bin/python scripts/run_evaluation.py \
    --baseline data/dev_a.csv --candidate data/dev_b.csv \
    --comparison-mode model_compete --regression-mode baseline \
    --labels data/labels_compete.json \
    --baseline-label "Scraper A" --candidate-label "Scraper B" \
    --output data/evaluation_history/compete

# Act 4 — clean release                              (expect: APPROVE, exit code 0)
.venv/bin/python scripts/run_evaluation.py \
    --baseline data/providers_v1.csv --candidate data/providers_v2_clean.csv \
    --labels data/labels_clean.json --output data/evaluation_history/release

.venv/bin/streamlit run app/dashboard.py             # dashboard + Delta Explorer
.venv/bin/python -m pytest                           # 62 tests
Exit codes 0/1/2 make it a drop-in CI release gate ([workflow example](docs/ARCHITECTURE.md)). Each run persists a decision-first report (JSON + Markdown) under data/evaluation_history/; deltas export to CSV via scripts/export_delta.py. ## Dashboard (screens + Delta Explorer) Run:
bash
.venv/bin/streamlit run app/dashboard.py
The dashboard is decision-first (APPROVE/REVIEW/REJECT), with **At-a-glance** charts, a **Delta Explorer** to browse persisted field-level diffs, and a **Human review queue** whose decisions persist in data/reviews.json for subsequent runs. <details> <summary>30s demo GIF + screenshots</summary> ![Demo — run, REJECT with reasons, Delta Explorer, competition winner, APPROVE](docs/images/demo.gif) ![REJECT](docs/images/dashboard_reject.png) ![Delta Explorer](docs/images/delta_explorer.png) ![Model competition](docs/images/compete_winner.png) ![APPROVE](docs/images/dashboard_approve.png) </details> ## Architecture
mermaid
flowchart TD
    subgraph SRC["Sources"]
        direction LR
        R["Registries"] ~~~ CL["Claims"] ~~~ H["Hospital sites"] ~~~ W["Web scrapes"]
    end
    SRC --> ING["Ingest pipeline (vN / vN+1)"]
    ING --> BASE[("Baseline snapshot<br/>yesterday · prod · model A")]
    ING --> CAND[("Candidate snapshot<br/>today · release · model B")]
    ING -. "provenance capture" .-> EV[("Evidence store<br/>(npi, field) → source · value · seen_at")]

    subgraph QA["candor_qa — five stages"]
        P1["1 · PREPARE<br/>safe load · dedupe · normalize"]
        P2["2 · COMPARE<br/>delta + snapshot health metrics"]
        P3["3 · SCORE<br/>trust-weighted evidence confidence"]
        P4["4 · JUDGE<br/>auto-accept · LLM judge (budgeted) · flag"]
        P5["5 · DECIDE<br/>alerts · regression vs history · score"]
        P1 --> P2 --> P3 --> P4 --> P5
    end
    BASE --> P1
    CAND --> P1
    EV --> P3
    CFG[/"config YAMLs<br/>schema · source tiers · weights · thresholds"/] -.-> QA

    P5 --> DEC{{"APPROVE / REVIEW / REJECT<br/>(CLI exit codes 0 / 1 / 2)"}}
    P5 --> REP["Report — JSON + Markdown"]
    REP --> DASH["Dashboard · Delta Explorer"]
    DASH --> HQ["Human review queue"]
    HQ -- "reviews.json feeds the next run" --> P4
The code mirrors the diagram: src/candor_qa/stage1_prepare/ … stage5_decide/, orchestrated by [pipeline.py](src/candor_qa/pipeline.py) in five readable calls. All behavior — fields, source trust tiers, weights, thresholds — lives in [config/](config/), not code. ## How the score is computed
overall_score = Σ (normalized_score × weight) / Σ weight      # N/A metrics excluded,
                                                              # weights renormalized
Seven weighted metrics (completeness 0.25 · change_quality 0.25 · phone_validity 0.20 · duplicate_rate 0.15, inverted · freshness 0.15 · addition_quality 0.10 · state_zip_consistency 0.05). The score never decides alone — per-metric **alerts** drive the decision, so a pretty average cannot hide one catastrophic metric. **Full formula, denominator rules, and a worked example with real run numbers: [docs/SCORING.md](docs/SCORING.md).** ## Why you can trust it (meta-QA) The synthetic generator knows every change it planted and emits ground-truth labels. The E2E tests assert — and every report shows: - planted regressions caught: **38/38** · planted invalid additions caught: **3/3** - false-positive traps flagged: **0/50** (reformats, order shuffles, "" vs "N/A", …) - judge precision / recall on regressions: **100% / 100%** It doesn't just produce a score — it proves it can tell a real regression from legitimate real-world change. ## Docs - **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — stage detail, design rules, extension recipes, the 70M-provider scaling path, CI gate, HTTP API - **[docs/SCORING.md](docs/SCORING.md)** — the score formula, worked example, guard rails ## Project layout
config/          schema · source tiers · metrics/weights · judge prompts · zip↔state reference
data/            snapshots (v1, v2, v2_clean, dev_a, dev_b) · labels (ground truth) ·
                 evidence store · evaluation_history/{release,compete}/
src/candor_qa/   contracts.py · config.py · stage1_prepare/ … stage5_decide/ · pipeline.py
scripts/         generate_sample_data.py · run_evaluation.py · export_delta.py
app/ · api/      Streamlit dashboard · FastAPI wrapper
tests/           one file per stage + record events, additions, compete, E2E (62 tests)
docs/            ARCHITECTURE.md · SCORING.md · images/