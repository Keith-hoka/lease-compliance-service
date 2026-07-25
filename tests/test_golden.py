from datetime import date

import pytest

from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput
from tests.golden.leases import GOLDEN
from tests.test_rules_nsw import corpus_session  # noqa: F401  (reuse the skip-guard fixture)

AS_AT = date(2026, 7, 24)


@pytest.mark.parametrize("case_id,lease_kwargs,expected", GOLDEN, ids=[g[0] for g in GOLDEN])
async def test_golden_case(corpus_session, case_id, lease_kwargs, expected):  # noqa: F811
    findings = await run_audit(corpus_session, "NSW", AS_AT, LeaseInput(**lease_kwargs))
    actual = {f.rule_id: f.verdict for f in findings if f.verdict != "skipped"}
    assert actual == expected


async def test_same_lease_differs_across_reform(corpus_session):  # noqa: F811
    """The frequency rule is inactive before its commencement and red after."""
    from app.rules.nsw import FREQ_COMMENCED

    lease_kwargs = {
        "rent_amount": "600",
        "rent_frequency": "weekly",
        "start_date": "2000-01-01",
        "rent_increases": [
            {"effective_on": "2001-01-01", "new_amount": "620"},
            {"effective_on": "2001-06-01", "new_amount": "640"},
        ],
    }
    before = await run_audit(
        corpus_session,
        "NSW",
        FREQ_COMMENCED.replace(year=FREQ_COMMENCED.year - 1),
        LeaseInput(**lease_kwargs),
    )
    after = await run_audit(corpus_session, "NSW", AS_AT, LeaseInput(**lease_kwargs))
    freq_before = next(f for f in before if f.rule_id == "nsw.rent_increase_frequency")
    freq_after = next(f for f in after if f.rule_id == "nsw.rent_increase_frequency")
    assert freq_before.verdict == "skipped"
    assert freq_after.verdict == "red"
