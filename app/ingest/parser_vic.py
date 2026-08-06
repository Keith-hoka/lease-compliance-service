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

Prescribed forms inside a schedule (New Form Heading paragraphs) yield
their numbered terms as S{sch}-F{form}-T{term}, with the form identity
in division and the schedule heading in part.
"""

import io
import re

from docx import Document

from app.ingest.parser import ParsedSection

SECTION_HEADING_STYLES: tuple[str, ...] = ("Draft Heading 1",)
FORM_HEADING_STYLE = "New Form Heading"
SIDE_NOTE_STYLE = "Side Note"

_PART_RE = re.compile(r"^Part \d+[A-Z]*—")
_DIVISION_RE = re.compile(r"^(?:Division|Subdivision) \d+[A-Z]*—")
_SCHEDULE_RE = re.compile(r"^Schedule (\d+[A-Z]*)—")
_SECTION_RE = re.compile(r"^(\d+[A-Z]*)\s+(\S.*)$")
_FORM_RE = re.compile(r"^Form (\d+[A-Z]?)\b")
_FORM_TERM_RE = re.compile(r"^(\d+[A-Z]?)\.\t(.+)$", re.DOTALL)


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
    form_no: str | None = None
    form_title: str | None = None
    term: dict | None = None

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

    def flush_term() -> None:
        nonlocal term
        if term is not None:
            sections.append(
                ParsedSection(
                    section_no=f"S{schedule_no}-F{form_no}-T{term['no']}",
                    heading=term["heading"],
                    body_text=_clean(" ".join(term["body"])),
                    part=part,
                    division=_clean(f"Form {form_no} {form_title or ''}"),
                )
            )
            term = None

    for paragraph in document.paragraphs:
        style_name = paragraph.style.name if paragraph.style is not None else ""
        if style_name.lower().startswith("toc"):
            continue
        text = _clean(paragraph.text)
        if not text:
            continue
        if text == "Endnotes":
            flush_term()
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
            flush_term()
            part, division, schedule_no, started = text, None, schedule.group(1), True
            form_no, form_title = None, None
            continue
        if _DIVISION_RE.match(text):
            flush()
            division = text
            continue
        if schedule_no is not None and style_name == FORM_HEADING_STYLE:
            flush_term()
            form_match = _FORM_RE.match(text)
            if form_match:
                form_no, form_title = form_match.group(1), None
            elif form_no is not None and form_title is None and not text.startswith("PART"):
                form_title = text
            continue
        if form_no is not None and style_name == SIDE_NOTE_STYLE:
            continue
        if form_no is not None:
            term_match = _FORM_TERM_RE.match(paragraph.text)
            if term_match:
                flush_term()
                term = {
                    "no": term_match.group(1),
                    "heading": _clean(term_match.group(2)),
                    "body": [],
                }
                continue
            if term is not None:
                term["body"].append(text)
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
    flush_term()
    return sections
