"""The formatter derives the label from the section_no shape alone, so the
NSW/VIC part-division asymmetry never leaks to callers."""

from app.citations import format_citation


def test_plain_section_numbers():
    assert format_citation("52") == "s 52"
    assert format_citation("27B") == "s 27B"


def test_schedule_clauses():
    assert format_citation("S1-1") == "Sch 1 cl 1"
    assert format_citation("S1A-2") == "Sch 1A cl 2"
    assert format_citation("S4-1") == "Sch 4 cl 1"


def test_nsw_standard_form_terms():
    assert format_citation("S1-T5") == "Sch 1 term 5"
    assert format_citation("S1-T30A") == "Sch 1 term 30A"


def test_vic_form_terms():
    assert format_citation("S1-F1-T5") == "Sch 1 Form 1 term 5"
    assert format_citation("S1-F19-T7") == "Sch 1 Form 19 term 7"
    assert format_citation("S1-F16A-T3") == "Sch 1 Form 16A term 3"
