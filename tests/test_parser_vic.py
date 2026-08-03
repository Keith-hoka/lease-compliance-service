import io

from docx import Document

from app.ingest.parser_vic import parse_docx


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
            (None, "26 Application of Part"),
            (None, "This Part applies to all agreements."),
            (None, "27B Prohibited terms"),
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


def test_toc_is_skipped_and_collection_starts_at_first_part():
    data = build_docx(
        [
            ("TOC 1", "27B Prohibited terms 55"),
            (None, "27B Prohibited terms"),
            (None, "Part 1—Preliminary"),
            (None, "1 Purposes"),
            (None, "The purposes of this Act are set out."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1"]


def test_parsing_stops_at_endnotes():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (None, "1 Purposes"),
            (None, "Body text."),
            (None, "Endnotes"),
            (None, "2 This looks like a section but is history."),
        ]
    )
    sections = parse_docx(data)
    assert [s.section_no for s in sections] == ["1"]


def test_schedule_becomes_part_label():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (None, "1 Purposes"),
            (None, "Body."),
            (None, "Schedule 1—Transitional provisions"),
            (None, "1 Saved instruments"),
            (None, "Schedule body."),
        ]
    )
    sections = parse_docx(data)
    assert sections[-1].part == "Schedule 1—Transitional provisions"
    assert sections[-1].section_no == "1"
    assert sections[-1].division is None


def test_repealed_placeholder_loads_as_shown():
    data = build_docx(
        [
            (None, "Part 1—Preliminary"),
            (None, "27A Repealed"),
            (None, "* * * * *"),
        ]
    )
    sections = parse_docx(data)
    assert sections[0].section_no == "27A"
    assert sections[0].heading == "Repealed"


def test_pinned_style_wins_over_regex():
    from app.ingest import parser_vic

    original = parser_vic.SECTION_HEADING_STYLES
    parser_vic.SECTION_HEADING_STYLES = ("SectionHead",)
    try:
        data = build_docx(
            [
                (None, "Part 1—Preliminary"),
                ("SectionHead", "5 Definitions"),
                (None, "14 days means a fortnight in this test body."),
            ]
        )
        sections = parse_docx(data)
        assert [s.section_no for s in sections] == ["5"]
        assert "14 days" in sections[0].body_text
    finally:
        parser_vic.SECTION_HEADING_STYLES = original
