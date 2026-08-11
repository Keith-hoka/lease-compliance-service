"""Provider adapter and judge-construction tests (no network)."""

import pytest

from app.llm.client import parse_model_ref
from app.llm.failover import JudgeError, ProviderDown


def test_provider_down_is_a_judge_error():
    assert issubclass(ProviderDown, JudgeError)


def test_parse_model_ref_defaults_bare_to_anthropic():
    assert parse_model_ref("claude-sonnet-5") == ("anthropic", "claude-sonnet-5")


def test_parse_model_ref_splits_prefix():
    assert parse_model_ref("openai:gpt-5.6-terra") == ("openai", "gpt-5.6-terra")
    assert parse_model_ref("anthropic:claude-opus-4-8") == ("anthropic", "claude-opus-4-8")


def test_parse_model_ref_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        parse_model_ref("deepseek:chat")
