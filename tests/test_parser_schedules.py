from pathlib import Path

import pytest

from app.ingest.parser import parse_whole_act

FIXTURE = """
<div id="sec.5" class="frag-clause">
  <div role="heading" class="heading"><span class="frag-no">5</span> <span class="frag-heading">Ordinary clause</span></div>
  <blockquote class="children">Body of the ordinary clause.</blockquote>
</div>
<div id="sch.1" class="frag-schedule">
  <div role="heading" class="heading"><span class="frag-no">Schedule 1</span> <span id="sch.1-he" class="frag-heading">Standard Form Agreement</span></div>
  <div id="sch.1-sec.2." class="frag-clause">
    <div role="heading" class="heading"><span class="frag-no">2.</span> <span class="frag-heading">Ending this agreement</span></div>
    <blockquote class="children">Schedule clause body.</blockquote>
    <div class="frag-historynote">history noise</div>
  </div>
  <div id="sch.1-form" class="frag-form">
    <div id="sch.1-form-bg1" class="frag-blockgroup">
      <div class="frag-head"><div class="heading joined"><span class="frag-heading">This agreement is made</span></div></div>
      <div id="sch.1-form-bg1-bg2" class="frag-blockgroup">
        <div class="frag-head"><div class="heading joined"><span class="frag-heading">RENT</span></div></div>
        <div class="frag-block">
          <div id="sch.1-form-bg1-bg2-para1.7." class="frag-li"><blockquote class="children"><span class="frag-no"><b>7.</b></span>&#160;&#160;<b>The tenant agrees</b> to pay rent on time.
            <div class="frag-li"><blockquote class="children"><span class="frag-no">(a)</span> by the method chosen.</blockquote></div>
            <div class="frag-li"><blockquote class="children"><span class="frag-no">2.</span> Nested marker text that must survive.</blockquote></div>
          </blockquote></div>
        </div>
      </div>
    </div>
  </div>
</div>
<div id="sch.2" class="frag-schedule">
  <div role="heading" class="heading"><span class="frag-no">Schedule 2</span> <span class="frag-heading">Condition report</span></div>
  <div class="frag-form"><div class="frag-table">table only, no numbered terms</div></div>
</div>
<div id="sch.4" class="frag-schedule">
  <div role="heading" class="heading"><span class="frag-no">Schedule 4</span> <span class="frag-heading">Penalty notice offences</span></div>
  <div id="sch.4-sec.1" class="frag-clause">
    <div role="heading" class="heading"><span class="frag-no">1</span> <span class="frag-heading">Application of Schedule</span></div>
    <blockquote class="children">Applies to offences.</blockquote>
  </div>
</div>
"""


def _by_no(sections):
    return {s.section_no: s for s in sections}


def test_schedule_clauses_and_form_terms_parse():
    sections = _by_no(parse_whole_act(FIXTURE))
    assert set(sections) == {"5", "S1-2", "S1-T7", "S4-1"}

    clause = sections["S1-2"]
    assert clause.heading == "Ending this agreement"
    assert clause.body_text == "Schedule clause body."
    assert clause.division == "Schedule 1 Standard Form Agreement"
    assert clause.part is None

    term = sections["S1-T7"]
    assert term.heading == "RENT"
    assert term.body_text.startswith("The tenant agrees")
    assert "(a) by the method chosen." in term.body_text
    assert term.division == "Schedule 1 Standard Form Agreement"

    dotless = sections["S4-1"]
    assert dotless.heading == "Application of Schedule"
    assert dotless.division == "Schedule 4 Penalty notice offences"


def test_ordinary_sections_unchanged():
    sections = _by_no(parse_whole_act(FIXTURE))
    assert sections["5"].heading == "Ordinary clause"
    assert sections["5"].body_text == "Body of the ordinary clause."


def test_history_notes_stripped_from_schedule_clauses():
    sections = _by_no(parse_whole_act(FIXTURE))
    assert "history noise" not in sections["S1-2"].body_text


def test_nested_frag_li_does_not_create_phantom_term_key():
    sections = _by_no(parse_whole_act(FIXTURE))
    assert "S1-T2" not in sections
    assert "Nested marker text that must survive." in sections["S1-T7"].body_text


CACHE = Path("data/raw/nsw/sl-2019-0629")


def test_real_regulation_cache_yields_the_standard_form():
    cached = sorted(CACHE.glob("*.html"))
    if not cached:
        pytest.skip("NSW regulation cache not present")
    sections = parse_whole_act(cached[-1].read_text())
    terms = [s for s in sections if s.section_no.startswith("S1-T")]
    clauses = [s for s in sections if s.section_no.startswith("S1-") and "-T" not in s.section_no]
    assert len(terms) >= 55
    # Exact count is deliberate: schedule-level clauses are structurally stable, while the
    # term floor is >= because terms grow with amendments.
    assert len(clauses) == 6
    assert any(s.heading == "RENT" for s in terms)

    s3_terms = {s.section_no: s for s in sections if s.section_no.startswith("S3-T")}
    assert set(s3_terms) == {f"S3-T{n}" for n in range(1, 6)}
    assert {s.heading for s in s3_terms.values()} == {"How to complete this declaration"}
