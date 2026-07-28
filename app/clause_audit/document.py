"""Render a stored document for the LLM: text when a usable layer exists."""

import io
from dataclasses import dataclass
from typing import Literal

from pypdf import PdfReader

CHARS_PER_PAGE_MIN = 200


@dataclass(frozen=True)
class DocumentInput:
    kind: Literal["text", "pdf"]
    text: str | None = None
    pdf: bytes | None = None


def extract_pdf_text(data: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages), len(pages)


def document_input(kind: str, raw: bytes) -> DocumentInput:
    if kind == "text":
        return DocumentInput(kind="text", text=raw.decode("utf-8"))
    text, page_count = extract_pdf_text(raw)
    if page_count and len(text) / page_count >= CHARS_PER_PAGE_MIN:
        return DocumentInput(kind="text", text=text)
    return DocumentInput(kind="pdf", pdf=raw)
