# Calendar Interval Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Statutory "12 months" / "2 years" / "3 years" are reckoned as calendar periods (corresponding-date rule) instead of 365-day multiples, closing the leap-day false-green in both jurisdictions' frequency rules.

**Architecture:** One stdlib helper `add_months` in `app/rules/base.py`; five call sites swap day-count comparisons for anniversary comparisons with unchanged inequality directions; leap-boundary red/green pairs pin every site; `ENGINE_VERSION` 1.5.0.

**Tech Stack:** stdlib `calendar.monthrange` only - no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-06-calendar-interval-design.md`

## Global Constraints

- Helper exactly: `add_months(d: date, months: int) -> date` in `app/rules/base.py`, day clamped to the target month's last day.
- Inequality directions unchanged at every site; summaries keep their wording; `gaps_days` / `term_days` evidence stays (informational).
- An increase on the anniversary day itself is green.
- The `YEAR` constants in `app/rules/nsw.py` and `app/rules/vic.py` are removed (and `timedelta` imports if then unused).
- `ENGINE_VERSION = "1.5.0"`.
- Existing 365-day boundary tests stay untouched and passing.
- No other rule logic, thresholds, messages, or clause-audit changes. No re-audit of stored results.
- uv only, no emojis, TDD, ruff sequence, commit + push + CI green per task. Trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: The add_months helper

**Files:**
- Modify: `app/rules/base.py` (add `calendar` import and the helper)
- Test: `tests/test_add_months.py` (new)

**Interfaces:**
- Produces: `add_months(d: date, months: int) -> date`, imported by Task 2's five call sites.

- [ ] **Step 1: Write the failing tests**

`tests/test_add_months.py`:

```python
from datetime import date

import pytest

from app.rules.base import add_months


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2025, 1, 15), 12, date(2026, 1, 15)),
        (date(2023, 3, 1), 12, date(2024, 3, 1)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2025, 1, 31), 1, date(2025, 2, 28)),
        (date(2025, 3, 31), 1, date(2025, 4, 30)),
        (date(2025, 11, 15), 2, date(2026, 1, 15)),
        (date(2023, 3, 1), 24, date(2025, 3, 1)),
        (date(2023, 3, 1), 36, date(2026, 3, 1)),
        (date(2024, 2, 29), 48, date(2028, 2, 29)),
    ],
)
def test_add_months_corresponding_date_rule(start, months, expected):
    assert add_months(start, months) == expected
```

- [ ] **Step 2: Watch them fail**

Run: `uv run pytest tests/test_add_months.py -v`
Expected: collection error - `ImportError: cannot import name 'add_months'`.

- [ ] **Step 3: Implement**

In `app/rules/base.py`, add `import calendar` to the imports and, after
`to_weekly_rent`, the helper:

```python
def add_months(d: date, months: int) -> date:
    """The corresponding date `months` calendar months after `d`.

    Statutory periods of months are reckoned by the corresponding-date
    rule (Dodds v Walker): the period ends on the corresponding day of
    the target month, or its last day when no corresponding day exists
    (Jan 31 + 1 month is the end of February; Feb 29 + 12 months is
    Feb 28). The Interpretation Act 1987 (NSW) Schedule 4 defines
    calendar month as the period to the corresponding day of the next
    named month, or that month's end when no corresponding day exists;
    the Interpretation of Legislation Act 1984 (Vic) s 44(6)(b)
    construes a month as a calendar month (both verified against the
    current in-force text, 2026-08-06).
    """
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/test_add_months.py -v`
Expected: 10 passed.

- [ ] **Step 5: Full suite, ruff, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/rules/base.py tests/test_add_months.py
git commit -m "Add the add_months corresponding-date helper"
git push origin main
```

---

### Task 2: Five call sites and leap-boundary evals

**Files:**
- Modify: `app/rules/nsw.py` (`_frequency_check`, `_first_year_check`, `_disclosure_check`, `_break_fee_check`, remove `YEAR`)
- Modify: `app/rules/vic.py` (`_frequency_check`, remove `YEAR`)
- Modify: `app/rules/__init__.py` (`ENGINE_VERSION = "1.5.0"`)
- Test: `tests/test_rules_nsw.py`, `tests/test_rules_vic.py` (append leap-boundary tests)

**Interfaces:**
- Consumes: Task 1's `add_months`.
- Produces: verdicts reckoned by calendar anniversaries; engine 1.5.0.

- [ ] **Step 1: Append the failing leap-boundary tests**

Both files use their existing `corpus_session` fixture and helpers -
read each file's local idioms (`lease(**overrides)` in the VIC file;
the NSW file's equivalent constructors) and adapt constructor calls,
keeping the dates and assertions below exactly.

To `tests/test_rules_vic.py`:

