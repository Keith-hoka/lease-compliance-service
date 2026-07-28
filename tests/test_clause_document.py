from app.clause_audit.document import CHARS_PER_PAGE_MIN, document_input
from tests.fixtures.pdfs import make_scanned_pdf, make_text_pdf

LEASE = (
    "RESIDENTIAL TENANCY AGREEMENT. The weekly rent is $560 payable weekly. "
    "The tenant must have the carpet professionally cleaned at the end of the tenancy. "
) * 10


def test_plain_text_takes_text_path():
    doc = document_input("text", b"rent is $560")
    assert doc.kind == "text" and doc.text == "rent is $560" and doc.pdf is None


def test_text_layer_pdf_takes_text_path():
    doc = document_input("pdf", make_text_pdf(LEASE))
    assert doc.kind == "text"
    assert "professionally cleaned" in doc.text


def test_scanned_pdf_falls_back_to_pdf_path():
    raw = make_scanned_pdf(LEASE)
    doc = document_input("pdf", raw)
    assert doc.kind == "pdf" and doc.pdf == raw and doc.text is None


def test_threshold_constant():
    assert CHARS_PER_PAGE_MIN == 200
