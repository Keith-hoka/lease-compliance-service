"""The three check families: judge, verify, assemble findings."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.clause_audit import rules as clause_rules
from app.clause_audit.document import DocumentInput
from app.clause_audit.verify import parse_amount, parse_date, parse_frequency, quote_matches
from app.llm.client import JudgeFn
from app.llm.prompts import clause_instruction, fields_instruction
from app.llm.schemas import FieldsOutput, family_output_model
from app.schemas.clause_audit import ClauseFinding, ClauseLeaseInput, Discrepancy

DATE_FIELDS = {"start_date", "end_date"}


async def _run_clause_family(
    judge: JudgeFn,
    session: AsyncSession,
    doc: DocumentInput,
    as_at: date,
    rules: list[clause_rules.ClauseRule],
    family_name: str,
    output_name: str,
    require_quote_on_red: bool,
) -> list[ClauseFinding]:
    findings: list[ClauseFinding] = []
    active: list[tuple[clause_rules.ClauseRule, object]] = []
    for rule in rules:
        if not clause_rules.rule_active(rule, as_at):
            findings.append(
                ClauseFinding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Rule not active at the audit date.",
                    skip_reason=f"rule not active at {as_at}",
                )
            )
            continue
        citation = await clause_rules.resolve_rule(session, rule, as_at)
        if citation is None:
            findings.append(
                ClauseFinding(
                    rule_id=rule.rule_id,
                    verdict="skipped",
                    summary="Statutory basis not in force at the audit date.",
                    skip_reason=f"section {rule.ref.section_no} not in force at {as_at}",
                )
            )
            continue
        active.append((rule, citation))
    if not active:
        return findings

    sections = await clause_rules.statutory_texts(session, [r for r, _ in active], as_at)
    instruction = clause_instruction(
        family_name, as_at, sections, [(r.rule_id, r.question) for r, _ in active]
    )
    output_model = family_output_model(output_name, [r.rule_id for r, _ in active])
    result = await judge(doc, instruction, output_model)
    by_id = {str(item.rule_id): item for item in result.items}

    for rule, citation in active:
        item = by_id.get(rule.rule_id)
        if item is None:
            findings.append(
                ClauseFinding(
                    rule_id=rule.rule_id,
                    verdict="yellow",
                    summary="The model did not report on this rule.",
                    citations=[citation],
                )
            )
            continue
        verdict, summary, quote = item.verdict, item.reasoning, item.clause_quote
        if verdict == "red" and require_quote_on_red:
            if quote is None:
                verdict, summary = "yellow", "Downgraded: red verdict carried no quote."
            elif not quote_matches(quote, doc.text):
                verdict = "yellow"
                summary = "Downgraded: quoted text was not found in the document."
        findings.append(
            ClauseFinding(
                rule_id=rule.rule_id,
                verdict=verdict,
                summary=summary,
                evidence={"reasoning": item.reasoning},
                citations=[citation],
                clause_quote=quote,
            )
        )
    return findings


async def run_prohibited(
    judge: JudgeFn, session: AsyncSession, doc: DocumentInput, as_at: date
) -> list[ClauseFinding]:
    return await _run_clause_family(
        judge,
        session,
        doc,
        as_at,
        clause_rules.PROHIBITED_RULES,
        "prohibited terms",
        "ProhibitedOutput",
        require_quote_on_red=True,
    )


async def run_mandatory(
    judge: JudgeFn, session: AsyncSession, doc: DocumentInput, as_at: date
) -> list[ClauseFinding]:
    return await _run_clause_family(
        judge,
        session,
        doc,
        as_at,
        clause_rules.MANDATORY_RULES,
        "mandatory terms",
        "MandatoryOutput",
        require_quote_on_red=False,
    )


def _mismatch(field: str, document_value: str, submitted) -> bool:
    if field in DATE_FIELDS:
        parsed_date = parse_date(document_value)
        return parsed_date is not None and parsed_date != submitted
    if field == "rent_frequency":
        parsed_freq = parse_frequency(document_value)
        return parsed_freq is not None and parsed_freq != submitted
    parsed_amount = parse_amount(document_value)
    return parsed_amount is not None and parsed_amount != submitted


async def run_fields(
    judge: JudgeFn, doc: DocumentInput, lease: ClauseLeaseInput
) -> list[Discrepancy]:
    result = await judge(doc, fields_instruction(), FieldsOutput)
    discrepancies: list[Discrepancy] = []
    for item in result.fields:
        submitted = getattr(lease, item.field)
        if submitted is None or item.document_value is None:
            continue
        if _mismatch(item.field, item.document_value, submitted):
            discrepancies.append(
                Discrepancy(
                    field=item.field,
                    document_value=item.document_value,
                    submitted_value=str(submitted),
                )
            )
    return discrepancies
