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


_SCH_SEC_RE = re.compile(r"^sch\.([0-9]+[A-Z]?)-sec\.([0-9A-Za-z]+?)\.?$")
_TERM_NO_RE = re.compile(r"^[0-9]+[A-Z]?\.$")


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


def _is_nested_frag_li(node, boundary) -> bool:
    current = node.parent
    while current is not None and current is not boundary:
        classes = current.attributes.get("class", "") or ""
        if "frag-li" in classes.split():
            return True
        current = current.parent
    return False


def _parse_schedules(tree: HTMLParser) -> list[ParsedSection]:
    """Schedule clauses and standard-form terms, in two keyspaces.

    Schedule-level clauses (sch.N-sec.M fragments, trailing dot
    optional) become S{N}-{M}; numbered form terms inside frag-form
    become S{N}-T{M} with the enclosing blockgroup heading.
    """
    sections: list[ParsedSection] = []
    for schedule in tree.css("div.frag-schedule"):
        sch_id = schedule.attributes.get("id", "") or ""
        if not sch_id.startswith("sch."):
            continue
        sch_no = sch_id.removeprefix("sch.")
        heading_node = schedule.css_first(".frag-heading")
        sch_heading = _clean(heading_node.text()) if heading_node else ""
        division = _clean(f"Schedule {sch_no} {sch_heading}")
        for note in schedule.css(".frag-historynote, .view-history-note"):
            note.decompose()
        for clause in schedule.css("div.frag-clause"):
            match = _SCH_SEC_RE.match(clause.attributes.get("id", "") or "")
            if match is None or match.group(1) != sch_no:
                continue
            clause_heading = clause.css_first(".frag-heading")
            body_node = clause.css_first("blockquote.children")
            sections.append(
                ParsedSection(
                    section_no=f"S{sch_no}-{match.group(2)}",
                    heading=_clean(clause_heading.text()) if clause_heading else "",
                    body_text=_clean(body_node.text()) if body_node else "",
                    part=None,
                    division=division,
                )
            )
        form = schedule.css_first("div.frag-form")
        if form is None:
            continue
        for item in form.css("div.frag-li"):
            if _is_nested_frag_li(item, form):
                continue
            no_node = item.css_first(".frag-no")
            if no_node is None:
                continue
            no_text = no_node.text().strip()
            if not _TERM_NO_RE.fullmatch(no_text):
                continue
            no_node.decompose()
            sections.append(
                ParsedSection(
                    section_no=f"S{sch_no}-T{no_text.rstrip('.')}",
                    heading=_ancestor_heading(item, "frag-blockgroup") or "",
                    body_text=_clean(item.text()),
                    part=None,
                    division=division,
                )
            )
    return sections


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
    return sections + _parse_schedules(tree)
