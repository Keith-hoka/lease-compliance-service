from pathlib import Path

from app.ingest.parser import parse_whole_act

HTML = (Path(__file__).parent / "fixtures" / "mini_act.html").read_text()


def test_parses_all_sections_in_order():
    sections = parse_whole_act(HTML)
    assert [s.section_no for s in sections] == ["1", "159", "159A"]


def test_heading_and_body():
    s159 = parse_whole_act(HTML)[1]
    assert s159.heading == "Payment of bonds"
    assert "exceeding 4 weeks rent" in s159.body_text


def test_history_notes_stripped():
    s159 = parse_whole_act(HTML)[1]
    assert "Am 2018" not in s159.body_text


def test_part_and_division_labels():
    sections = parse_whole_act(HTML)
    assert sections[0].part == "Part 1 Preliminary"
    assert sections[0].division is None
    assert sections[1].part == "Part 8 Rental bonds"
    assert sections[1].division == "Division 1 Payment of bonds"


def test_repealed_placeholders_excluded():
    sections = parse_whole_act(HTML)
    assert "160" not in [s.section_no for s in sections]
