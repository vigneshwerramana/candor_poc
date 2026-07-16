# How the Overall Quality Score is calculated

One page. One formula. One worked example with real numbers from an actual run.

## The formula

```
overall_score = Σ ( normalized_scoreᵢ × weightᵢ )  /  Σ weightᵢ
```

over every metric that is **scoreable** (weight > 0 AND score is not N/A), where

```
normalized_score = score                 if direction = higher_is_better
normalized_score = 1 − score            if direction = lower_is_better   (e.g. duplicate_rate)
```

Dividing by `Σ weight` is the **renormalization**: when a metric is N/A (e.g.
`change_quality` on a zero-change run), it is *excluded* and the remaining weights
re-scale — an N/A is never silently treated as 0 or 1, because that would distort the
exact number this system exists to produce.

## The weights (from [`config/metrics.yaml`](../config/metrics.yaml) — config, not code)

| Metric | Weight | Direction | What it measures |
|---|---|---|---|
| completeness | 0.25 | higher | non-null share across data fields |
| change_quality | 0.25 | higher | valid changes / definitively-dispositioned changes |
| phone_validity | 0.20 | higher | valid 10-digit, non-placeholder phones (all rows) |
| duplicate_rate | 0.15 | **lower** | surplus duplicate-NPI rows / raw rows |
| freshness | 0.15 | higher | rows updated within 180 days |
| addition_quality | 0.10 | higher | valid additions / eligible additions |
| state_zip_consistency | 0.05 | higher | ZIP prefix belongs to the state |

(Weights sum to 1.15 by design — the divisor renormalizes, so adding a metric never
requires re-tuning the others. Report-only metrics have weight 0 and never enter the score.)

## Worked example — the actual REJECT run from the demo (overall = 0.8342)

| Metric | Score | Normalized | × Weight | Contribution |
|---|---|---|---|---|
| completeness | 0.9905 | 0.9905 | × 0.25 | 0.2476 |
| phone_validity | 0.9313 | 0.9313 | × 0.20 | 0.1863 |
| duplicate_rate | 0.0247 | 1 − 0.0247 = 0.9753 | × 0.15 | 0.1463 |
| freshness | 0.7125 | 0.7125 | × 0.15 | 0.1069 |
| state_zip_consistency | 0.9744 | 0.9744 | × 0.05 | 0.0487 |
| change_quality | 0.6275 | 0.6275 | × 0.25 | 0.1569 |
| addition_quality | 0.6667 | 0.6667 | × 0.10 | 0.0667 |
| **Σ** | | | **÷ 1.15** | **0.9593** |

```
overall_score = 0.9593 / 1.15 = 0.8342   →   83.4%
```

Same run, first-run baseline (v1 vs v1): zero changes → change_quality and
addition_quality are **N/A** → both excluded, divisor becomes 0.80, and the five
snapshot metrics alone give `overall = 0.9691`. No fake numbers, no distortion.

## Where the two transition metrics come from

**change_quality** — the denominator counts only *definitive* dispositions:

```
numerator   = auto-accepted (evidence-verified) + judged valid + human-approved
denominator = numerator + judged regressions + human-rejected
EXCLUDED    = auto-flagged, judge-uncertain, budget-deferred   (unknown ≠ invalid)
```

In the REJECT run: 48 auto-accepted + 15 judge-valid = 63 valid, over
63 + 38 judged regressions = 101 eligible → **0.6275**. The 4 uncertain changes are
excluded and reported as counts.

**addition_quality** — same discipline, one exception: *structural rejects* (bad NPI
Luhn checksum, placeholder phone, near-empty record) count as **invalid**, because they
are provably junk, not merely unverified. Demo: (4 verified + 2 judge-valid) /
(6 + 3 structural rejects) = 6/9 → **0.6667**.

## Guard rails on the score

- **min_sample:** change_quality below 20 eligible changes (5 for additions) is marked
  `low_confidence` — still displayed, but it cannot fire alerts. Two changes with one
  bad one must not swing a release decision.
- **The score does not decide alone.** The release decision comes from config-defined
  **alerts** (per-metric drop/rise thresholds + absolute volume thresholds):
  any critical alert → REJECT · warnings or review queue > 20 → REVIEW · else APPROVE.
  A pretty average must never hide one catastrophic metric — and per-metric alerts are
  exactly the mechanism that prevents it.
- **Comparability:** every report embeds a SHA256 fingerprint of the config; scores
  computed under different weights are flagged as incomparable, and only *accepted*
  runs ever become the regression baseline.
