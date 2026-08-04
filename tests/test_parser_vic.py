import io

from docx import Document

from app.ingest.parser_vic import SECTION_HEADING_STYLES, parse_docx

HEAD = SECTION_HEADING_STYLES[0]


def build_docx(paragraphs: list[tuple[str | None, str]]) -> bytes:
    """A minimal DOCX from (style_name, text) rows; None = default style."""
    doc = Document()
    for style_name, text in paragraphs:
        paragraph = doc.add_paragraph(text)
        if style_name is not None:
            from docx.enum.style import WD_STYLE_TYPE

            styles = doc.styles
            if style_name not in [s.name for s in styles]:
                styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
            paragraph.style = styles[style_name]
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_sections_split_with_part_and_division_tracking():
    data = build_docx(
        [
            (None, "Part 2—Tenancy agreements"),
            (None, "Division 1—General"),
            (HEAD, "26\tApplication of Part"),
            (None, "This Part applies to all agreements."),
            (HEAD, "27B\tProhibited terms"),
            (None, "A term listed below must not be included."),
            (None, "Penalty: 60 penalty units."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["26", "27B"]
    assert sections[1].heading == "Prohibited terms"
    assert "must not be included" in sections[1].body_text
    assert "Penalty" in sections[1].body_text
    assert sections[0].part == "Part 2—Tenancy agreements"
    assert sections[0].division == "Division 1—General"


def test_subdivision_updates_division_label():
    data = build_docx(
        [
            (None, "Part 2—Tenancy agreements"),
            (None, "Division 2—Applications"),
            (HEAD, "10\tFirst"),
            (None, "Body one."),
            (None, "Subdivision 1—Application to rental agreements"),
            (HEAD, "11\tSecond"),
            (None, "Body two."),
        ]
    )
    sections = parse_docx(data)
    assert sections[0].division == "Division 2—Applications"
    assert sections[1].division == "Subdivision 1—Application to rental agreements"


def test_body_lines_starting_with_numbers_stay_in_body():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (HEAD, "5\tDefinitions"),
            (None, "14 days means a fortnight in this test body."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["5"]
    assert "14 days" in sections[0].body_text


def test_regex_fallback_when_styles_unpinned(monkeypatch):
    from app.ingest import parser_vic

    monkeypatch.setattr(parser_vic, "SECTION_HEADING_STYLES", ())
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (None, "1 Purposes"),
            (None, "The purposes of this Act are set out."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1"]


def test_toc_lines_are_skipped_by_style():
    data = build_docx(
        [
            ("toc 3", "27B Prohibited terms 55"),
            (None, "Part 1—Preliminary"),
            (HEAD, "1\tPurposes"),
            (None, "The purposes of this Act are set out."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1"]


def test_parsing_stops_at_endnotes():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (HEAD, "1\tPurposes"),
            (None, "Body text."),
            (None, "Endnotes"),
            (HEAD, "2\tThis looks like a section but is history."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1"]


def test_schedule_clauses_get_prefixed_keys():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (HEAD, "1\tPurposes"),
            (None, "Body."),
            (None, "Schedule 1—Transitional provisions"),
            (HEAD, "1\tSaved instruments"),
            (None, "Schedule body."),
            (None, "Schedule 1A—Pecuniary penalty provisions"),
            (HEAD, "2\tPenalty items"),
            (None, "Item list."),
            (None, "Schedule 4—Validation"),
            (None, "Part 1—Preliminary"),
            (HEAD, "1\tDefinition"),
            (None, "Internal part body."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1", "S1-1", "S1A-2", "S4-1"]
    assert sections[1].part == "Schedule 1—Transitional provisions"
    assert sections[1].division is None
    assert sections[3].part == "Schedule 4—Validation"
    assert sections[3].division == "Part 1—Preliminary"


def test_repealed_placeholder_loads_as_shown():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (HEAD, "27A\tRepealed"),
            (None, "* * * * *"),
        ]
    )
    sections = parse_docx(data)
    assert sections[0].section_no == "27A"
    assert sections[0].heading == "Repealed"


def test_partless_document_collects_sections_with_pinned_styles():
    """The Regulations open with clauses before any Part heading."""
    data = build_docx(
        [
            (None, "Version No. 009"),
            (HEAD, "1\tObjective"),
            (None, "The objective of these Regulations is to prescribe matters."),
            (HEAD, "2\tAuthorising provision"),
            (None, "Made under section 511."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1", "2"]
    assert sections[0].part is None
