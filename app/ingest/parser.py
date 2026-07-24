import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser


@dataclass(frozen=True)
class ParsedSection:
    section_no: str
    heading: str
    body_text: str
    part: str | None
    division: str | None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _ancestor_heading(node, ancestor_class: str) -> str | None:
    current = node.parent
    while current is not None:
        classes = current.attributes.get("class", "") or ""
        if ancestor_class in classes.split():
            heading = current.css_first(":scope > .heading") or current.css_first(".heading")
            return _clean(heading.text()) if heading else None
        current = current.parent
    return None


def parse_whole_act(html: str) -> list[ParsedSection]:
    """Extract the act's numbered sections from a whole-act HTML page."""
    tree = HTMLParser(html)
    sections: list[ParsedSection] = []
    for clause in tree.css("div.frag-clause"):
        node_id = clause.attributes.get("id", "") or ""
        if not node_id.startswith("sec."):
            continue
        section_no = node_id.removeprefix("sec.")
        for note in clause.css(".frag-historynote, .view-history-note"):
            note.decompose()
        heading_node = clause.css_first(".heading")
        raw_heading = _clean(heading_node.text()) if heading_node else ""
        heading = _clean(re.sub(rf"^{re.escape(section_no)}\s+", "", raw_heading))
        body_node = clause.css_first("blockquote.children")
        body_text = _clean(body_node.text()) if body_node else ""
        if heading in ("(Repealed)", "") and body_text == "":
            continue
        sections.append(
            ParsedSection(
                section_no=section_no,
                heading=heading,
                body_text=body_text,
                part=_ancestor_heading(clause, "frag-part"),
                division=_ancestor_heading(clause, "frag-division"),
            )
        )
    return sections
