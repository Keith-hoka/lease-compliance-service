"""Prompt text. SYSTEM is byte-identical across families so the cache prefix holds."""

from datetime import date

from app.llm.schemas import FIELD_NAMES

SYSTEM = (
    "You are a compliance checker for New South Wales residential tenancy documents. "
    "You judge lease clauses strictly against the statutory text supplied in the "
    "instruction; never rely on remembered law and never cite anything not supplied. "
    "If the document or a clause is ambiguous, unreadable, or only partially matches, "
    "answer yellow rather than guessing. When you report a violation, quote the "
    "offending clause verbatim from the document. Return one item for every rule you "
    "are asked about. Your output is general information, not legal advice."
)


def clause_instruction(
    family_name: str,
    as_at: date,
    sections: dict[tuple[str, str], str],
    rules: list[tuple[str, str]],
) -> str:
    parts = [f"Check family: {family_name}. Statutory text in force at {as_at.isoformat()}:"]
    for (slug, section_no), text in sections.items():
        parts.append(f"--- {slug} s {section_no} ---\n{text}")
    parts.append(
        "Judge the document against each rule below. Return exactly one item per "
        "rule_id. verdict red means the rule is breached, green means it is not, "
        "yellow means you cannot tell. For red verdicts, clause_quote must be the "
        "verbatim offending text from the document."
    )
    for rule_id, question in rules:
        parts.append(f"- {rule_id}: {question}")
    return "\n\n".join(parts)


def fields_instruction() -> str:
    names = ", ".join(FIELD_NAMES)
    return (
        "Extract the following lease fields from the document, exactly as written: "
        f"{names}. Return one item per field. document_value is the verbatim value "
        "from the document, or null if the document does not state it. quote is the "
        "sentence or table cell it came from. Do not convert units or normalise; "
        "copy what the document says."
    )