```python
async def test_leap_spanning_365_day_gap_is_red(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_increases=[
                RentIncrease(effective_on=date(2023, 3, 1), new_amount=Decimal(2000)),
                RentIncrease(effective_on=date(2024, 2, 29), new_amount=Decimal(2100)),
            ]
        ),
    )
    f = findings["vic.rent_increase_frequency"]
    assert f.verdict == "red"
    assert "365" in f.summary


async def test_feb29_anniversary_clamps_to_feb28(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_increases=[
                RentIncrease(effective_on=date(2024, 2, 29), new_amount=Decimal(2000)),
                RentIncrease(effective_on=date(2025, 2, 28), new_amount=Decimal(2100)),
            ]
        ),
    )
    assert findings["vic.rent_increase_frequency"].verdict == "green"


async def test_day_before_clamped_anniversary_is_red(corpus_session):
    findings = await run(
        corpus_session,
        lease(
            rent_increases=[
                RentIncrease(effective_on=date(2024, 2, 29), new_amount=Decimal(2000)),
                RentIncrease(effective_on=date(2025, 2, 27), new_amount=Decimal(2100)),
            ]
        ),
    )
    assert findings["vic.rent_increase_frequency"].verdict == "red"
```

To `tests/test_rules_nsw.py`, using the file's `_verdict` helper and
its `lease()` defaults (weekly 600, start 2026-01-01); `RentIncrease`
is already imported there for the existing frequency tests:

```python
async def test_leap_spanning_365_day_gap_is_red(corpus_session):
    finding = await _verdict(
        corpus_session,
        "nsw.rent_increase_frequency",
        rent_increases=[
            RentIncrease(effective_on=date(2023, 3, 1), new_amount=Decimal(650)),
            RentIncrease(effective_on=date(2024, 2, 29), new_amount=Decimal(700)),
        ],
    )
    assert finding.verdict == "red"
    assert "365" in finding.summary


async def test_first_year_leap_boundary(corpus_session):
    red = await _verdict(
        corpus_session,
        "nsw.rent_increase_first_year",
        start_date=date(2023, 3, 1),
        rent_increases=[RentIncrease(effective_on=date(2024, 2, 29), new_amount=Decimal(650))],
    )
    assert red.verdict == "red"
    green = await _verdict(
        corpus_session,
        "nsw.rent_increase_first_year",
        start_date=date(2023, 3, 1),
        rent_increases=[RentIncrease(effective_on=date(2024, 3, 1), new_amount=Decimal(650))],
    )
    assert green.verdict == "green"


async def test_disclosure_leap_term_is_under_two_years(corpus_session):
    red = await _verdict(
        corpus_session,
        "nsw.fixed_term_increase_disclosure",
        as_at=date(2024, 6, 1),
        start_date=date(2023, 3, 1),
        end_date=date(2025, 2, 28),
        rent_increases=[RentIncrease(effective_on=date(2024, 1, 15), new_amount=Decimal(650))],
        fixed_term_increase_in_agreement=False,
    )
    assert red.verdict == "red"
    green = await _verdict(
        corpus_session,
        "nsw.fixed_term_increase_disclosure",
        as_at=date(2024, 6, 1),
        start_date=date(2023, 3, 1),
        end_date=date(2025, 3, 1),
        rent_increases=[RentIncrease(effective_on=date(2024, 1, 15), new_amount=Decimal(650))],
        fixed_term_increase_in_agreement=False,
    )
    assert green.verdict == "green"


async def test_break_fee_scale_applies_on_the_exact_anniversary(corpus_session):
    red = await _verdict(
        corpus_session,
        "nsw.break_fee_cap",
        start_date=date(2023, 3, 1),
        end_date=date(2026, 3, 1),
        break_fee_amount=Decimal(5000),
    )
    assert red.verdict == "red"
    green = await _verdict(
        corpus_session,
        "nsw.break_fee_cap",
        start_date=date(2023, 3, 1),
        end_date=date(2026, 3, 2),
        break_fee_amount=Decimal(5000),
    )
    assert green.verdict == "green"
```

If the existing frequency tests construct `RentIncrease` differently
(field names are `effective_on`/`new_amount` - verify against the
file), mirror them; the dates and verdicts above are the requirements.
The first-year rule is active at the default `AS_AT` (2026-07-24, after
its 2024-10-31 commencement); the disclosure tests pin `as_at`
2024-06-01 because s 42 was repealed 2024-12-13.

- [ ] **Step 2: Watch the new tests fail for the right reason**

Run: `uv run pytest tests/test_rules_vic.py tests/test_rules_nsw.py -v -k "leap or feb29 or clamped or anniversary or disclosure_leap"`
Expected: every red-expecting new test fails with green (or
scale-not-applied) verdicts - the 365-day arithmetic passing what
calendar reckoning must catch. The green-expecting halves pass already
(they are hard greens under both semantics; that is fine at RED stage).

