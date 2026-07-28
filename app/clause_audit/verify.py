"""Deterministic checks on model output: quotes and field comparison."""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y")
FREQUENCY_WORDS = {"fortnightly": "fortnight", "monthly": "month", "weekly": "week"}


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def quote_matches(quote: str, document_text: str | None) -> bool:
    """True when the quote appears in the document; PDF path (no text) passes."""
    if document_text is None:
        return True
    return _normalise(quote) in _normalise(document_text)


def parse_amount(value: str) -> Decimal | None:
    cleaned = value.replace("$", "").replace(",", "").replace("AUD", "").strip()
    cleaned = cleaned.split(" ")[0] if cleaned else cleaned
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(value: str) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def parse_frequency(value: str) -> str | None:
    low = value.casefold()
    for name, word in FREQUENCY_WORDS.items():
        if word in low:
            return name
    return None
