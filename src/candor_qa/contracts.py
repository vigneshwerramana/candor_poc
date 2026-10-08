"""All data contracts between stages, in one readable file.

Reading order mirrors the pipeline:
PREPARE -> COMPARE -> SCORE -> JUDGE -> DECIDE -> EvalReport
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


# ---------------------------------------------------------------- Stage 1: PREPARE

class DedupeReport(BaseModel):
    """What was removed before the delta merge could safely run."""
    raw_count: int
    duplicate_npi_count: int          # distinct NPIs that had more than one row
    dropped_duplicate_rows: int       # surplus rows removed (keep-first)
    null_npi_count: int               # rows with no join key, excluded from merge

    @property
    def duplicate_rate(self) -> float:
        return self.dropped_duplicate_rows / self.raw_count if self.raw_count else 0.0


class NormalizationReport(BaseModel):
    """Unparseable values are kept (never nulled out) but counted here."""
    failure_counts: dict[str, int] = {}
    row_count: int = 0

    @property
    def failure_rate(self) -> float:
        if not self.row_count or not self.failure_counts:
            return 0.0
        return sum(self.failure_counts.values()) / (self.row_count * len(self.failure_counts))


# ---------------------------------------------------------------- Stage 2: COMPARE

class ChangeKind(str, Enum):
    modified = "modified"   # value -> different value
    wiped = "wiped"         # value -> null (almost never a valid update)
    filled = "filled"       # null -> value


class RecordEventKind(str, Enum):
    added = "added"         # NPI present only in the candidate snapshot
    deleted = "deleted"     # NPI present only in the baseline snapshot


class RecordEvent(BaseModel):
    """A whole-record appearance/disappearance — evaluated as its own event,
    not as a set of field changes (an addition has no 'old value' to judge)."""
    npi: str
    kind: RecordEventKind
    record: dict[str, Any]            # normalized row values
    completeness: float               # non-null share of data-carrying fields
    validity_flags: dict[str, bool]   # npi_luhn_ok, phone_ok, required_ok

    def key(self) -> tuple[str, str, str, str]:
        """Review/cache identity, same shape as FieldChange.key()."""
        return (self.npi, "__record__", "None", self.kind.value)


class FieldChange(BaseModel):
    npi: str
    field: str
    old_value: Any = None
    new_value: Any = None
    change_kind: ChangeKind

    def key(self) -> tuple[str, str, str, str]:
        """Stable identity used by the judge cache and reviews.json."""
        return (self.npi, self.field, str(self.old_value), str(self.new_value))


class VolumeSummary(BaseModel):
    """Pairwise churn. Alert-only: rate metrics cannot see silent row drops."""
    baseline_count: int
    candidate_count: int
    added_count: int
    deleted_count: int
    updated_count: int      # records with at least one field change
    unchanged_count: int
    added_pct: float        # added / baseline_count
    deleted_pct: float      # deleted / baseline_count


class DeltaSummary(BaseModel):
    field_changes: list[FieldChange]
    record_events: list[RecordEvent] = []   # additions + deletions, typed
    volume: VolumeSummary
    # Raw values differed but normalization made them equal (format noise).
    suppressed_by_normalization: int = 0
    # Normalized values differed only in order (set-compared fields).
    suppressed_by_set_compare: int = 0


class MetricResult(BaseModel):
    name: str
    score: float | None                # None == N/A (e.g. zero eligible changes)
    weight: float = 0.0
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"
    status: Literal["ok", "n/a", "low_confidence"] = "ok"
    sample_size: int | None = None

    @property
    def normalized_score(self) -> float | None:
        """Score oriented so that higher is always better (for weighting)."""
        if self.score is None:
            return None
        return self.score if self.direction == "higher_is_better" else 1.0 - self.score


# ---------------------------------------------------------------- Stage 3: SCORE

class SourceCandidate(BaseModel):
    source: str
    value: Any
    observed_at: datetime | None = None
    source_tier: float | None = None


class CrossSourceStatus(str, Enum):
    verified = "verified"           # 2+ sources agree with the new value
    conflict = "conflict"           # 2+ sources, mixed signals -> judge
    weak_evidence = "weak_evidence" # exactly 1 source -> confidence cap 0.6
    unverifiable = "unverifiable"   # 0 sources -> confidence 0.5, judge/review


class ScoredChange(BaseModel):
    change: FieldChange
    cross_source_status: CrossSourceStatus
    confidence: float
    agreeing_sources: list[str] = []
    disagreeing_sources: list[str] = []
    evidence: list[SourceCandidate] = []


class ScoredAddition(BaseModel):
    """An added record scored on structure + per-field evidence."""
    event: RecordEvent
    cross_source_status: CrossSourceStatus   # worst state across checked fields
    confidence: float                        # mean per-field confidence, capped
    checked_fields: list[str] = []
    structural_failures: list[str] = []      # e.g. npi_luhn_ok, phone_ok
    evidence: list[SourceCandidate] = []


# ---------------------------------------------------------------- Stage 4: JUDGE

class JudgeVerdict(BaseModel):
    verdict: Literal["valid_update", "regression", "uncertain"]
    confidence: float
    rationale: str
    recommended_action: Literal["auto_accept", "human_review", "reject"]


class JudgedChange(BaseModel):
    scored: ScoredChange
    route: Literal["auto_accept", "judged", "auto_flag", "budget_deferred", "prior_review"]
    verdict: JudgeVerdict | None = None
    prior_review_decision: Literal["approved", "rejected"] | None = None


class AdditionVerdict(BaseModel):
    """Separate from JudgeVerdict so the change_quality rollup stays untouched."""
    verdict: Literal["valid_addition", "invalid_addition", "uncertain"]
    confidence: float
    rationale: str


class JudgedAddition(BaseModel):
    scored: ScoredAddition
    route: Literal["auto_accept", "judged", "auto_flag", "structural_reject",
                   "budget_deferred", "prior_review"]
    verdict: AdditionVerdict | None = None
    prior_review_decision: Literal["approved", "rejected"] | None = None


class AdditionQualityBreakdown(BaseModel):
    """Same denominator discipline as change_quality, with one difference:
    structural_rejected (bad NPI checksum, placeholder phone, near-empty) is
    KNOWN-invalid — provably junk, not merely unverified — so it counts in the
    denominator, unlike auto_flagged/uncertain/deferred which are unknown."""
    auto_accepted: int = 0
    judged_valid: int = 0
    judged_invalid: int = 0
    structural_rejected: int = 0
    review_approved: int = 0
    review_rejected: int = 0
    auto_flagged: int = 0
    uncertain: int = 0
    budget_deferred: int = 0

    @property
    def numerator(self) -> int:
        return self.auto_accepted + self.judged_valid + self.review_approved

    @property
    def denominator(self) -> int:
        return (self.numerator + self.judged_invalid + self.structural_rejected
                + self.review_rejected)


class ChangeQualityBreakdown(BaseModel):
    """The one rule, made auditable.

    numerator   = auto_accepts + judged "valid_update" + prior reviews approved
    denominator = numerator base + judged "regression" + prior reviews rejected
    excluded    = auto_flagged + uncertain + budget_deferred (reported as counts)
    """
    auto_accepted: int = 0
    judged_valid: int = 0
    judged_regression: int = 0
    review_approved: int = 0
    review_rejected: int = 0
    auto_flagged: int = 0
    uncertain: int = 0
    budget_deferred: int = 0

    @property
    def numerator(self) -> int:
        return self.auto_accepted + self.judged_valid + self.review_approved

    @property
    def denominator(self) -> int:
        return self.numerator + self.judged_regression + self.review_rejected

    @property
    def judge_coverage(self) -> float:
        judged = self.judged_valid + self.judged_regression + self.uncertain
        total = judged + self.budget_deferred
        return judged / total if total else 1.0


# ---------------------------------------------------------------- Stage 5: DECIDE

class Alert(BaseModel):
    metric: str
    severity: Literal["critical", "warning"]
    kind: Literal["drop", "rise", "volume", "improvement_anomaly", "config_mismatch"]
    message: str
    baseline_value: float | None = None
    candidate_value: float | None = None


class RegressionResult(BaseModel):
    mode: Literal["compare", "baseline", "compare_to"]
    compared_run_id: str | None = None
    config_mismatch: bool = False
    metric_deltas: dict[str, float] = {}   # candidate - compared run, per metric
    notes: list[str] = []


class ReleaseDecision(str, Enum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    REJECT = "REJECT"
    BASELINE_ESTABLISHED = "BASELINE_ESTABLISHED"


class ReviewItem(BaseModel):
    npi: str
    field: str                        # "__record__" for addition events
    old_value: Any = None
    new_value: Any = None
    change_kind: ChangeKind | RecordEventKind
    reason: Literal["auto_flagged", "judge_review", "uncertain",
                    "addition_flagged", "addition_uncertain"]
    confidence: float
    cross_source_status: CrossSourceStatus
    judge_rationale: str | None = None


class DeletionVerdict(BaseModel):
    """Heuristic only in the POC: deleting a complete record is suspicious
    (possible accidental drop); deleting an incomplete/stale one is plausible
    retirement. Full judge-based deletion scoring is a production extension."""
    npi: str
    was_complete: float
    likely_valid_retirement: bool
    recommended_action: Literal["accept", "review"]


class FieldChangeSummary(BaseModel):
    """Persisted, browseable representation of a field delta after routing."""
    npi: str
    field: str
    old_value: Any = None
    new_value: Any = None
    change_kind: ChangeKind
    disposition: str | None = None
    confidence: float | None = None


class RecordEventSummary(BaseModel):
    """Persisted, browseable representation of a record-level event."""
    npi: str
    kind: RecordEventKind
    disposition: str | None = None
    confidence: float | None = None


class CompetitionSummary(BaseModel):
    """Model A (baseline) vs model B (candidate) on the same frozen input.

    field_win_rate IS the change_quality score read as a contest: of the
    definitive dispositions on fields where the models disagree, the share
    where B's value won (verified or judged valid)."""
    baseline_label: str
    candidate_label: str
    shared_npi_count: int
    disagreement_count: int            # definitive dispositions (win-rate denominator)
    field_win_rate: float | None       # None below min_disagreements
    snapshot_score_delta: float        # snapshot-metrics-only score(B) - score(A)
    addition_quality: float | None
    coverage_delta: int                # candidate rows - baseline rows
    regressions_in_candidate: int
    recommendation: Literal["candidate_wins", "baseline_wins", "inconclusive"]

    def winner_name(self) -> str | None:
        """Human-readable winner for headlines (--baseline-label / --candidate-label)."""
        if self.recommendation == "candidate_wins":
            return self.candidate_label
        if self.recommendation == "baseline_wins":
            return self.baseline_label
        return None