- [ ] **Step 3: Rewrite the five sites**

`app/rules/vic.py` - imports gain `add_months` (from app.rules.base),
drop `YEAR` and `timedelta`; `_frequency_check`'s decision becomes:

```python
pairs = list(pairwise(sorted(i.effective_on for i in lease.rent_increases)))
gaps = [(later - earlier).days for earlier, later in pairs]
evidence = {
    "fields": {"rent_increases": [str(i.effective_on) for i in lease.rent_increases]},
    "computed": {"gaps_days": gaps},
}
short = [(later - earlier).days for earlier, later in pairs if later < add_months(earlier, 12)]
if short:
    return (
        "red",
        f"Rent increases less than 12 months apart (shortest gap {min(short)} days).",
        evidence,
    )
return ("green", "All rent increases are at least 12 months apart.", evidence)
```

(keep the docstring, appending one line: "Twelve months is reckoned in
calendar months under the corresponding-date rule.")

`app/rules/nsw.py`:

- `_frequency_check`: same reshape as VIC (pairs over `_sorted_increases`,
  red set = pairs with `later.effective_on < add_months(earlier.effective_on, 12)`,
  message/evidence unchanged).
- `_first_year_check`: the condition
  `if first is not None and days < YEAR.days:` becomes
  `if first is not None and first.effective_on < add_months(lease.start_date, 12):`
  (evidence keeps `days_after_start`).
- `_disclosure_check`: add `under_2_years = lease.end_date < add_months(lease.start_date, 24)`
  before the evidence dict; the evidence's computed value and the red
  condition both use `under_2_years` in place of
  `term_days < 2 * YEAR.days` (evidence fields keep `term_days`).
- `_break_fee_check`: `scale_applies = lease.end_date <= add_months(lease.start_date, 36)`
  replaces the `term_days` comparison (evidence keeps `term_days`).
- Remove `YEAR` (line 14) and prune `timedelta` from the import if
  nothing else uses it. Append the same one-line calendar-reckoning
  note to each touched docstring.

`app/rules/__init__.py`: `ENGINE_VERSION = "1.5.0"`.

- [ ] **Step 4: All tests green**

```bash
uv run pytest tests/test_rules_vic.py tests/test_rules_nsw.py tests/test_add_months.py -v
uv run pytest
```

Expected: the new leap tests pass, every pre-existing 365-day boundary
test still passes untouched, full suite green. If any existing test
fails, that is a finding about the reshape - fix the code, not the
test.

- [ ] **Step 5: Ruff, commit, push, CI**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/rules/nsw.py app/rules/vic.py app/rules/__init__.py tests/test_rules_nsw.py tests/test_rules_vic.py
git commit -m "Reckon statutory months and years as calendar periods"
git push origin main
```

CI runs these evals against the corpus dump - the leap boundaries are
CI-enforced from this commit on.

---

### Task 3: Rollout (interactive)

No repo changes except the ledger and memory. Run by the controller.

- [ ] **Step 1: Deploy**

```bash
LEASE_DEPLOY_SERVER=deploy@168.144.169.66 LEASE_DEPLOY_DOMAIN=api.leasekoala.com ./deploy/deploy.sh
```

Migrationless. The script's final probe may 502 before uvicorn finishes
booting - verify `/health` directly.

- [ ] **Step 2: Production acceptance**

One audit per jurisdiction with the leap-spanning pair (increases
2023-03-01 -> 2024-02-29, rent 2000 monthly, start 2023-01-01):
`nsw.rent_increase_frequency` and `vic.rent_increase_frequency` both
red with "shortest gap 365 days" in the summary, engine_version 1.5.0
in the response.

- [ ] **Step 3: Ledger and memory**

Append completion to `.superpowers/sdd/progress.md`; strike the
calendar-month item from the milestone memory's spawn list.

---

## Self-review

- Spec coverage: helper + docstring citations (Task 1); five sites with
  unchanged inequality directions, YEAR removal, anniversary-green
  semantics, docstring notes (Task 2); leap red/green pairs per site
  incl. the 2023-03-01 -> 2024-02-29 nail and the Feb 29 clamp
  (Task 2); existing 365 tests untouched (Task 2 Step 4); CI
  enforcement via the corpus dump (Task 2 Step 5); ENGINE_VERSION 1.5.0
  (Task 2); migrationless deploy + leap-pair production acceptance +
  no re-audit (Task 3).
- Placeholders: none - both files' new tests are complete code against
  their verified helpers (`_verdict`/`lease` in the NSW file,
  `run`/`lease` in the VIC file), with a verify-the-field-names note as
  the only adaptation point.
- Type consistency: `add_months(d, months)` signature identical across
  Tasks 1 and 2; VIC reshape keeps `run`/`lease` helper usage from the
  existing file; evidence keys unchanged everywhere.
