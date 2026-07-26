from datetime import date

from app.monitor.runner import diff_findings, new_version_dates


def _f(rule_id, verdict):
    return {"rule_id": rule_id, "verdict": verdict}


def test_diff_verdict_flip():
    delta = diff_findings(
        [_f("nsw.bond_max_4_weeks", "green")], [_f("nsw.bond_max_4_weeks", "red")]
    )
    assert delta == {"nsw.bond_max_4_weeks": {"from": "green", "to": "red"}}


def test_diff_skipped_transition_counts():
    delta = diff_findings(
        [_f("nsw.fixed_term_increase_disclosure", "red")],
        [_f("nsw.fixed_term_increase_disclosure", "skipped")],
    )
    assert delta == {"nsw.fixed_term_increase_disclosure": {"from": "red", "to": "skipped"}}


def test_diff_rule_added_and_removed():
    delta = diff_findings([_f("nsw.old_rule", "green")], [_f("nsw.new_rule", "green")])
    assert delta == {
        "nsw.old_rule": {"from": "green", "to": None},
        "nsw.new_rule": {"from": None, "to": "green"},
    }


def test_diff_no_change_is_empty():
    same = [_f("nsw.bond_max_4_weeks", "red"), _f("nsw.no_other_security", "skipped")]
    assert diff_findings(same, list(same)) == {}


def test_new_version_dates_subtracts_and_sorts():
    timeline = [date(2026, 6, 10), date(2010, 6, 17), date(2026, 9, 1)]
    ingested = {date(2010, 6, 17), date(2026, 6, 10)}
    assert new_version_dates(timeline, ingested) == [date(2026, 9, 1)]
