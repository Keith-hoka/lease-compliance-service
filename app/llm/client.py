"""Judge construction: provider selection behind the frozen JudgeFn interface."""

from app.core.config import settings
from app.llm.failover import JudgeError, JudgeFn, ProviderDown
from app.llm.providers.anthropic import make_anthropic_judge
from app.llm.providers.openai_ import make_openai_judge

__all__ = ["JudgeError", "JudgeFn", "ProviderDown", "make_judge", "parse_model_ref"]

PROVIDERS = ("anthropic", "openai")


def parse_model_ref(ref: str) -> tuple[str, str]:
    """Split 'provider:model'; a bare ref means anthropic."""
    provider, sep, model = ref.partition(":")
    if not sep:
        return "anthropic", ref
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider prefix in model ref: {ref}")
    return provider, model


def _provider_judge(ref: str) -> JudgeFn:
    provider, model = parse_model_ref(ref)
    if provider == "anthropic":
        return make_anthropic_judge(model)
    if not settings.openai_api_key:
        raise RuntimeError(f"model ref {ref} requires OPENAI_API_KEY")
    return make_openai_judge(model)


def make_judge() -> JudgeFn:
    return _provider_judge(settings.clause_audit_model)
