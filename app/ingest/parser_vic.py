"""Parse a whole VIC instrument DOCX into sections.

Classification is style-first: when SECTION_HEADING_STYLES is pinned
(by the rollout spike, from real authorised-version style names), only
those styles start a section. While it is empty, a regex fallback
matches headings like "27B Prohibited terms".
"""

import io
import re

from docx import Document

from app.ingest.parser import ParsedSection

SECTION_HEADING_STYLES: tuple[str, ...] = ()

_PART_RE = re.compile(r"^Part \d+[A-Z]*—")
_DIVISION_RE = re.compile(r"^Division \d+[A-Z]*—")
_SCHEDULE_RE = re.compile(r"^Schedule \d+[A-Z]*—")
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
            part, division, started = text, None, True
            continue
        if _SCHEDULE_RE.match(text):
            flush()
            part, division, started = text, None, True
            continue
        if _DIVISION_RE.match(text):
            flush()
            division = text
            continue
        if not started:
            continue
        match = _SECTION_RE.match(text)
        if match and _is_section_heading(style_name, text):
            flush()
            current = {
                "section_no": match.group(1),
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