class JudgeAccuracy(BaseModel):
    """Judge meta-QA vs. generator ground truth (labels.json), when available."""
    n_labeled_judged: int
    regression_precision: float | None
    regression_recall: float | None
    planted_regressions: int
    planted_regressions_caught: int
    traps_flagged: int             # false-positive traps that leaked into changes
    planted_invalid_additions: int = 0
    planted_invalid_additions_caught: int = 0


class EvalReport(BaseModel):
    run_id: str
    pipeline_version: str | None = None
    comparison_mode: Literal["same_input", "temporal", "model_compete"]
    config_fingerprint: str
    regression_mode: Literal["compare", "baseline", "compare_to"]
    baseline_label: str | None = None
    candidate_label: str | None = None

    volume_summary: VolumeSummary
    dedupe_report_baseline: DedupeReport
    dedupe_report_candidate: DedupeReport
    normalization_report_baseline: NormalizationReport
    normalization_report_candidate: NormalizationReport
    suppressed_by_normalization: int
    suppressed_by_set_compare: int
    total_field_changes: int

    metrics_baseline: list[MetricResult]
    metrics_candidate: list[MetricResult]
    overall_score: float | None
    metrics_included: list[str]
    metrics_excluded: list[str]

    change_quality: ChangeQualityBreakdown
    addition_quality: AdditionQualityBreakdown
    deletion_verdicts: list[DeletionVerdict] = []
    judge_coverage: float
    judge_calls_made: int = 0
    estimated_judge_cost: float = 0.0
    judge_accuracy: JudgeAccuracy | None = None
    competition_summary: CompetitionSummary | None = None

    prod_recommendation: str | None = None
    prod_rationale: list[str] = []

    field_change_summaries: list[FieldChangeSummary] = []
    record_event_summaries: list[RecordEventSummary] = []
    field_change_summaries_truncated: bool = False

    review_queue: list[ReviewItem]
    regression: RegressionResult
    critical_alerts: list[Alert]
    warnings: list[Alert]
    release_decision: ReleaseDecision
    top_findings: list[str] = []


