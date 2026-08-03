from datetime import date
from pathlib import Path

import respx
from httpx import Response

from app.ingest.fetcher_vic import VersionInfo, docx_url, fetch_docx, list_versions

LANDING = "https://www.legislation.vic.gov.au/in-force/acts/residential-tenancies-act-1997"

LANDING_HTML = f"""
<html><body>
<div class="version-history">
  <a href="{LANDING}/113">1 July 2026 113 In force</a>
  <a href="{LANDING}/113#rpl-above-body">1 July 2026 113 In force</a>
  <a href="{LANDING}/112">30 June 2026 112 Superseded</a>
  <a href="{LANDING}/098">29 Mar 2021 098 Superseded</a>
  <a href="{LANDING}">not a version row</a>
</div>
</body></html>
"""

VERSION_HTML = """
<html><body>
<a href="https://content.legislation.vic.gov.au/sites/default/files/2026-07/97-109aa113-authorised.pdf">pdf</a>
<a href="https://content.legislation.vic.gov.au/sites/default/files/2026-07/97-109a113.docx">docx</a>
</body></html>
"""


@respx.mock
def test_list_versions_parses_rows_ascending():
    respx.get(LANDING).mock(return_value=Response(200, text=LANDING_HTML))
    versions = list_versions(LANDING)
    assert versions == [
        VersionInfo("098", date(2021, 3, 29), "Superseded"),
        VersionInfo("112", date(2026, 6, 30), "Superseded"),
        VersionInfo("113", date(2026, 7, 1), "In force"),
    ]


@respx.mock
def test_docx_url_skips_authorised_pdf():
    respx.get(f"{LANDING}/113").mock(return_value=Response(200, text=VERSION_HTML))
    url = docx_url(LANDING, "113")
    assert url.endswith("/97-109a113.docx")


@respx.mock
def test_fetch_docx_caches(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.ingest.fetcher_vic.FETCH_PAUSE_SECONDS", 0)
    url = "https://content.legislation.vic.gov.au/files/97-109a113.docx"
    route = respx.get(url).mock(return_value=Response(200, content=b"DOCXBYTES"))
    cache = tmp_path / "vic" / "residential-tenancies-act-1997" / "113.docx"

    first = fetch_docx(url, cache)
    assert first == b"DOCXBYTES"
    assert cache.read_bytes() == b"DOCXBYTES"
    assert route.call_count == 1

    second = fetch_docx(url, cache)
    assert second == b"DOCXBYTES"
    assert route.call_count == 1
