from datetime import date

from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act, Audit
from app.monitor.runner import diff_findings, new_version_dates, run_monitor
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput


def _f(rule_id, verdict):
    return {"rule_id": rule_id, "verdict": verdict}


def test_diff_verdict_flip():
    delta = diff_findings(
        [_f("nsw.bond_max_4_weeks", "green")], [_f("nsw.bond_max_4_weeks", "red")]
    )
    assert delta == {"nsw.bond_max_4_weeks": {"from": "green", "to": "red"}}


def test_diff_skipped_transition_counts():
    delta = diff_findings(
        [_f("nsw.fixed_term_increase_disclosure", "red")],
        [_f("nsw.fixed_term_increase_disclosure", "skipped")],
    )
    assert delta == {"nsw.fixed_term_increase_disclosure": {"from": "red", "to": "skipped"}}


def test_diff_rule_added_and_removed():
    delta = diff_findings([_f("nsw.old_rule", "green")], [_f("nsw.new_rule", "green")])
    assert delta == {
        "nsw.old_rule": {"from": "green", "to": None},
        "nsw.new_rule": {"from": None, "to": "green"},
    }


def test_diff_no_change_is_empty():
    same = [_f("nsw.bond_max_4_weeks", "red"), _f("nsw.no_other_security", "skipped")]
    assert diff_findings(same, list(same)) == {}


def test_new_version_dates_subtracts_and_sorts():
    timeline = [date(2026, 6, 10), date(2010, 6, 17), date(2026, 9, 1)]
    ingested = {date(2010, 6, 17), date(2026, 6, 10)}
    assert new_version_dates(timeline, ingested) == [date(2026, 9, 1)]


LEASE = {
    "rent_amount": "600",
    "rent_frequency": "weekly",
    "start_date": "2020-06-01",
    "bond_amount": "3000",
}


async def _seed_act(db_session):
    act = Act(jurisdiction="NSW", slug="act-2010-042", title="T", source_url="x")
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2020, 1, 1),
        [ParsedSection("159", "Payment of bonds", "4 weeks", None, None)],
    )


async def _stored_audit(db_session, client_id="rentalapp", client_ref="lease-1"):
    """A real audit whose bond verdict is tampered to green, so a re-run flips it."""
    findings = await run_audit(db_session, "NSW", date(2021, 1, 1), LeaseInput(**LEASE))
    dumped = [f.model_dump(mode="json") for f in findings]
    bond = next(f for f in dumped if f["rule_id"] == "nsw.bond_max_4_weeks")
    bond["verdict"] = "green"
    audit = Audit(
        jurisdiction="NSW",
        as_at=date(2021, 1, 1),
        input=LEASE,
        findings=dumped,
        engine_version="1.0.0",
        client_id=client_id,
        client_ref=client_ref,
    )
    db_session.add(audit)
    await db_session.commit()
    return audit


async def test_monitor_records_verdict_change(db_session):
    await _seed_act(db_session)
    stored = await _stored_audit(db_session)
    result = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    assert result.checked == 1
    [change] = result.changes
    assert change.changes == {"nsw.bond_max_4_weeks": {"from": "green", "to": "red"}}
    assert change.old_audit_id == stored.id
    new_audit = await db_session.get(Audit, change.new_audit_id)
    assert new_audit.client_id == "rentalapp"
    assert new_audit.client_ref == "lease-1"
    assert new_audit.as_at == date(2021, 6, 1)


async def test_monitor_is_idempotent(db_session):
    await _seed_act(db_session)
    await _stored_audit(db_session)
    first = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    second = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    assert len(first.changes) == 1
    assert second.checked == 1
    assert second.changes == []


async def test_audit_without_client_ref_is_not_monitored(db_session):
    await _seed_act(db_session)
    await _stored_audit(db_session, client_ref=None)
    result = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    assert result.checked == 0


async def test_same_ref_different_tenants_grouped_separately(db_session):
    await _seed_act(db_session)
    await _stored_audit(db_session, client_id="rentalapp", client_ref="lease-1")
    await _stored_audit(db_session, client_id="acme", client_ref="lease-1")
    result = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    assert result.checked == 2
    assert len(result.changes) == 2
