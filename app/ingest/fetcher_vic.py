"""VIC legislation fetchers: plain httpx, no browser needed."""

import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from selectolax.parser import HTMLParser

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
FETCH_PAUSE_SECONDS = 1.0
VIC_CACHE_ROOT = Path("data/raw/vic")

_DATE_RE = re.compile(r"(\d{1,2} \w+ \d{4})")


@dataclass(frozen=True)
class VersionInfo:
    number: str
    effective_date: date
    status: str


def _get(url: str) -> httpx.Response:
    response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response


def _parse_date(text: str) -> date:
    raw = _DATE_RE.search(text).group(1)
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable version date: {raw!r}")


def list_versions(landing_url: str) -> list[VersionInfo]:
    """Version-history rows from the landing page, ascending by date.

    When two version numbers share an effective date, only the
    highest-numbered one is kept - it superseded the other same-day.
    """
    html = _get(landing_url).text
    row_href = re.compile(rf"^{re.escape(landing_url)}/(\d+)$")
    seen: dict[str, VersionInfo] = {}
    for node in HTMLParser(html).css("a"):
        href = node.attributes.get("href", "") or ""
        match = row_href.match(href)
        if match is None:
            continue
        number = match.group(1)
        if number in seen:
            continue
        text = node.text()
        status = "In force" if "In force" in text else "Superseded"
        seen[number] = VersionInfo(number, _parse_date(text), status)

    newest_by_date: dict[date, VersionInfo] = {}
    for version in seen.values():
        current = newest_by_date.get(version.effective_date)
        if current is None or int(version.number) > int(current.number):
            newest_by_date[version.effective_date] = version
    return sorted(newest_by_date.values(), key=lambda v: (v.effective_date, int(v.number)))


def docx_url(landing_url: str, number: str) -> str:
    """The whole-instrument DOCX link on a version page."""
    html = _get(f"{landing_url}/{number}").text
    candidates = [node.attributes.get("href", "") or "" for node in HTMLParser(html).css("a")]
    docx = [
        href
        for href in candidates
        if "content.legislation.vic.gov.au" in href
        and href.endswith(".docx")
        and "authorised" not in href
    ]
    numbered = [href for href in docx if number in href.rsplit("/", 1)[-1]]
    return numbered[0] if numbered else docx[0]


def fetch_docx(url: str, cache_path: Path) -> bytes:
    """Cache-first download; full re-ingests never re-hit the site."""
    if cache_path.exists():
        return cache_path.read_bytes()
    data = _get(url).content
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    time.sleep(FETCH_PAUSE_SECONDS)
    return data
