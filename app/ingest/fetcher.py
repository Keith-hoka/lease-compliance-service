import re
import time
from datetime import date
from pathlib import Path

from selectolax.parser import HTMLParser

LANDING_URL_TEMPLATE = "https://legislation.nsw.gov.au/view/html/inforce/current/{slug}"
WHOLE_ACT_URL_TEMPLATE = "https://legislation.nsw.gov.au/view/whole/html/inforce/{version}/{slug}"


def parse_version_dates(html: str) -> list[date]:
    """The point-in-time version dates listed on an act landing page.

    Each version is a .timeline-item whose .timeline-item-description holds the
    version start date; hover popups and compare links carry other dates and are
    deliberately not searched.
    """
    tree = HTMLParser(html)
    dates = set()
    for node in tree.css("#pointInTimeBar .timeline-item-description"):
        for match in re.finditer(r"\b(\d{2})/(\d{2})/(\d{4})\b", node.text()):
            day, month, year = match.groups()
            dates.add(date(int(year), int(month), int(day)))
    return sorted(dates)


def _launch(playwright):
    """A headed real-Chrome browser; headless fingerprints fail the site's bot check."""
    return playwright.chromium.launch(channel="chrome", headless=False)


def fetch_landing(slug: str) -> str:
    """Fetch the act landing page HTML with a real browser."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        page.goto(LANDING_URL_TEMPLATE.format(slug=slug), wait_until="domcontentloaded")
        page.wait_for_selector("#pointInTimeBar", state="attached", timeout=30000)
        html = page.content()
        browser.close()
    return html


def fetch_versions(
    slug: str, dates: list[date], cache_dir: Path, delay_seconds: float = 2.0
) -> list[Path]:
    """Fetch each version's whole-act HTML into cache_dir; skip cached dates."""
    from playwright.sync_api import sync_playwright

    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    missing = [d for d in dates if not (cache_dir / f"{d.isoformat()}.html").exists()]
    for d in dates:
        paths.append(cache_dir / f"{d.isoformat()}.html")
    if not missing:
        return paths
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        for d in missing:
            url = WHOLE_ACT_URL_TEMPLATE.format(version=d.isoformat(), slug=slug)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("div.frag-clause", timeout=60000)
            (cache_dir / f"{d.isoformat()}.html").write_text(page.content())
            time.sleep(delay_seconds)
        browser.close()
    return paths