# ---------------------------------------------------------------- Claims validation

class ClaimCheckResult(BaseModel):
    claim_id: str
    npi: str
    provider_name: str | None = None
    contract: str
    agreement_type: str
    service_city: str | None = None
    service_state: str | None = None
    mapped_network: str
    address_match: str
    diagnosis: str
    explanation: str
    flagged: bool = False
    reference_available: bool = False


class ClaimsCacheCoverage(BaseModel):
    unique_npis: int
    nppes_cached: int
    candor_detail_cached: int
    claims_with_reference: int


class ClaimsReport(BaseModel):
    run_id: str
    pipeline: Literal["claims"] = "claims"
    total_claims: int
    unmapped_count: int
    flagged_count: int
    exact_match_count: int = 0
    mapping_and_address_match_count: int = 0
    results: list[ClaimCheckResult]
    summary_by_diagnosis: dict[str, int] = {}
    assignment_npis: list[str] = []
    cache_coverage: ClaimsCacheCoverage | None = None


# ---------------------------------------------------------------- DQI validation

class ValidationFinding(BaseModel):
    dqi_id: str
    rule_id: str
    npi: str
    provider_name: str | None = None
    severity: Literal["critical", "warning", "info"]
    message: str
    observed: str | None = None
    expected: str | None = None
    source: str = "candor"


class ValidationReport(BaseModel):
    run_id: str
    pipeline: Literal["validation"] = "validation"
    provider_count: int
    overall_score: float | None = None
    findings: list[ValidationFinding]
    summary_by_dqi: dict[str, int] = {}
    summary_by_severity: dict[str, int] = {}
