from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Act
from app.rules import ALL_RULES
from app.rules.base import Citation, Finding
from app.schemas.lease import LeaseInput
from app.services.legislation import section_at


async def run_audit(
    session: AsyncSession, jurisdiction: str, as_at: date, lease: LeaseInput
) -> list[Finding]:
    """Run every registered rule for the jurisdiction against the lease at as_at."""
    findings: list[Finding] = []
    for rule in ALL_RULES:
        if rule.jurisdiction != jurisdiction:
            continue
        if (rule.applies_from and as_at < rule.applies_from) or (
            rule.applies_to and as_at >= rule.applies_to
        ):
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Rule not active at the audit date.",
                    skip_reason=f"rule not active at {as_at}",
                )
            )
            continue

        citations: list[Citation] = []
        missing_section = None
        for ref in rule.citations:
            section = await section_at(session, ref.act_slug, ref.section_no, as_at)
            if section is None:
                missing_section = ref
                break
            act = await session.get(Act, section.act_id)
            citations.append(
                Citation(
                    act=act.title, section_no=ref.section_no, as_at=as_at, section_id=section.id
                )
            )
        if missing_section is not None:
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Statutory basis not in force at the audit date.",
                    skip_reason=f"section {missing_section.section_no} not in force at {as_at}",
                )
            )
            continue

        absent = [f for f in rule.required_inputs if getattr(lease, f) is None]
        if absent:
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Insufficient input to run this check.",
                    citations=citations,
                    skip_reason="missing input: " + ", ".join(absent),
                )
            )
            continue

        verdict, summary, evidence = rule.check(lease)
        findings.append(
            Finding(
                rule_id=rule.rule_id,
                verdict=verdict,
                summary=summary,
                evidence=evidence,
                citations=citations,
                skip_reason=summary if verdict == "skipped" else None,
            )
        )
    return findings
