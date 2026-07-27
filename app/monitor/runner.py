from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audit, AuditChange
from app.rules import ENGINE_VERSION
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput


def diff_findings(old: list[dict], new: list[dict]) -> dict[str, dict]:
    """Rules whose verdict differs between two findings lists.

    A rule present on one side only reports None for the absent side.
    """
    old_verdicts = {f["rule_id"]: f["verdict"] for f in old}
    new_verdicts = {f["rule_id"]: f["verdict"] for f in new}
    return {
        rule_id: {"from": old_verdicts.get(rule_id), "to": new_verdicts.get(rule_id)}
        for rule_id in old_verdicts.keys() | new_verdicts.keys()
        if old_verdicts.get(rule_id) != new_verdicts.get(rule_id)
    }


def new_version_dates(timeline: list[date], ingested: set[date]) -> list[date]:
    """Timeline dates not yet ingested, ascending."""
    return sorted(set(timeline) - ingested)


@dataclass
class MonitorResult:
    checked: int
    changes: list[AuditChange]


async def latest_monitored_audits(session: AsyncSession, jurisdiction: str) -> list[Audit]:
    """The newest audit per (client_id, client_ref) where client_ref is set."""
    rows = (
        (
            await session.execute(
                select(Audit)
                .where(Audit.jurisdiction == jurisdiction, Audit.client_ref.is_not(None))
                .order_by(Audit.created_at.desc(), Audit.id.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[tuple[str, str], Audit] = {}
    for audit in rows:
        latest.setdefault((audit.client_id, audit.client_ref), audit)
    return list(latest.values())


async def run_monitor(session: AsyncSession, jurisdiction: str, as_at: date) -> MonitorResult:
    """Re-run monitored audits at as_at and record verdict changes."""
    monitored = await latest_monitored_audits(session, jurisdiction)
    changes: list[AuditChange] = []
    for audit in monitored:
        findings = await run_audit(session, jurisdiction, as_at, LeaseInput(**audit.input))
        new_findings = [f.model_dump(mode="json") for f in findings]
        delta = diff_findings(audit.findings, new_findings)
        if not delta:
            continue
        new_audit = Audit(
            jurisdiction=jurisdiction,
            as_at=as_at,
            input=audit.input,
            findings=new_findings,
            engine_version=ENGINE_VERSION,
            client_id=audit.client_id,
            client_ref=audit.client_ref,
        )
        session.add(new_audit)
        await session.flush()
        change = AuditChange(
            client_id=audit.client_id,
            client_ref=audit.client_ref,
            old_audit_id=audit.id,
            new_audit_id=new_audit.id,
            changes=delta,
            created_at=func.clock_timestamp(),
        )
        session.add(change)
        changes.append(change)
    await session.commit()
    return MonitorResult(checked=len(monitored), changes=changes)
