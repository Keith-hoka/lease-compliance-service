# Renewal Rent Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /v1/rent-suggestions` — a deterministic market/legal
range plus an LLM-chosen figure and reasoning — and a SaaS renew-page
button that fills the rent field from it.

**Architecture:** New `app/rent_suggest/` package: `anchor.py`
(market band ∩ cap band -> range + gap, from `rent_statistics`),
`law.py` (hypothetical audit through the existing rule engine ->
law card), `judge.py` (one failover-judge call with a per-request
output model whose `suggested_weekly` is bounded by the range; skipped
when the range is degenerate), `service.py` (composition), router +
schemas. The judge is fed the evidence block as a text `DocumentInput`
under a dedicated system prompt. Spec:
`docs/superpowers/specs/2026-08-17-rent-suggestions-design.md`.

**Tech Stack:** FastAPI, async SQLAlchemy, pydantic v2, existing
`app.llm.client.make_judge` (FailoverJudge), pytest; SaaS: FastAPI proxy
+ Next.js renew page + Playwright.

## Global Constraints

- `uv` only; ruff sequence in exact order before every push (`uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`); TDD; no emojis; docstrings over comments; commit trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Full suite `uv run pytest -m "not llm_eval" -q`.
- Money is `Decimal`; range bounds and suggestion rounded to whole dollars (`quantize(Decimal("1"), ROUND_HALF_UP)`); wire format strings like `"630.00"`? NO — this endpoint returns whole-dollar Decimals serialised by pydantic as strings `"630"`; tests assert on `Decimal(...)` equality of parsed values, not on string formatting.
- Constants exactly: `CAP_RATIO = Decimal("1.15")`, `VIC_BAND = Decimal("0.08")`, `THIN_SAMPLE = 10`.
- Market band: NSW newest row for (jurisdiction, area_key, dwelling_type, bedrooms); if `sample_size < THIN_SAMPLE` fall back to `bedrooms IS NULL` same type (`fallback="bedrooms_all"`), then to `dwelling_type='all'` bedrooms NULL (`fallback="dwelling_all"`); band [p25, p75]. VIC newest row for the exact cell, fallback only to `('all', NULL)`; band [median×(1-VIC_BAND), median×(1+VIC_BAND)]. No row anywhere -> `no_data`.
- Cap band [current, current×CAP_RATIO]. Range = intersection; market entirely above cap -> range = cap band, `above_cap`; market entirely below current -> [current, current], `below_current`; `no_data` -> cap band.
- Law card: hypothetical increase = range midpoint (in the lease's own frequency, converted back from weekly), effective on `renewal_start`, appended to `lease.rent_increases`; run existing `run_audit`; keep findings whose rule_id contains `rent_increase` or `fixed_term_increase`; any red -> `law_blocked=True`, range collapses to [current, current].
- LLM skipped when `range.low == range.high`; suggestion = current, template reasoning. Otherwise one `make_judge()` call; output model `suggested_weekly: Decimal = Field(ge=low, le=high)`, `reasoning: str`. Judge failure -> HTTP 502 `{"detail": {"code": "judge_unavailable"}}`.
- Prompt: dedicated `RENT_SUGGESTION_SYSTEM`; evidence block = range + derivation, last 4 market periods for the cell used, renewal-chain history (past rents + increase %), property attributes, law card summaries; instructions: choose within range, 2–3 sentences, cite only supplied numbers, `above_cap` -> upper part of range + note staged approach; newest market period older than 6 months -> say so. No tenant identity anywhere (LeaseInput carries none).
- Endpoint auth: `TenantDep` + router-level `enforce_rate_limit`; usage class `rent_suggestions`; response includes `model` (the ref that judged, from `judge.drain_models_used()` or the primary ref when the LLM was skipped -> `null`), `engine_version`, `disclaimer` "General information, not legal advice."
- Eval: deterministic exact-assert cases (no LLM) + `-m llm_eval` golden scenarios asserting in-range, every money figure in reasoning ∈ evidence set, direction property; gate ≥ 0.9 all-properties pass rate; both primary and backup model refs run once; recorded in `docs/model-evals.md`.
- SaaS: proxy `POST /api/v1/leases/{lease_id}/rent-suggestion` (manager roles + `require_ai_consent(AiFeature.rent_ai)`), renew-page "Suggest rent" button, result card with law card, market line, VIC CC BY 4.0 attribution, "Use suggestion" fills the rent field converted to the form frequency; Playwright with the service mocked.
- Tasks 1–5 service (subagent), Task 6 SaaS (subagent), Task 7 eval + rollout (controller).

---

### Task 1: Market anchoring

**Files:**
- Create: `app/rent_suggest/__init__.py` (empty), `app/rent_suggest/anchor.py`
- Test: `tests/test_rent_suggest_anchor.py`

**Interfaces:**
- Consumes: `RentStatistic` model (`app.models`), `to_weekly_rent` (`app.rules.base`).
- Produces:
  ```python
  CAP_RATIO = Decimal("1.15"); VIC_BAND = Decimal("0.08"); THIN_SAMPLE = 10
  @dataclass(frozen=True)
  class MarketCell: period: str; median: Decimal; p25: Decimal | None; p75: Decimal | None; sample_size: int; fallback: str | None; series: list[RentStatistic]  # newest first, up to 4
  @dataclass(frozen=True)
  class Anchor: current_weekly: Decimal; low: Decimal; high: Decimal; gap: str; market: MarketCell | None
  async def market_cell(session, jurisdiction, area_key, dwelling_type, bedrooms) -> MarketCell | None
  def band_for(jurisdiction, cell: MarketCell) -> tuple[Decimal, Decimal]
  def anchor(current_weekly: Decimal, jurisdiction: str, cell: MarketCell | None) -> Anchor
  def dollars(value: Decimal) -> Decimal  # quantize to whole dollars, ROUND_HALF_UP
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rent_suggest_anchor.py`:

```python
from decimal import Decimal

from app.models import RentStatistic
from app.rent_suggest.anchor import MarketCell, anchor, band_for, dollars, market_cell


def _cell(**kw) -> MarketCell:
    base = dict(
        period="2026-07",
        median=Decimal("760"),
        p25=Decimal("697.5"),
        p75=Decimal("886.25"),
        sample_size=170,
        fallback=None,
        series=[],
    )
    base.update(kw)
    return MarketCell(**base)


def test_dollars_rounds_half_up():
    assert dollars(Decimal("697.5")) == Decimal("698")
    assert dollars(Decimal("886.25")) == Decimal("886")


def test_nsw_band_is_p25_p75_and_vic_band_is_median_pm_8pct():
    assert band_for("NSW", _cell()) == (Decimal("698"), Decimal("886"))
    vic = _cell(median=Decimal("643"), p25=None, p75=None)
    assert band_for("VIC", vic) == (Decimal("592"), Decimal("694"))


def test_within_intersects_market_and_cap_bands():
    result = anchor(Decimal("650"), "NSW", _cell())
    assert (result.low, result.high, result.gap) == (Decimal("698"), Decimal("748"), "within")


def test_above_cap_uses_cap_band():
    result = anchor(Decimal("500"), "NSW", _cell())
    assert (result.low, result.high, result.gap) == (Decimal("500"), Decimal("575"), "above_cap")


def test_below_current_collapses_to_current():
    result = anchor(Decimal("950"), "NSW", _cell())
    assert (result.low, result.high, result.gap) == (
        Decimal("950"),
        Decimal("950"),
        "below_current",
    )


def test_no_data_uses_cap_band():
    result = anchor(Decimal("600"), "NSW", None)
    assert (result.low, result.high, result.gap, result.market) == (
        Decimal("600"),
        Decimal("690"),
        "no_data",
        None,
    )


async def test_market_cell_prefers_exact_then_falls_back_when_thin(db_session):
    rows = [
        RentStatistic(
            jurisdiction="NSW",
            period="2026-07",
            area_code="2000",
            dwelling_type="unit",
            bedrooms=2,
            median=Decimal("800"),
            p25=Decimal("750"),
            p75=Decimal("850"),
            sample_size=4,
            source_url="u",
        ),
        RentStatistic(
            jurisdiction="NSW",
            period="2026-07",
            area_code="2000",
            dwelling_type="unit",
            bedrooms=None,
            median=Decimal("760"),
            p25=Decimal("697.5"),
            p75=Decimal("886.25"),
            sample_size=170,
            source_url="u",
        ),
        RentStatistic(
            jurisdiction="NSW",
            period="2026-06",
            area_code="2000",
            dwelling_type="unit",
            bedrooms=None,
            median=Decimal("750"),
            p25=Decimal("690"),
            p75=Decimal("880"),
            sample_size=160,
            source_url="u",
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    cell = await market_cell(db_session, "NSW", "2000", "unit", 2)
    assert cell.fallback == "bedrooms_all" and cell.sample_size == 170
    assert [s.period for s in cell.series] == ["2026-07", "2026-06"]
    exact = await market_cell(db_session, "NSW", "2000", "unit", None)
    assert exact.fallback is None and exact.period == "2026-07"
    assert await market_cell(db_session, "NSW", "9999", "unit", 2) is None


async def test_vic_falls_back_only_to_all(db_session):
    db_session.add(
        RentStatistic(
            jurisdiction="VIC",
            period="2025-Q3",
            area_code="Carlton",
            dwelling_type="all",
            bedrooms=None,
            median=Decimal("600"),
            p25=None,
            p75=None,
            sample_size=900,
            source_url="u",
        )
    )
    await db_session.commit()
    cell = await market_cell(db_session, "VIC", "Carlton", "unit", 2)
    assert cell.fallback == "dwelling_all" and cell.median == Decimal("600")
```

(Arithmetic: NSW band [698, 886]; cap for 650 = [650, 747.5] -> intersection [698, 747.5] -> dollars 748; cap for 500 = [500, 575] entirely below 698 -> above_cap; 950 > 886 -> below_current; VIC 643×0.92 = 591.56 -> 592, ×1.08 = 694.44 -> 694.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rent_suggest_anchor.py -v`
Expected: FAIL — `ModuleNotFoundError: app.rent_suggest`.

- [ ] **Step 3: Implement**

Create empty `app/rent_suggest/__init__.py`. Create `app/rent_suggest/anchor.py`:

```python
"""Deterministic market anchoring: a suggestion range from statistics and a cap."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RentStatistic

CAP_RATIO = Decimal("1.15")
VIC_BAND = Decimal("0.08")
THIN_SAMPLE = 10
SERIES_PERIODS = 4


@dataclass(frozen=True)
class MarketCell:
    period: str
    median: Decimal
    p25: Decimal | None
    p75: Decimal | None
    sample_size: int
    fallback: str | None
    series: list[RentStatistic]


@dataclass(frozen=True)
class Anchor:
    current_weekly: Decimal
    low: Decimal
    high: Decimal
    gap: str
    market: MarketCell | None


def dollars(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


async def _series(session, jurisdiction, area_key, dwelling_type, bedrooms):
    stmt = (
        select(RentStatistic)
        .where(
            RentStatistic.jurisdiction == jurisdiction,
            RentStatistic.area_code == area_key,
            RentStatistic.dwelling_type == dwelling_type,
            RentStatistic.bedrooms.is_(None)
            if bedrooms is None
            else RentStatistic.bedrooms == bedrooms,
        )
        .order_by(RentStatistic.period.desc())
        .limit(SERIES_PERIODS)
    )
    return list((await session.execute(stmt)).scalars().all())


def _candidates(jurisdiction, dwelling_type, bedrooms):
    """(dwelling_type, bedrooms, fallback label) in preference order."""
    exact = [(dwelling_type, bedrooms, None)]
    if jurisdiction == "NSW":
        return exact + [(dwelling_type, None, "bedrooms_all"), ("all", None, "dwelling_all")]
    return exact + [("all", None, "dwelling_all")]


async def market_cell(
    session: AsyncSession,
    jurisdiction: str,
    area_key: str,
    dwelling_type: str,
    bedrooms: int | None,
) -> MarketCell | None:
    thin: MarketCell | None = None
    for dtype, beds, fallback in _candidates(jurisdiction, dwelling_type, bedrooms):
        if (dtype, beds) == (dwelling_type, bedrooms) and fallback is not None:
            continue
        rows = await _series(session, jurisdiction, area_key, dtype, beds)
        if not rows:
            continue
        newest = rows[0]
        cell = MarketCell(
            period=newest.period,
            median=newest.median,
            p25=newest.p25,
            p75=newest.p75,
            sample_size=newest.sample_size,
            fallback=fallback,
            series=rows,
        )
        if newest.sample_size >= THIN_SAMPLE:
            return cell
        thin = thin or cell
    return thin


def band_for(jurisdiction: str, cell: MarketCell) -> tuple[Decimal, Decimal]:
    if jurisdiction == "NSW" and cell.p25 is not None and cell.p75 is not None:
        return dollars(cell.p25), dollars(cell.p75)
    return dollars(cell.median * (1 - VIC_BAND)), dollars(cell.median * (1 + VIC_BAND))


def anchor(current_weekly: Decimal, jurisdiction: str, cell: MarketCell | None) -> Anchor:
    current = dollars(current_weekly)
    cap_high = dollars(current * CAP_RATIO)
    if cell is None:
        return Anchor(current, current, cap_high, "no_data", None)
    market_low, market_high = band_for(jurisdiction, cell)
    if market_low > cap_high:
        return Anchor(current, current, cap_high, "above_cap", cell)
    if market_high < current:
        return Anchor(current, current, current, "below_current", cell)
    return Anchor(current, max(current, market_low), min(cap_high, market_high), "within", cell)
```

Note on the thin-sample rule: a thin exact cell is remembered and only
returned if no thicker fallback exists (so a thin exact cell beats no
data, but a thick rollup beats a thin exact cell). The
`_candidates` guard skips the degenerate case where the exact cell IS
the rollup (bedrooms already None) so `fallback` stays `None` there.

- [ ] **Step 4: Run tests, full suite, ruff, commit**

Run: `uv run pytest tests/test_rent_suggest_anchor.py -v` — Expected: 8 PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/rent_suggest tests/test_rent_suggest_anchor.py
git commit -m "Anchor rent suggestions to market statistics and a cap band"
```

---

### Task 2: Law card via hypothetical audit

**Files:**
- Create: `app/rent_suggest/law.py`
- Test: `tests/test_rent_suggest_law.py`

**Interfaces:**
- Consumes: `run_audit(session, jurisdiction, as_at, lease) -> list[Finding]` (`app.rules.engine`), `LeaseInput`/`RentIncrease` (`app.schemas.lease`), `Finding` (`app.rules.base`), `dollars` (Task 1).
- Produces:
  ```python
  @dataclass(frozen=True)
  class LawCard: findings: list[Finding]; blocked: bool
  def from_weekly(weekly: Decimal, frequency: str) -> Decimal   # inverse of to_weekly_rent, whole dollars
  async def law_card(session, jurisdiction, as_at, lease: LeaseInput, renewal_start: date, proposed_weekly: Decimal) -> LawCard
  RENT_RULE_MARKERS = ("rent_increase", "fixed_term_increase")
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rent_suggest_law.py` (the seeded corpus fixture from
`tests/conftest.py` gives `run_audit` its statutes — check how
`tests/test_engine.py` seeds/uses the DB for NSW rules and mirror it; if
the engine works without corpus rows for these rules, no seeding needed):

```python
from datetime import date
from decimal import Decimal

from app.rent_suggest.law import RENT_RULE_MARKERS, from_weekly, law_card
from app.schemas.lease import LeaseInput, RentIncrease


def test_from_weekly_inverts_to_weekly():
    assert from_weekly(Decimal("600"), "weekly") == Decimal("600")
    assert from_weekly(Decimal("600"), "fortnightly") == Decimal("1200")
    assert from_weekly(Decimal("600"), "monthly") == Decimal("2600")


async def test_law_card_green_when_increase_is_lawful(db_session):
    lease = LeaseInput(
        rent_amount=Decimal("600"),
        rent_frequency="weekly",
        start_date=date(2024, 10, 1),
        end_date=date(2026, 9, 30),
    )
    card = await law_card(
        db_session, "NSW", date(2026, 8, 17), lease, date(2026, 10, 1), Decimal("630")
    )
    assert card.blocked is False
    assert card.findings and all(
        any(m in f.rule_id for m in RENT_RULE_MARKERS) for f in card.findings
    )
    assert {f.verdict for f in card.findings} <= {"green", "skipped"}


async def test_law_card_blocked_by_frequency_rule(db_session):
    lease = LeaseInput(
        rent_amount=Decimal("600"),
        rent_frequency="weekly",
        start_date=date(2024, 10, 1),
        end_date=date(2026, 9, 30),
        rent_increases=[RentIncrease(effective_on=date(2026, 4, 1), new_amount=Decimal("600"))],
    )
    card = await law_card(
        db_session, "NSW", date(2026, 8, 17), lease, date(2026, 10, 1), Decimal("630")
    )
    assert card.blocked is True
    red = [f for f in card.findings if f.verdict == "red"]
    assert red and red[0].rule_id == "nsw.rent_increase_frequency"
```

(2026-04-01 -> 2026-10-01 is six months, inside NSW's 12-month
frequency rule; the first case has no prior increase and the tenancy is
older than 12 months so frequency and first-year rules pass. If the
engine's rule set makes a different rule fire red first, adjust the
expected rule_id to the actual red and note it in the report — the
contract is "the frequency scenario blocks".)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rent_suggest_law.py -v`
Expected: FAIL — `ModuleNotFoundError: app.rent_suggest.law`.

- [ ] **Step 3: Implement**

Create `app/rent_suggest/law.py`:

```python
"""Law card: the existing rent-increase rules run against a hypothetical increase."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.rent_suggest.anchor import dollars
from app.rules.base import Finding
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput, RentIncrease

RENT_RULE_MARKERS = ("rent_increase", "fixed_term_increase")
_PERIODS_PER_YEAR = {"weekly": Decimal(52), "fortnightly": Decimal(26), "monthly": Decimal(12)}


def from_weekly(weekly: Decimal, frequency: str) -> Decimal:
    """Weekly amount expressed in the lease's own payment frequency."""
    return dollars(weekly * Decimal(52) / _PERIODS_PER_YEAR[frequency])


@dataclass(frozen=True)
class LawCard:
    findings: list[Finding]
    blocked: bool


async def law_card(
    session: AsyncSession,
    jurisdiction: str,
    as_at: date,
    lease: LeaseInput,
    renewal_start: date,
    proposed_weekly: Decimal,
) -> LawCard:
    proposed = RentIncrease(
        effective_on=renewal_start, new_amount=from_weekly(proposed_weekly, lease.rent_frequency)
    )
    hypothetical = lease.model_copy(
        update={"rent_increases": [*(lease.rent_increases or []), proposed]}
    )
    findings = await run_audit(session, jurisdiction, as_at, hypothetical)
    relevant = [f for f in findings if any(m in f.rule_id for m in RENT_RULE_MARKERS)]
    return LawCard(findings=relevant, blocked=any(f.verdict == "red" for f in relevant))
```

- [ ] **Step 4: Run tests, full suite, ruff, commit**

Run: `uv run pytest tests/test_rent_suggest_law.py -v` — Expected: 3 PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/rent_suggest/law.py tests/test_rent_suggest_law.py
git commit -m "Build the rent-suggestion law card from a hypothetical audit"
```

---

### Task 3: Prompt, output model, judge wrapper

**Files:**
- Modify: `app/llm/prompts.py` (append `RENT_SUGGESTION_SYSTEM`, `rent_suggestion_instruction`)
- Create: `app/rent_suggest/judge.py`
- Test: `tests/test_rent_suggest_judge.py`

**Interfaces:**
- Consumes: `Anchor`, `MarketCell` (Task 1), `LawCard` (Task 2), `JudgeFn`/`FailoverJudge` (`app.llm.failover`), `DocumentInput` (`app.clause_audit.document`), `LeaseInput`.
- Produces:
  ```python
  RENT_SUGGESTION_SYSTEM: str
  def rent_suggestion_instruction(low: Decimal, high: Decimal, gap: str) -> str
  def evidence_block(anchor: Anchor, jurisdiction: str, lease: LeaseInput, law: LawCard, property_desc: str) -> str
  def suggestion_output_model(low: Decimal, high: Decimal) -> type[BaseModel]   # fields suggested_weekly (ge/le), reasoning
  def evidence_numbers(anchor, lease, law) -> set[Decimal]   # every money figure the prompt exposes (used by eval)
  @dataclass(frozen=True)
  class Suggestion: suggested_weekly: Decimal; reasoning: str; model: str | None
  async def suggest(judge, anchor, jurisdiction, lease, law, property_desc) -> Suggestion
  HOLD_REASON_BLOCKED / HOLD_REASON_BELOW: str templates
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rent_suggest_judge.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.clause_audit.document import DocumentInput
from app.llm.failover import FailoverJudge
from app.llm.prompts import RENT_SUGGESTION_SYSTEM, rent_suggestion_instruction
from app.rent_suggest.anchor import Anchor, MarketCell
from app.rent_suggest.judge import (
    HOLD_REASON_BLOCKED,
    evidence_block,
    evidence_numbers,
    suggest,
    suggestion_output_model,
)
from app.rent_suggest.law import LawCard
from app.rules.base import Finding
from app.schemas.lease import LeaseInput, RentIncrease

LEASE = LeaseInput(
    rent_amount=Decimal("600"),
    rent_frequency="weekly",
    start_date=date(2024, 10, 1),
    end_date=date(2026, 9, 30),
    rent_increases=[RentIncrease(effective_on=date(2025, 10, 1), new_amount=Decimal("600"))],
)
CELL = MarketCell(
    period="2026-07",
    median=Decimal("760"),
    p25=Decimal("697.5"),
    p75=Decimal("886.25"),
    sample_size=170,
    fallback=None,
    series=[],
)
ANCHOR = Anchor(Decimal("650"), Decimal("698"), Decimal("748"), "within", CELL)
LAW = LawCard(
    findings=[
        Finding(
            rule_id="nsw.rent_increase_frequency",
            verdict="green",
            summary="Increases at least 12 months apart.",
        )
    ],
    blocked=False,
)


def test_output_model_bounds_the_suggestion():
    model = suggestion_output_model(Decimal("698"), Decimal("748"))
    ok = model.model_validate({"suggested_weekly": "720", "reasoning": "x"})
    assert ok.suggested_weekly == Decimal("720")
    with pytest.raises(ValidationError):
        model.model_validate({"suggested_weekly": "760", "reasoning": "x"})
    with pytest.raises(ValidationError):
        model.model_validate({"suggested_weekly": "690", "reasoning": "x"})


def test_evidence_block_carries_numbers_and_no_tenant_fields():
    text = evidence_block(ANCHOR, "NSW", LEASE, LAW, "unit, 2 bedrooms, postcode 2000")
    for token in (
        "698",
        "748",
        "760",
        "170",
        "2026-07",
        "650",
        "600",
        "nsw.rent_increase_frequency",
    ):
        assert token in text
    assert "tenant" not in text.lower() or "tenancy" in text.lower()


def test_evidence_numbers_collects_every_money_figure():
    numbers = evidence_numbers(ANCHOR, LEASE, LAW)
    assert {
        Decimal("600"),
        Decimal("650"),
        Decimal("698"),
        Decimal("748"),
        Decimal("760"),
        Decimal("697.5"),
        Decimal("886.25"),
    } <= numbers


def test_system_and_instruction_mention_range_and_citation_rule():
    assert "general information" in RENT_SUGGESTION_SYSTEM.lower()
    text = rent_suggestion_instruction(Decimal("698"), Decimal("748"), "above_cap")
    assert "698" in text and "748" in text and "upper" in text.lower()


async def test_suggest_skips_the_model_when_range_is_degenerate():
    calls = []

    async def never(doc, instruction, output_model):
        calls.append(1)

    judge = FailoverJudge(primary=never, primary_ref="claude-sonnet-5")
    held = Anchor(Decimal("600"), Decimal("600"), Decimal("600"), "within", CELL)
    blocked = LawCard(findings=[], blocked=True)
    result = await suggest(judge, held, "NSW", LEASE, blocked, "unit")
    assert result.suggested_weekly == Decimal("600") and result.model is None
    assert result.reasoning == HOLD_REASON_BLOCKED and calls == []


async def test_suggest_calls_the_judge_with_evidence_and_records_model():
    seen = {}

    async def fake(doc, instruction, output_model):
        seen["doc"] = doc
        seen["instruction"] = instruction
        return output_model.model_validate(
            {"suggested_weekly": "720", "reasoning": "Market median 760."}
        )

    judge = FailoverJudge(primary=fake, primary_ref="claude-sonnet-5")
    live = Anchor(Decimal("600"), Decimal("698"), Decimal("748"), "within", CELL)
    result = await suggest(judge, live, "NSW", LEASE, LAW, "unit")
    assert result.suggested_weekly == Decimal("720") and result.model == "claude-sonnet-5"
    assert isinstance(seen["doc"], DocumentInput) and seen["doc"].kind == "text"
    assert "760" in seen["doc"].text and "698" in seen["instruction"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rent_suggest_judge.py -v`
Expected: FAIL — `ImportError` (prompts + judge symbols).

- [ ] **Step 3: Implement prompts**

Append to `app/llm/prompts.py`:

```python
RENT_SUGGESTION_SYSTEM = (
    "You help an Australian landlord choose a renewal rent. The evidence is "
    "supplied between <evidence> tags: a pre-computed allowed range, official "
    "bond-derived market statistics, the lease's own rent history, property "
    "attributes, and a legal check already performed by deterministic rules. "
    "Choose one weekly figure inside the allowed range and explain it in two or "
    "three sentences. Cite only numbers that appear in the evidence; never "
    "introduce market figures from memory. Your output is general information, "
    "not legal advice."
)


def rent_suggestion_instruction(low, high, gap: str) -> str:
    steer = {
        "above_cap": (
            " The market band sits above the cap, so choose from the upper part of the "
            "range and note that a staged approach may follow at the next renewal."
        ),
        "within": "",
        "no_data": " No market statistics exist for this area; say so and stay conservative.",
    }[gap]
    return (
        f"Choose suggested_weekly between {low} and {high} inclusive (whole dollars)."
        f"{steer} If the newest market period is more than six months old, say so. "
        "Write reasoning as two or three sentences citing only supplied numbers."
    )
```

- [ ] **Step 4: Implement the judge wrapper**

Create `app/rent_suggest/judge.py`:

```python
"""One judge call that picks a figure inside a pre-computed range and explains it."""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field, create_model

from app.clause_audit.document import DocumentInput
from app.llm.failover import FailoverJudge
from app.llm.prompts import rent_suggestion_instruction
from app.rent_suggest.anchor import Anchor
from app.rent_suggest.law import LawCard
from app.schemas.lease import LeaseInput

HOLD_REASON_BLOCKED = (
    "A rent increase at this renewal would breach a rent-increase rule (see the law card), "
    "so the suggestion is to hold the current rent."
)
HOLD_REASON_BELOW = (
    "The market band for comparable properties sits below the current rent, so the "
    "suggestion is to hold the current rent."
)


@dataclass(frozen=True)
class Suggestion:
    suggested_weekly: Decimal
    reasoning: str
    model: str | None


def suggestion_output_model(low: Decimal, high: Decimal) -> type[BaseModel]:
    return create_model(
        "RentSuggestionOutput",
        suggested_weekly=(Decimal, Field(ge=low, le=high)),
        reasoning=(str, ...),
    )


def evidence_numbers(anchor: Anchor, lease: LeaseInput, law: LawCard) -> set[Decimal]:
    numbers = {anchor.current_weekly, anchor.low, anchor.high, lease.rent_amount}
    for inc in lease.rent_increases or []:
        numbers.add(inc.new_amount)
    if anchor.market:
        for row in anchor.market.series or [anchor.market]:
            for value in (row.median, row.p25, row.p75):
                if value is not None:
                    numbers.add(Decimal(value))
        numbers.update(
            {anchor.market.median, *(v for v in (anchor.market.p25, anchor.market.p75) if v)}
        )
    return numbers


def _history_lines(lease: LeaseInput) -> list[str]:
    lines = [
        f"- tenancy started {lease.start_date:%Y-%m}; current rent {lease.rent_amount} per {lease.rent_frequency}"
    ]
    previous = None
    for inc in lease.rent_increases or []:
        pct = f" (+{((inc.new_amount / previous) - 1) * 100:.1f}%)" if previous else ""
        lines.append(f"- {inc.effective_on:%Y-%m}: rent {inc.new_amount}{pct}")
        previous = inc.new_amount
    return lines


def evidence_block(
    anchor: Anchor, jurisdiction: str, lease: LeaseInput, law: LawCard, property_desc: str
) -> str:
    parts = [
        "<evidence>",
        f"Property: {property_desc} ({jurisdiction}).",
        f"Current weekly rent: {anchor.current_weekly}.",
        f"Allowed range: {anchor.low} to {anchor.high} weekly; market gap: {anchor.gap}.",
    ]
    if anchor.market:
        cell = anchor.market
        note = f" (fallback: {cell.fallback})" if cell.fallback else ""
        parts.append(f"Market cell used: period {cell.period}, sample {cell.sample_size}{note}.")
        for row in cell.series or []:
            pct = f", p25 {row.p25}, p75 {row.p75}" if row.p25 is not None else ""
            parts.append(f"- {row.period}: median {row.median}{pct}, n={row.sample_size}")
    else:
        parts.append("Market: no statistics for this area.")
    parts.append("Rent history:")
    parts.extend(_history_lines(lease))
    parts.append("Legal check (deterministic rules):")
    parts.extend(f"- {f.rule_id}: {f.verdict} - {f.summary}" for f in law.findings)
    parts.append("</evidence>")
    return "\n".join(parts)


async def suggest(
    judge: FailoverJudge,
    anchor: Anchor,
    jurisdiction: str,
    lease: LeaseInput,
    law: LawCard,
    property_desc: str,
) -> Suggestion:
    if anchor.low == anchor.high:
        reason = HOLD_REASON_BLOCKED if law.blocked else HOLD_REASON_BELOW
        return Suggestion(anchor.current_weekly, reason, None)
    doc = DocumentInput(
        kind="text", text=evidence_block(anchor, jurisdiction, lease, law, property_desc)
    )
    output = await judge(
        doc,
        rent_suggestion_instruction(anchor.low, anchor.high, anchor.gap),
        suggestion_output_model(anchor.low, anchor.high),
    )
    used = judge.drain_models_used()
    return Suggestion(output.suggested_weekly, output.reasoning, used[-1] if used else None)
```

Note: `evidence_numbers` includes `anchor.market.series` rows only if the
caller populated `series` (Task 1's `market_cell` does; hand-built cells
in tests may pass `series=[]`, in which case the newest cell's own
median/p25/p75 are still included). The system prompt is passed how?
`make_judge()`'s adapters hardcode `SYSTEM` — see Task 4 for the
one-line change that lets the judge take a system prompt.

- [ ] **Step 5: Run tests, full suite, ruff, commit**

Run: `uv run pytest tests/test_rent_suggest_judge.py -v` — Expected: 6 PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/llm/prompts.py app/rent_suggest/judge.py tests/test_rent_suggest_judge.py
git commit -m "Add the rent-suggestion prompt, bounded output model, and judge wrapper"
```

---

### Task 4: System-prompt selection in the adapters, service composition, endpoint

**Files:**
- Modify: `app/llm/providers/anthropic.py`, `app/llm/providers/openai_.py` (system prompt chosen per call), `app/llm/failover.py` (pass-through), `app/llm/prompts.py` (no change) — see Step 3 design note
- Create: `app/rent_suggest/service.py`, `app/schemas/rent_suggestions.py`, `app/routers/rent_suggestions.py`
- Modify: `app/main.py` (include router)
- Test: `tests/test_rent_suggestions_api.py`, `tests/test_llm_providers.py` (system selection)

**Interfaces:**
- Consumes: Tasks 1–3; `TenantDep`, `enforce_rate_limit`, `record_usage`, `ENGINE_VERSION`, `make_judge`.
- Produces: `POST /v1/rent-suggestions`; `RentSuggestionRequest`/`RentSuggestionResponse` schemas; `async build_suggestion(session, judge, request) -> RentSuggestionResponse`.

- [ ] **Step 1: Design note — system prompt per call (read before coding)**

The adapters build the request with `system=SYSTEM`. The rent judge needs
`RENT_SUGGESTION_SYSTEM`. Smallest change consistent with the frozen
`JudgeFn` signature: encode the system prompt choice in the
`DocumentInput` — add an optional field `system: str | None = None` to
`DocumentInput` (dataclass default; existing callers untouched) and have
both adapters use `doc.system or SYSTEM`. `FailoverJudge` passes `doc`
through unchanged. Tests: one per adapter asserting the create kwargs
carry `system == doc.system` when set and `SYSTEM` when not (extend the
existing `test_*_create_kwargs` tests in `tests/test_llm_providers.py`).

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_llm_providers.py`:

```python
def test_anthropic_uses_document_system_prompt_when_given():
    doc = DocumentInput(kind="text", text="lease body", system="You are a rent adviser.")
    kwargs = anthropic_provider.build_create_kwargs("claude-sonnet-5", doc, "i", FieldsOutput)
    assert kwargs["system"] == "You are a rent adviser."
    default = anthropic_provider.build_create_kwargs("claude-sonnet-5", DOC, "i", FieldsOutput)
    assert default["system"] == SYSTEM


def test_openai_uses_document_system_prompt_when_given():
    doc = DocumentInput(kind="text", text="lease body", system="You are a rent adviser.")
    kwargs = openai_provider.build_response_kwargs("gpt-5.6-terra", doc, "i", FieldsOutput)
    assert kwargs["instructions"] == "You are a rent adviser."
```

(import `SYSTEM` from `app.llm.prompts` in that test file if absent.)

Create `tests/test_rent_suggestions_api.py` (auth per `tests/test_clause_api.py`: `seeded_tenants` + `{"X-API-Key": "test-key"}`; the judge is monkeypatched at the router's `make_judge` reference — see Step 3 for the hook):

```python
from decimal import Decimal

from app.models import RentStatistic, UsageCounter
from app.rent_suggest import service as suggest_service
from app.llm.failover import FailoverJudge

KEY = {"X-API-Key": "test-key"}
BODY = {
    "jurisdiction": "NSW",
    "as_at": "2026-08-17",
    "property": {"area_key": "2000", "dwelling_type": "unit", "bedrooms": 2},
    "lease": {
        "rent_amount": "600",
        "rent_frequency": "weekly",
        "start_date": "2024-10-01",
        "end_date": "2026-09-30",
    },
    "renewal_start": "2026-10-01",
}


async def _seed_market(session):
    session.add(
        RentStatistic(
            jurisdiction="NSW",
            period="2026-07",
            area_code="2000",
            dwelling_type="unit",
            bedrooms=2,
            median=Decimal("760"),
            p25=Decimal("697.5"),
            p75=Decimal("886.25"),
            sample_size=170,
            source_url="u",
        )
    )
    await session.commit()


def _fake_judge(monkeypatch, weekly="720", reasoning="Median 760 supports 720."):
    async def fake(doc, instruction, output_model):
        return output_model.model_validate({"suggested_weekly": weekly, "reasoning": reasoning})

    monkeypatch.setattr(
        suggest_service, "make_judge", lambda: FailoverJudge(primary=fake, primary_ref="fake-model")
    )


async def test_requires_api_key(client):
    assert (await client.post("/v1/rent-suggestions", json=BODY)).status_code == 401


async def test_full_response_shape(client, db_session, seeded_tenants, monkeypatch):
    await _seed_market(db_session)
    _fake_judge(monkeypatch, weekly="650", reasoning="Cap 690 binds below the market band.")
    response = await client.post("/v1/rent-suggestions", json=BODY, headers=KEY)
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["current_weekly"]) == Decimal("600")
    assert (Decimal(body["range"]["low"]), Decimal(body["range"]["high"])) == (
        Decimal("600"),
        Decimal("690"),
    )
    assert body["market_gap"] == "above_cap"
    assert Decimal(body["suggested_weekly"]) == Decimal("650")
    assert body["market"]["period"] == "2026-07" and body["market"]["sample_size"] == 170
    assert body["law_blocked"] is False and body["law_card"]
    assert (
        body["model"] == "fake-model"
        and body["disclaimer"] == "General information, not legal advice."
    )
    assert body["engine_version"]


async def test_no_market_data_uses_cap_band(client, db_session, seeded_tenants, monkeypatch):
    _fake_judge(monkeypatch, weekly="650")
    response = await client.post("/v1/rent-suggestions", json=BODY, headers=KEY)
    body = response.json()
    assert body["market_gap"] == "no_data" and body["market"] is None
    assert (Decimal(body["range"]["low"]), Decimal(body["range"]["high"])) == (
        Decimal("600"),
        Decimal("690"),
    )


async def test_blocked_by_law_skips_model(client, db_session, seeded_tenants, monkeypatch):
    called = []

    async def fake(doc, instruction, output_model):
        called.append(1)

    monkeypatch.setattr(
        suggest_service, "make_judge", lambda: FailoverJudge(primary=fake, primary_ref="fake")
    )
    body = dict(
        BODY,
        lease=dict(
            BODY["lease"], rent_increases=[{"effective_on": "2026-04-01", "new_amount": "600"}]
        ),
    )
    response = await client.post("/v1/rent-suggestions", json=body, headers=KEY)
    data = response.json()
    assert data["law_blocked"] is True and Decimal(data["suggested_weekly"]) == Decimal("600")
    assert data["model"] is None and called == []


async def test_judge_failure_is_502(client, db_session, seeded_tenants, monkeypatch):
    from app.llm.failover import ProviderDown

    async def down(doc, instruction, output_model):
        raise ProviderDown("x")

    await _seed_market(db_session)
    monkeypatch.setattr(
        suggest_service, "make_judge", lambda: FailoverJudge(primary=down, primary_ref="p")
    )
    response = await client.post("/v1/rent-suggestions", json=BODY, headers=KEY)
    assert response.status_code == 502 and response.json()["detail"] == {
        "code": "judge_unavailable"
    }


async def test_usage_recorded(client, db_session, seeded_tenants, monkeypatch):
    from sqlalchemy import select

    _fake_judge(monkeypatch, weekly="650")
    await client.post("/v1/rent-suggestions", json=BODY, headers=KEY)
    row = (
        await db_session.execute(
            select(UsageCounter).where(UsageCounter.endpoint_class == "rent_suggestions")
        )
    ).scalar_one()
    assert row.count == 1
```

(Arithmetic behind the shape test: market band [698, 886] does not
intersect the cap band [600, 690] (600 × 1.15 = 690 < p25 698), so the
gap is `above_cap`, the range is the cap band, and the fake judge's 650
sits inside it.)

- [ ] **Step 3: Implement**

`app/clause_audit/document.py`: add `system: str | None = None` to `DocumentInput`. In `app/llm/providers/anthropic.py` `build_create_kwargs`: `"system": doc.system or SYSTEM`. In `app/llm/providers/openai_.py` `build_response_kwargs`: `"instructions": doc.system or SYSTEM`. `FailoverJudge` needs no change. Update `app/rent_suggest/judge.py` `suggest()` to build `DocumentInput(kind="text", text=..., system=RENT_SUGGESTION_SYSTEM)` (import from `app.llm.prompts`).

Create `app/schemas/rent_suggestions.py`:

```python
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.rules.base import Finding
from app.schemas.lease import LeaseInput


class SuggestionProperty(BaseModel):
    area_key: str
    dwelling_type: Literal["house", "unit", "townhouse", "other"]
    bedrooms: int | None = None


class RentSuggestionRequest(BaseModel):
    jurisdiction: Literal["NSW", "VIC"]
    as_at: date | None = None
    property: SuggestionProperty
    lease: LeaseInput
    renewal_start: date


class SuggestionRange(BaseModel):
    low: Decimal
    high: Decimal


class SuggestionSource(BaseModel):
    name: str
    url: str
    licence: str


class SuggestionMarket(BaseModel):
    period: str
    median: Decimal
    p25: Decimal | None
    p75: Decimal | None
    sample_size: int
    fallback: str | None
    source: SuggestionSource


class RentSuggestionResponse(BaseModel):
    current_weekly: Decimal
    suggested_weekly: Decimal
    range: SuggestionRange
    market_gap: Literal["within", "above_cap", "below_current", "no_data"]
    market: SuggestionMarket | None
    law_card: list[Finding]
    law_blocked: bool
    reasoning: str
    model: str | None
    engine_version: str
    disclaimer: str
```

Create `app/rent_suggest/service.py`:

```python
"""Compose anchoring, the law card, and the judge into one suggestion."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dates import sydney_today
from app.llm.client import make_judge
from app.rent_suggest.anchor import Anchor, anchor, market_cell
from app.rent_suggest.judge import suggest
from app.rent_suggest.law import law_card
from app.rules import ENGINE_VERSION
from app.rules.base import to_weekly_rent
from app.schemas.rent_suggestions import (
    RentSuggestionRequest,
    RentSuggestionResponse,
    SuggestionMarket,
    SuggestionRange,
    SuggestionSource,
)

DISCLAIMER = "General information, not legal advice."
_SOURCES = {
    "NSW": SuggestionSource(
        name="NSW Fair Trading rental bond lodgements",
        url="https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data",
        licence="NSW Government open data (terms on the source page)",
    ),
    "VIC": SuggestionSource(
        name="Homes Victoria Rental Report (moving annual median rents by suburb)",
        url="https://www.dffh.vic.gov.au/publications/rental-report",
        licence="CC BY 4.0",
    ),
}


def _property_desc(request: RentSuggestionRequest) -> str:
    beds = (
        f", {request.property.bedrooms} bedrooms" if request.property.bedrooms is not None else ""
    )
    return f"{request.property.dwelling_type}{beds}, {request.property.area_key}"


def _market(anchored: Anchor, jurisdiction: str) -> SuggestionMarket | None:
    cell = anchored.market
    if cell is None:
        return None
    return SuggestionMarket(
        period=cell.period,
        median=cell.median,
        p25=cell.p25,
        p75=cell.p75,
        sample_size=cell.sample_size,
        fallback=cell.fallback,
        source=_SOURCES[jurisdiction],
    )


async def build_suggestion(
    session: AsyncSession, request: RentSuggestionRequest
) -> RentSuggestionResponse:
    as_at = request.as_at or sydney_today()
    current = to_weekly_rent(request.lease.rent_amount, request.lease.rent_frequency)
    cell = await market_cell(
        session,
        request.jurisdiction,
        request.property.area_key,
        request.property.dwelling_type,
        request.property.bedrooms,
    )
    anchored = anchor(current, request.jurisdiction, cell)
    midpoint = (anchored.low + anchored.high) / 2
    law = await law_card(
        session, request.jurisdiction, as_at, request.lease, request.renewal_start, midpoint
    )
    if law.blocked:
        anchored = Anchor(
            anchored.current_weekly,
            anchored.current_weekly,
            anchored.current_weekly,
            anchored.gap,
            anchored.market,
        )
    result = await suggest(
        make_judge(), anchored, request.jurisdiction, request.lease, law, _property_desc(request)
    )
    return RentSuggestionResponse(
        current_weekly=anchored.current_weekly,
        suggested_weekly=result.suggested_weekly,
        range=SuggestionRange(low=anchored.low, high=anchored.high),
        market_gap=anchored.gap,
        market=_market(anchored, request.jurisdiction),
        law_card=law.findings,
        law_blocked=law.blocked,
        reasoning=result.reasoning,
        model=result.model,
        engine_version=ENGINE_VERSION,
        disclaimer=DISCLAIMER,
    )
```

Create `app/routers/rent_suggestions.py`:

```python
"""Renewal rent suggestions: deterministic range and law card, one judged figure."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TenantDep
from app.core.db import get_session
from app.core.ratelimit import enforce_rate_limit
from app.core.usage import record_usage
from app.llm.failover import JudgeError
from app.rent_suggest.service import build_suggestion
from app.schemas.rent_suggestions import RentSuggestionRequest, RentSuggestionResponse

router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/rent-suggestions", response_model=RentSuggestionResponse)
async def create_rent_suggestion(
    body: RentSuggestionRequest, tenant: TenantDep, session: SessionDep
) -> RentSuggestionResponse:
    try:
        response = await build_suggestion(session, body)
    except JudgeError as exc:
        raise HTTPException(status_code=502, detail={"code": "judge_unavailable"}) from exc
    await record_usage(session, tenant.tenant_id, "rent_suggestions")
    await session.commit()
    return response
```

Register the router in `app/main.py` like the others. Note the judge is
built per request via `make_judge()` inside the service (so tests can
monkeypatch `service.make_judge`); this constructs a fresh
`FailoverJudge` per call, which means the breaker state is per-request
for this endpoint — acceptable for a synchronous, low-volume endpoint
(document this in the service module docstring); the worker's long-lived
judge is unaffected.

- [ ] **Step 4: Run tests, full suite, ruff, commit**

Run: `uv run pytest tests/test_llm_providers.py tests/test_rent_suggestions_api.py tests/test_rent_suggest_judge.py -v` — Expected: PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/clause_audit/document.py app/llm/providers app/rent_suggest app/schemas/rent_suggestions.py app/routers/rent_suggestions.py app/main.py tests/test_llm_providers.py tests/test_rent_suggestions_api.py tests/test_rent_suggest_judge.py
git commit -m "Serve renewal rent suggestions from a tenant endpoint"
```

---

### Task 5: Golden scenarios and the LLM eval

**Files:**
- Create: `tests/golden/rent_suggestions.py`
- Modify: `tests/test_llm_eval.py` (append `test_rent_suggestions_eval`)
- Test: the eval itself (`-m llm_eval`), plus a structural test in `tests/test_golden.py` that every scenario's deterministic expectations hold without a model

**Interfaces:**
- Consumes: Tasks 1–4; `evidence_numbers` (Task 3).
- Produces: `SCENARIOS: list[Scenario]` (~20) with `Scenario(name, jurisdiction, market_rows, lease, property, renewal_start, expected_gap, expected_range, direction)`; `money_figures(text) -> set[Decimal]`; `RS_GATE = 0.9`.

- [ ] **Step 1: Write the golden module and structural test**

Create `tests/golden/rent_suggestions.py` with ~20 scenarios covering: NSW within (3 sample cells), above_cap (2), below_current (1), no_data (1), thin-sample fallback (2), VIC within/above/below (3), law_blocked frequency (2, NSW/VIC), first-year rule (1), fixed-term disclosure (1), monthly/fortnightly frequency conversion (2), stale market period (1). Each scenario carries the `RentStatistic` rows to seed and the exact deterministic expectations. Include:

```python
import re
from decimal import Decimal

_MONEY = re.compile(r"\$?(\d[\d,]*(?:\.\d+)?)")


def money_figures(text: str) -> set[Decimal]:
    return {Decimal(m.replace(",", "")) for m in _MONEY.findall(text)}


RS_GATE = 0.9
```

and a `test_golden.py` addition asserting, for every scenario, that
`anchor(...)` and `law_card(...)` produce `expected_gap`/`expected_range`
(deterministic, no model, seeded per scenario in a DB fixture).

- [ ] **Step 2: Append the eval to `tests/test_llm_eval.py`**

```python
async def test_rent_suggestions_eval(eval_session):
    from tests.golden.rent_suggestions import RS_GATE, SCENARIOS, money_figures
    from app.rent_suggest.judge import evidence_numbers, HOLD_REASON_BLOCKED, HOLD_REASON_BELOW

    passed = 0
    for scenario in SCENARIOS:
        response, anchored, law = await _run_scenario(eval_session, scenario)
        ok = anchored.low <= response.suggested_weekly <= anchored.high
        if response.model is not None:
            allowed = evidence_numbers(anchored, scenario.lease, law)
            cited = money_figures(response.reasoning) - {Decimal(y) for y in range(2000, 2100)}
            ok = ok and cited <= allowed
            if scenario.expected_gap == "above_cap":
                ok = ok and response.suggested_weekly >= (anchored.low + anchored.high) / 2
        else:
            ok = ok and response.reasoning in (HOLD_REASON_BLOCKED, HOLD_REASON_BELOW)
        print(
            f"{scenario.name:40} {'PASS' if ok else 'FAIL'} {response.suggested_weekly} {response.reasoning[:80]}"
        )
        passed += ok
    rate = passed / len(SCENARIOS)
    print(f"rent suggestions: {passed}/{len(SCENARIOS)} = {rate:.2f}")
    assert rate >= RS_GATE
```

with this helper in the same file (service pieces, not HTTP; year-like
integers 2000–2099 are excluded from citation matching because periods
appear in reasoning):

```python
async def _run_scenario(session, scenario):
    from app.rent_suggest.anchor import anchor, market_cell
    from app.rent_suggest.law import law_card
    from app.rent_suggest.service import build_suggestion
    from app.rules.base import to_weekly_rent
    from app.schemas.rent_suggestions import RentSuggestionRequest

    session.add_all(scenario.market_rows)
    await session.flush()
    request = RentSuggestionRequest(
        jurisdiction=scenario.jurisdiction,
        as_at=scenario.as_at,
        property=scenario.property,
        lease=scenario.lease,
        renewal_start=scenario.renewal_start,
    )
    current = to_weekly_rent(scenario.lease.rent_amount, scenario.lease.rent_frequency)
    cell = await market_cell(
        session,
        scenario.jurisdiction,
        scenario.property.area_key,
        scenario.property.dwelling_type,
        scenario.property.bedrooms,
    )
    anchored = anchor(current, scenario.jurisdiction, cell)
    law = await law_card(
        session,
        scenario.jurisdiction,
        scenario.as_at,
        scenario.lease,
        scenario.renewal_start,
        (anchored.low + anchored.high) / 2,
    )
    response = await build_suggestion(session, request)
    await session.rollback()
    return response, anchored, law
```

(`build_suggestion` recomputes anchor and law internally; the helper
recomputes them once more only to hand the eval the same objects for
property checks — identical inputs give identical deterministic outputs.
After the law-blocked collapse inside the service, the eval's in-range
check uses the anchored range pre-collapse only for non-blocked
scenarios; blocked scenarios assert the hold template instead.)

- [ ] **Step 3: Run structural tests, full suite, ruff, commit**

Run: `uv run pytest tests/test_golden.py -q` — Expected: PASS.
Run: `uv run pytest -m "not llm_eval" -q` — Expected: PASS.

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add tests/golden/rent_suggestions.py tests/test_golden.py tests/test_llm_eval.py
git commit -m "Add rent-suggestion golden scenarios and the LLM eval"
```

---

### Task 6: SaaS proxy, renew-page button, e2e

**Files (SaaS repo `/Users/keithho/LLMProjects/rental_management_app`):**
- Modify: `backend/app/services/compliance.py` (add `create_rent_suggestion(payload) -> dict`), `backend/app/routers/leases.py` or a new `backend/app/routers/rent_suggestions.py` (proxy endpoint), `backend/app/main.py` (register)
- Create: `frontend/src/lib/rentSuggestion.ts`, `frontend/src/app/app/leases/[leaseId]/renew/RentSuggestionCard.tsx`
- Modify: `frontend/src/app/app/leases/[leaseId]/renew/page.tsx`
- Test: `backend/tests/test_rent_suggestion_endpoint.py`, `frontend/e2e/rent-suggestion.spec.ts`

**Interfaces:**
- Consumes: service endpoint (Task 4 shapes), `chain_to_audit_payload`, `load_chain`, `property_jurisdiction`, `get_owned_lease`, `require_ai_consent(AiFeature.rent_ai)`, `require_roles(landlord, property_manager)`.
- Produces: `POST /api/v1/leases/{lease_id}/rent-suggestion` body `{"renewal_start": "YYYY-MM-DD"}` -> the service response verbatim (503 when compliance disabled, 403 consent, 404 lease, 502 passthrough); frontend `getRentSuggestion(leaseId, renewalStart)`.

- [ ] **Step 1: Backend tests (failing first)** — mirror `tests/test_clause_audit_endpoints.py`'s setup helpers (`_setup`, `enable_clause_audit` -> add `enable_rent_ai` in `tests/test_ai_consent_endpoints.py`); tests: 401 unauth; 403 without rent_ai consent with the exact `{"code":"ai_consent_required","feature":"rent_ai"}` body; 200 passthrough with `compliance.create_rent_suggestion` monkeypatched to return a canned response and the request payload captured — assert it carries `property.area_key == property.postcode` (NSW) / suburb (VIC — read how the SaaS stores VIC suburb: `Property.city`), `dwelling_type` mapped from `PropertyType` (map house/unit/townhouse/other; anything else -> other), `bedrooms`, `lease` from `chain_to_audit_payload(...)["lease"]`, `renewal_start` from body; 502 passthrough when the service call raises `httpx.HTTPStatusError` with 502.
- [ ] **Step 2: Implement** — `create_rent_suggestion(payload)` in `services/compliance.py` copying `create_audit`'s shape against `/v1/rent-suggestions`; router endpoint with deps `manager`, `require_ai_consent(AiFeature.rent_ai)`, `get_session`; build payload; call; return `JSONResponse(content=result)`; map `HTTPStatusError` 502 -> 502 `{"detail": {"code": "judge_unavailable"}}`.
- [ ] **Step 3: Frontend** — `lib/rentSuggestion.ts` via `apiFetch` (POST); `RentSuggestionCard.tsx` renders figure, range, reasoning, law card rows (verdict colour, citation label like the clause-audit UI), market line ("Market {period}: median {median}, n={sample_size}{fallback}"), source name, and for VIC the line "Data: Homes Victoria Rental Report, CC BY 4.0"; "Use suggestion" button calls `onUse(weekly)`. Renew page: "Suggest rent" button next to the rent input (disabled until start date set); on click POST with `renewal_start=startDate`; consent 403 -> render the existing consent prompt-card pattern linking `/app/settings/ai`; 502 -> inline "Suggestion unavailable, try again."; `onUse` converts weekly -> form frequency (`weekly*52/26` fortnightly, `weekly*52/12` monthly, rounded to whole dollars) and sets the rent field.
- [ ] **Step 4: e2e** — `frontend/e2e/rent-suggestion.spec.ts`: sign up (inline pattern from `clause-audit.spec.ts`), create property (NSW) + lease, open `/app/leases/{id}/renew`; test A: unconsented -> click "Suggest rent" -> consent card visible; test B: enable rent_ai on `/app/settings/ai`, mock the backend proxy via `page.route("**/rent-suggestion", ...)` returning a canned response with `suggested_weekly: "720"` -> card shows 720 and "Use suggestion" fills the rent field with the frequency-converted value. LIVE flag `RENT_SUGGESTION_E2E` for one un-mocked run.
- [ ] **Step 5: Run** backend `uv run pytest -q`, frontend lint/tsc, `npx playwright test e2e/rent-suggestion.spec.ts`; commit "Suggest renewal rent on the renew page" with trailer.

---

### Task 7: Eval gate, deploy, docs (controller-run)

- [ ] **Step 1: Deploy the service** at the Task 5 head (`deploy.sh sha-<short>`), verify `/health`.
- [ ] **Step 2: Run the eval on the primary**: `CLAUSE_AUDIT_FAILOVER_MODEL= uv run pytest -m llm_eval -k rent_suggestions -v -s` (~20 calls, cents). Gate ≥ 0.9. Then on the backup: `CLAUSE_AUDIT_FAILOVER_MODEL= CLAUSE_AUDIT_MODEL=openai:gpt-5.6-terra uv run pytest -m llm_eval -k rent_suggestions -v -s`. Any failure: per-scenario diagnosis (which property failed, the reasoning text); golden defects fixed as data, prompt tweaks re-run both models.
- [ ] **Step 3: Record** in `docs/model-evals.md` (new section "Rent suggestions", per-model pass rate, date) and `deploy/README.md` (endpoint, usage class `rent_suggestions`, breaker-per-request note, VIC attribution requirement).
- [ ] **Step 4: Production smoke**: one real `POST /v1/rent-suggestions` for a NSW postcode with data (e.g. 2000, unit, 2 bedrooms, current 600 weekly, renewal next month) — verify range/gap/law card/reasoning shape and that reasoning cites only supplied figures; one VIC suburb call.
- [ ] **Step 5: SaaS**: local dev only (no production) — run the SaaS e2e; ledger + memory; final whole-branch review across both repos.
