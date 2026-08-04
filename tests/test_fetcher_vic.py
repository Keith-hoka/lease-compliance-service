from datetime import date
from pathlib import Path

import pytest
import respx
from httpx import Response

from app.ingest.fetcher_vic import VersionInfo, docx_url, fetch_docx, list_versions


@pytest.fixture(autouse=True)
def no_fetch_pause(monkeypatch):
    monkeypatch.setattr("app.ingest.fetcher_vic.FETCH_PAUSE_SECONDS", 0)


LANDING = "https://www.legislation.vic.gov.au/in-force/acts/residential-tenancies-act-1997"

PATH = "/in-force/acts/residential-tenancies-act-1997"

LANDING_HTML = f"""
<html><body>
<div class="version-history">
  <a href="{PATH}/113"><span>1 July 2026</span><span>113</span><span>In force</span></a>
  <a href="{PATH}/113#rpl-above-body">1 July 2026 113 In force</a>
  <a href="{PATH}/112"><span>30 June 2026</span><span>112</span><span>Superseded</span></a>
  <a href="{LANDING}/098">29 Mar 2021 098 Superseded</a>
  <a href="{PATH}/114#frag">2 July 2026 114 In force</a>
  <a href="{PATH}">not a version row</a>
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
    assert all(v.number != "114" for v in versions)


@respx.mock
def test_same_date_versions_keep_the_newest():
    html = f"""
    <html><body>
      <a href="{LANDING}/200">1 May 2024 200 Superseded</a>
      <a href="{LANDING}/199">1 May 2024 199 Superseded</a>
    </body></html>
    """
    respx.get(LANDING).mock(return_value=Response(200, text=html))
    versions = list_versions(LANDING)
    assert [v.number for v in versions] == ["200"]


@respx.mock
def test_docx_url_skips_authorised_pdf():
    respx.get(f"{LANDING}/113").mock(return_value=Response(200, text=VERSION_HTML))
    url = docx_url(LANDING, "113")
    assert url.endswith("/97-109a113.docx")


@respx.mock
def test_docx_url_prefers_file_carrying_the_version_number():
    html = """
    <html><body>
    <a href="https://content.legislation.vic.gov.au/files/97-109a112.docx">old</a>
    <a href="https://content.legislation.vic.gov.au/files/97-109a113.docx">new</a>
    </body></html>
    """
    respx.get(f"{LANDING}/113").mock(return_value=Response(200, text=html))
    assert docx_url(LANDING, "113").endswith("97-109a113.docx")


@respx.mock
def test_fetch_docx_caches(tmp_path: Path):
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


@respx.mock
def test_versions_before_the_floor_are_excluded():
    html = f"""
    <html><body>
      <a href="{PATH}/010">17 Dec 1999 010 Superseded</a>
      <a href="{PATH}/091">25 Apr 2020 091 Superseded</a>
      <a href="{PATH}/113">1 July 2026 113 In force</a>
    </body></html>
    """
    respx.get(LANDING).mock(return_value=Response(200, text=html))
    versions = list_versions(LANDING)
    assert [v.number for v in versions] == ["091", "113"]


@respx.mock
def test_letter_suffixed_versions_parse_and_order_naturally():
    html = f"""
    <html><body>
      <a href="{PATH}/080">2 Feb 2021 080 Superseded</a>
      <a href="{PATH}/079A">1 Feb 2021 079A Superseded</a>
      <a href="{PATH}/079">1 Jan 2021 079 Superseded</a>
    </body></html>
    """
    respx.get(LANDING).mock(return_value=Response(200, text=html))
    versions = list_versions(LANDING)
    assert [v.number for v in versions] == ["079", "079A", "080"]
