"""Optional smoke test for the real LLM judge. Skipped unless an API key is
set — the rest of the suite never touches the network."""
import os

import pytest

from candor_qa.contracts import (
    ChangeKind,
    CrossSourceStatus,
    FieldChange,
    ScoredChange,
)

HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))


@pytest.mark.skipif(not HAS_KEY, reason="no LLM API key in environment")
def test_real_judge_returns_schema_valid_verdict(config):
    from candor_qa.stage4_judge.judge import RealJudgeClient

    client = RealJudgeClient(config.judge_prompts)
    verdict = client.judge(ScoredChange(
        change=FieldChange(npi="1234567893", field="phone",
                           old_value="6175550134", new_value=None,
                           change_kind=ChangeKind.wiped),
        cross_source_status=CrossSourceStatus.unverifiable,
        confidence=0.5,
    ))
    assert verdict.verdict in ("valid_update", "regression", "uncertain")
    assert 0.0 <= verdict.confidence <= 1.0
