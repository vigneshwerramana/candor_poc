"""Stage 3: cross-source states and evidence-weighted confidence."""
import pytest

from candor_qa.contracts import ChangeKind, CrossSourceStatus, FieldChange, SourceCandidate
from candor_qa.stage3_score.confidence import score_changes


class FakeEvidence:
    def __init__(self, candidates):
        self._candidates = candidates

    def get_candidates(self, npi, field):
        return self._candidates


def change(new_value="6175550134"):
    return FieldChange(npi="1", field="phone", old_value="2125550100",
                       new_value=new_value, change_kind=ChangeKind.modified)


def candidate(source, value, tier):
    return SourceCandidate(source=source, value=value, source_tier=tier)


def score_one(candidates, config, new_value="6175550134"):
    provider = FakeEvidence(candidates)
    return score_changes([change(new_value)], provider,
                         config.source_tiers, config.data_schema)[0]


def test_verified_two_sources_agree(config):
    scored = score_one([
        candidate("npi_registry", "(617) 555-0134", 1.0),   # format differs, agrees
        candidate("hospital_website", "6175550134", 0.8),
    ], config)
    assert scored.cross_source_status is CrossSourceStatus.verified
    assert scored.confidence == 1.0
    assert set(scored.agreeing_sources) == {"npi_registry", "hospital_website"}


def test_conflict_sources_disagree(config):
    scored = score_one([
        candidate("claims", "2125550100", 0.9),        # still shows old number
        candidate("web_scrape", "6175550134", 0.4),
    ], config)
    assert scored.cross_source_status is CrossSourceStatus.conflict
    # trust-weighted agreement: 0.4 / 1.3, capped at 0.70
    assert scored.confidence == pytest.approx(0.4 / 1.3, abs=1e-3)


def test_weak_evidence_single_source_capped(config):
    scored = score_one([candidate("insurance_directory", "6175550134", 0.7)], config)
    assert scored.cross_source_status is CrossSourceStatus.weak_evidence
    assert scored.confidence == 0.6      # full agreement, but one source caps at 0.6


def test_unverifiable_no_evidence_is_its_own_state(config):
    scored = score_one([], config)
    assert scored.cross_source_status is CrossSourceStatus.unverifiable
    assert scored.confidence == 0.5      # maximum uncertainty, not rejection


def test_tier_weighting_prefers_trusted_sources(config):
    # high-tier disagrees, low-tier agrees -> low confidence despite 1-of-2 agreeing
    scored = score_one([
        candidate("npi_registry", "2125550100", 1.0),
        candidate("social", "6175550134", 0.3),
    ], config)
    assert scored.cross_source_status is CrossSourceStatus.conflict
    assert scored.confidence < 0.3
