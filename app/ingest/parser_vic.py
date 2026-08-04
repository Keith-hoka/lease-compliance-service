"""Parse a whole VIC instrument DOCX into sections.

Classification is style-first: SECTION_HEADING_STYLES carries the real
authorised-version style names pinned by the rollout spike, and only
those styles start a section. Were it ever emptied, a regex fallback
matches headings like "27B Prohibited terms".

Schedule clauses reuse act section numbers, so they are keyed with a
schedule prefix ("S1-1" for Schedule 1 clause 1) to keep section_no
unique within a version. Schedules are terminal in consolidated
instruments (after the Parts, before the Endnotes), and a schedule may
carry its own internal Part headings - those become division labels so
the schedule prefix survives.

The wait-for-first-Part gate exists to keep ToC lines out of the regex
fallback; with pinned styles the toc-style skip already does that, and
the Regulations open with clauses before any Part heading, so the gate
applies only in fallback mode.
"""

import io
import re

from docx import Document

from app.ingest.parser import ParsedSection

SECTION_HEADING_STYLES: tuple[str, ...] = ("Draft Heading 1",)

_PART_RE = re.compile(r"^Part \d+[A-Z]*—")
_DIVISION_RE = re.compile(r"^(?:Division|Subdivision) \d+[A-Z]*—")
_SCHEDULE_RE = re.compile(r"^Schedule (\d+[A-Z]*)—")
_SECTION_RE = re.compile(r"^(\d+[A-Z]*)\s+(\S.*)$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_section_heading(style_name: str, text: str) -> bool:
    if SECTION_HEADING_STYLES:
        return style_name in SECTION_HEADING_STYLES and bool(_SECTION_RE.match(text))
    return bool(_SECTION_RE.match(text))


def parse_docx(data: bytes) -> list[ParsedSection]:
    document = Document(io.BytesIO(data))
    sections: list[ParsedSection] = []
    part: str | None = None
    division: str | None = None
    schedule_no: str | None = None
    current: dict | None = None
    started = False

    def flush() -> None:
        nonlocal current
        if current is not None:
            sections.append(
                ParsedSection(
                    section_no=current["section_no"],
                    heading=current["heading"],
                    body_text=_clean(" ".join(current["body"])),
                    part=current["part"],
                    division=current["division"],
                )
            )
            current = None

    for paragraph in document.paragraphs:
        style_name = paragraph.style.name if paragraph.style is not None else ""
        if style_name.lower().startswith("toc"):
            continue
        text = _clean(paragraph.text)
        if not text:
            continue
        if text == "Endnotes":
            break
        if _PART_RE.match(text):
            flush()
            if schedule_no is None:
                part, division, started = text, None, True
            else:
                division = text
            continue
        schedule = _SCHEDULE_RE.match(text)
        if schedule:
            flush()
            part, division, schedule_no, started = text, None, schedule.group(1), True
            continue
        if _DIVISION_RE.match(text):
            flush()
            division = text
            continue
        if not started and not SECTION_HEADING_STYLES:
            continue
        match = _SECTION_RE.match(text)
        if match and _is_section_heading(style_name, text):
            flush()
            number = match.group(1)
            current = {
                "section_no": f"S{schedule_no}-{number}" if schedule_no else number,
                "heading": match.group(2),
                "part": part,
                "division": division,
                "body": [],
            }
            continue
        if current is not None:
            current["body"].append(text)
    flush()
    return sections
