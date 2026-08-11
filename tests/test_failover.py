"""Circuit-breaker state machine tests: fake judges, injected clock, no sleeps."""

import pytest

from app.clause_audit.document import DocumentInput
from app.llm.failover import COOLDOWN_SECONDS, FailoverJudge, JudgeError, ProviderDown
from app.llm.schemas import FieldsOutput

DOC = DocumentInput(kind="text", text="x")


def _judge(script):
    """Scripted judge: 'down' raises ProviderDown, 'bad' raises JudgeError, else returned."""
    remaining = list(script)
    calls = []

    async def judge(doc, instruction, output_model):
        calls.append(instruction)
        action = remaining.pop(0)
        if action == "down":
            raise ProviderDown("x")
        if action == "bad":
            raise JudgeError("x")
        return action

    judge.calls = calls
    return judge


def _wrapper(primary_script, backup_script=None, clock=None):
    now = {"t": 0.0}
    primary = _judge(primary_script)
    backup = _judge(backup_script) if backup_script is not None else None
    wrapper = FailoverJudge(
        primary=primary,
        primary_ref="claude-sonnet-5",
        backup=backup,
        backup_ref="openai:gpt-5.6-terra" if backup else None,
        clock=clock or (lambda: now["t"]),
    )
    return wrapper, primary, backup, now


async def test_trips_after_three_consecutive_downs_and_serves_via_backup():
    wrapper, primary, backup, _ = _wrapper(["down", "down", "down"], ["ok"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed"
    assert await wrapper(DOC, "i", FieldsOutput) == "ok"
    assert wrapper.state == "open"
    assert len(primary.calls) == 3 and len(backup.calls) == 1


async def test_success_resets_the_counter():
    wrapper, _, _, _ = _wrapper(["down", "down", "r1", "down", "down"], ["never"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert await wrapper(DOC, "i", FieldsOutput) == "r1"
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed"


async def test_content_error_neither_counts_nor_switches():
    wrapper, _, backup, _ = _wrapper(["down", "down", "bad", "down", "down"], ["never"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    with pytest.raises(JudgeError):
        await wrapper(DOC, "i", FieldsOutput)
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed" and backup.calls == []


async def test_open_routes_everything_to_backup():
    wrapper, primary, _backup, _ = _wrapper(["down"] * 3, ["b1", "b2"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    await wrapper(DOC, "i", FieldsOutput)
    assert await wrapper(DOC, "i", FieldsOutput) == "b2"
    assert len(primary.calls) == 3


async def test_half_open_probe_success_recovers():
    wrapper, _, _, now = _wrapper(["down"] * 3 + ["recovered"], ["b1"])
    for _ in range(3):
        try:
            await wrapper(DOC, "i", FieldsOutput)
        except ProviderDown:
            pass
    assert wrapper.state == "open"
    now["t"] += COOLDOWN_SECONDS
    assert await wrapper(DOC, "i", FieldsOutput) == "recovered"
    assert wrapper.state == "closed"


async def test_half_open_probe_failure_reopens_with_fresh_cooldown():
    wrapper, primary, _backup, now = _wrapper(["down"] * 4, ["b1", "b2", "b3"])
    for _ in range(3):
        try:
            await wrapper(DOC, "i", FieldsOutput)
        except ProviderDown:
            pass
    now["t"] += COOLDOWN_SECONDS
    assert await wrapper(DOC, "i", FieldsOutput) == "b2"
    assert wrapper.state == "open"
    now["t"] += COOLDOWN_SECONDS - 1
    assert await wrapper(DOC, "i", FieldsOutput) == "b3"
    assert len(primary.calls) == 4


async def test_half_open_probe_content_error_closes():
    wrapper, _, _, now = _wrapper(["down"] * 3 + ["bad"], ["b1"])
    for _ in range(3):
        try:
            await wrapper(DOC, "i", FieldsOutput)
        except ProviderDown:
            pass
    now["t"] += COOLDOWN_SECONDS
    with pytest.raises(JudgeError):
        await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed"


async def test_double_failure_raises_and_stays_open():
    wrapper, _, _, _ = _wrapper(["down"] * 3, ["down"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    with pytest.raises(ProviderDown):
        await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "open"


async def test_without_backup_downs_raise_and_never_trip():
    wrapper, _, _, _ = _wrapper(["down"] * 5)
    for _ in range(5):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed"


async def test_drain_reports_first_use_order_then_clears():
    wrapper, _, _, _ = _wrapper(["p1", "down", "down", "down"], ["b1"])
    await wrapper(DOC, "i", FieldsOutput)
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.drain_models_used() == ["claude-sonnet-5", "openai:gpt-5.6-terra"]
    assert wrapper.drain_models_used() == []


async def test_active_model_follows_state():
    wrapper, _, _, _ = _wrapper(["down"] * 3, ["b1"])
    assert wrapper.active_model == "claude-sonnet-5"
    for _ in range(3):
        try:
            await wrapper(DOC, "i", FieldsOutput)
        except ProviderDown:
            pass
    assert wrapper.active_model == "openai:gpt-5.6-terra"


def test_make_judge_returns_wrapper_without_backup(monkeypatch):
    from app.core.config import settings
    from app.llm import client as client_module

    monkeypatch.setattr(settings, "clause_audit_failover_model", "")
    judge = client_module.make_judge()
    assert isinstance(judge, FailoverJudge)
    assert judge.active_model == settings.clause_audit_model


def test_make_judge_wires_backup_when_configured(monkeypatch):
    from app.core.config import settings
    from app.llm import client as client_module

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "clause_audit_failover_model", "openai:gpt-5.6-terra")
    judge = client_module.make_judge()
    assert isinstance(judge, FailoverJudge)
    assert judge.state == "closed"
