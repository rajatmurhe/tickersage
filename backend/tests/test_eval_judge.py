"""The eval judge: a separate judge provider, and judge output that can't be used."""

import pytest

from app.config import Settings
from app.llm import LLMClient
from evals.judge import judge
from evals.run_evals import build_judge

from .conftest import ScriptedProvider


def _settings(**kw) -> Settings:
    base = dict(_env_file=None, llm_mode="live", database_url="sqlite://", anthropic_api_key=None,
                openai_api_key=None, openrouter_api_key=None, nvidia_api_key="nv-test", groq_api_key="gsk-test",
                primary_provider="groq")
    return Settings(**{**base, **kw})


def test_judge_provider_gets_only_its_own_provider():
    j = build_judge(_settings(), "nvidia")
    assert list(j.providers) == ["nvidia"] and j.settings.primary_provider == "nvidia"
    assert build_judge(_settings(), None) is None


def test_judge_provider_without_a_key_stops_the_run():
    with pytest.raises(SystemExit):
        build_judge(_settings(nvidia_api_key=None), "nvidia")


async def test_unparseable_verdict_is_a_judge_error_not_a_crash():
    llm = LLMClient(_settings(), providers={"groq": ScriptedProvider("groq", default="not json at all")})
    verdict = await judge(llm, "What is RSI?", "RSI is a momentum indicator [1].", [])
    assert "error" in verdict and "score" not in verdict


async def test_verdict_records_score_and_judge_model():
    out = ('{"answers_question": 1, "grounded": 1, "no_advice": 1, "handles_gaps": 0, "clear": 1, '
           '"rationale": "ok"}')
    llm = LLMClient(_settings(), providers={"groq": ScriptedProvider("groq", default=out)})
    verdict = await judge(llm, "What is RSI?", "RSI is a momentum indicator [1].", [])
    assert verdict["score"] == 0.8 and verdict["model"]


def test_experiments_keep_sections_they_did_not_rerun(tmp_path, monkeypatch):
    from evals import experiments

    out = tmp_path / "experiments.md"
    out.write_text("# TickerSage experiments\n\n<!-- exp:cache -->\n## 3. Cache\n\n| on | 5.0 |\n\n"
                   "<!-- exp:verifier -->\n## 2. Number verifier on vs off\n\nold numbers\n")
    monkeypatch.setattr(experiments, "OUT", out)
    kept = experiments._previous_sections()
    assert kept["cache"] == ["## 3. Cache", "", "| on | 5.0 |"]
    assert kept["verifier"][-1] == "old numbers"
