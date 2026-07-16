"""Judge clients behind one protocol.

MockJudgeClient (default): deterministic verdicts from labels.json ground
truth — lets the E2E test measure the framework itself, and demos run free.

RealJudgeClient: LLM judge. Determinism measures: temperature 0, JSON schema
response, up to 3 retries on invalid output, fallback verdict "uncertain".
Provider-agnostic: reads ANTHROPIC_API_KEY or OPENAI_API_KEY from env.
Verdicts are cached on (npi, field, old, new, evidence) so identical changes
never pay twice.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from candor_qa.contracts import AdditionVerdict, JudgeVerdict, ScoredAddition, ScoredChange


class JudgeClient(Protocol):
    def judge(self, scored: ScoredChange) -> JudgeVerdict: ...
    def judge_addition(self, scored: ScoredAddition) -> AdditionVerdict: ...


def _cache_key(scored: ScoredChange) -> str:
    change = scored.change
    evidence_blob = json.dumps(
        [(c.source, str(c.value)) for c in scored.evidence], sort_keys=True
    )
    material = "|".join([change.npi, change.field, str(change.old_value),
                         str(change.new_value), evidence_blob])
    return hashlib.sha256(material.encode()).hexdigest()


def _addition_cache_key(scored: ScoredAddition) -> str:
    material = scored.event.npi + "|" + json.dumps(
        scored.event.record, sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()


class _CachingMixin:
    def __init__(self) -> None:
        self._cache: dict[str, JudgeVerdict] = {}
        self._addition_cache: dict[str, AdditionVerdict] = {}
        self.calls = 0

    def judge(self, scored: ScoredChange) -> JudgeVerdict:
        key = _cache_key(scored)
        if key not in self._cache:
            self.calls += 1
            self._cache[key] = self._judge_uncached(scored)
        return self._cache[key]

    def judge_addition(self, scored: ScoredAddition) -> AdditionVerdict:
        key = _addition_cache_key(scored)
        if key not in self._addition_cache:
            self.calls += 1
            self._addition_cache[key] = self._judge_addition_uncached(scored)
        return self._addition_cache[key]

    def _judge_uncached(self, scored: ScoredChange) -> JudgeVerdict:  # pragma: no cover
        raise NotImplementedError

    def _judge_addition_uncached(self, scored: ScoredAddition) -> AdditionVerdict:  # pragma: no cover
        raise NotImplementedError


class MockJudgeClient(_CachingMixin):
    """Answers from generator ground truth: field changes keyed (npi, field),
    additions keyed npi via labels record_events.additions."""

    def __init__(self, labels_path: str | Path):
        super().__init__()
        data = json.loads(Path(labels_path).read_text())
        self._truth = {
            (l["npi"], l["field"]): l["truth_label"]
            for l in data.get("field_changes", [])
        }
        self._addition_truth: dict[str, str] = (
            data.get("record_events", {}).get("additions", {})
        )

    def _judge_addition_uncached(self, scored: ScoredAddition) -> AdditionVerdict:
        truth = self._addition_truth.get(scored.event.npi)
        if truth in ("valid_addition", "invalid_addition"):
            return AdditionVerdict(
                verdict=truth, confidence=0.95,
                rationale=f"Mock judge: matches planted {truth} label.",
            )
        return AdditionVerdict(
            verdict="uncertain", confidence=0.5,
            rationale="Mock judge: no ground-truth label for this addition.",
        )

    def _judge_uncached(self, scored: ScoredChange) -> JudgeVerdict:
        truth = self._truth.get((scored.change.npi, scored.change.field))
        if truth == "regression":
            return JudgeVerdict(
                verdict="regression", confidence=0.95,
                rationale="Mock judge: matches a planted regression label.",
                recommended_action="reject",
            )
        if truth in ("valid_update", "false_positive_trap"):
            return JudgeVerdict(
                verdict="valid_update", confidence=0.95,
                rationale="Mock judge: matches a planted legitimate change.",
                recommended_action="auto_accept",
            )
        return JudgeVerdict(
            verdict="uncertain", confidence=0.5,
            rationale="Mock judge: no ground-truth label for this change.",
            recommended_action="human_review",
        )


class RealJudgeClient(_CachingMixin):
    """LLM judge. Requires ANTHROPIC_API_KEY or OPENAI_API_KEY."""

    MAX_RETRIES = 3

    def __init__(self, prompts: dict):
        super().__init__()
        self._prompts = prompts
        if os.environ.get("ANTHROPIC_API_KEY"):
            self._provider = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            self._provider = "openai"
        else:
            raise RuntimeError(
                "judge_mode=real requires ANTHROPIC_API_KEY or OPENAI_API_KEY; "
                "use judge_mode=mock otherwise."
            )

    def _render_prompt(self, scored: ScoredChange) -> str:
        change = scored.change
        evidence_lines = "\n".join(
            f"- {c.source} (tier {c.source_tier}): {c.value} @ {c.observed_at}"
            for c in scored.evidence
        ) or "- (no evidence available)"
        return self._prompts["user_template"].format(
            npi=change.npi, field=change.field,
            old_value=change.old_value, new_value=change.new_value,
            change_kind=change.change_kind.value,
            cross_source_status=scored.cross_source_status.value,
            evidence_lines=evidence_lines,
        )

    def _judge_uncached(self, scored: ScoredChange) -> JudgeVerdict:
        prompt = self._render_prompt(scored)
        for _attempt in range(self.MAX_RETRIES):
            try:
                return JudgeVerdict.model_validate_json(self._complete(prompt))
            except Exception:
                continue
        return JudgeVerdict(
            verdict="uncertain", confidence=0.0,
            rationale="Judge output failed schema validation after retries.",
            recommended_action="human_review",
        )

    def _judge_addition_uncached(self, scored: ScoredAddition) -> AdditionVerdict:
        evidence_lines = "\n".join(
            f"- {c.source} (tier {c.source_tier}): {c.value} @ {c.observed_at}"
            for c in scored.evidence
        ) or "- (no evidence available)"
        prompt = self._prompts["addition_template"].format(
            npi=scored.event.npi,
            record=json.dumps(scored.event.record, default=str),
            cross_source_status=scored.cross_source_status.value,
            evidence_lines=evidence_lines,
        )
        for _attempt in range(self.MAX_RETRIES):
            try:
                return AdditionVerdict.model_validate_json(self._complete(prompt))
            except Exception:
                continue
        return AdditionVerdict(
            verdict="uncertain", confidence=0.0,
            rationale="Judge output failed schema validation after retries.",
        )

    def _complete(self, prompt: str) -> str:
        if self._provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic()
            message = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=512,
                temperature=0,
                system=self._prompts["system"],
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        import openai
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self._prompts["system"]},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content
