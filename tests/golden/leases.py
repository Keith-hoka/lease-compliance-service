"""Golden lease cases: (case_id, lease_kwargs, expected non-skipped verdicts).

Expectations hold at the audit date 2026-07-24 with the baseline lease of
600 weekly rent starting 2026-01-01. The fixed-term disclosure rule (s42,
repealed 2024-12-13) is inactive at that date, so it never appears.
"""

BASE = {"rent_amount": "600", "rent_frequency": "weekly", "start_date": "2026-01-01"}


def _case(case_id: str, expected: dict[str, str], **kw) -> tuple[str, dict, dict[str, str]]:
    return case_id, {**BASE, **kw}, expected


GOLDEN: list[tuple[str, dict, dict[str, str]]] = [
    _case("compliant_minimal", {}),
    _case(
        "compliant_full",
        {
            "nsw.bond_max_4_weeks": "green",
            "nsw.rent_in_advance_max": "green",
            "nsw.holding_fee_max_1_week": "green",
            "nsw.rent_increase_frequency": "green",
            "nsw.rent_increase_first_year": "green",
            "nsw.rent_increase_notice": "green",
            "nsw.no_other_security": "green",
            "nsw.break_fee_cap": "green",
        },
        bond_amount="2400",
        rent_in_advance_amount="1200",
        holding_deposit_amount="600",
        other_security_amount="0",
        break_fee_amount="2400",
        end_date="2028-01-01",
        rent_increases=[
            {"effective_on": "2027-02-01", "new_amount": "620", "notice_given_on": "2026-11-01"},
            {"effective_on": "2028-03-01", "new_amount": "640", "notice_given_on": "2027-12-01"},
        ],
    ),
    _case("bond_five_weeks", {"nsw.bond_max_4_weeks": "red"}, bond_amount="3000"),
    _case("bond_exactly_at_cap", {"nsw.bond_max_4_weeks": "green"}, bond_amount="2400"),
    _case("advance_three_weeks", {"nsw.rent_in_advance_max": "red"}, rent_in_advance_amount="1800"),
    _case(
        "holding_fee_over_cap",
        {"nsw.holding_fee_max_1_week": "red"},
        holding_deposit_amount="700",
    ),
    _case(
        "increases_eight_months_apart",
        {
            "nsw.rent_increase_frequency": "red",
            "nsw.rent_increase_first_year": "green",
            "nsw.rent_increase_notice": "green",
        },
        rent_increases=[
            {"effective_on": "2027-02-01", "new_amount": "620"},
            {"effective_on": "2027-10-01", "new_amount": "640"},
        ],
    ),
    _case(
        "increases_exactly_twelve_months",
        {
            "nsw.rent_increase_frequency": "green",
            "nsw.rent_increase_first_year": "green",
            "nsw.rent_increase_notice": "green",
        },
        rent_increases=[
            {"effective_on": "2027-02-01", "new_amount": "620"},
            {"effective_on": "2028-02-01", "new_amount": "640"},
        ],
    ),
    _case(
        "notice_45_days",
        {
            "nsw.rent_increase_frequency": "green",
            "nsw.rent_increase_first_year": "green",
            "nsw.rent_increase_notice": "red",
        },
        rent_increases=[
            {"effective_on": "2027-06-01", "new_amount": "620", "notice_given_on": "2027-04-17"}
        ],
    ),
    _case(
        "notice_exactly_60_days",
        {
            "nsw.rent_increase_frequency": "green",
            "nsw.rent_increase_first_year": "green",
            "nsw.rent_increase_notice": "green",
        },
        rent_increases=[
            {"effective_on": "2027-06-01", "new_amount": "620", "notice_given_on": "2027-04-02"}
        ],
    ),
    _case(
        "first_increase_six_months_in",
        {
            "nsw.rent_increase_frequency": "green",
            "nsw.rent_increase_first_year": "red",
            "nsw.rent_increase_notice": "green",
        },
        rent_increases=[{"effective_on": "2026-07-01", "new_amount": "620"}],
    ),
    _case(
        "first_increase_exactly_one_year",
        {
            "nsw.rent_increase_frequency": "green",
            "nsw.rent_increase_first_year": "green",
            "nsw.rent_increase_notice": "green",
        },
        rent_increases=[{"effective_on": "2027-01-01", "new_amount": "620"}],
    ),
    _case("other_security_500", {"nsw.no_other_security": "red"}, other_security_amount="500"),
    _case("zero_other_security", {"nsw.no_other_security": "green"}, other_security_amount="0"),
    _case(
        "break_fee_five_weeks",
        {"nsw.break_fee_cap": "red"},
        break_fee_amount="3000",
        end_date="2028-01-01",
    ),
    _case(
        "break_fee_at_four_weeks",
        {"nsw.break_fee_cap": "green"},
        break_fee_amount="2400",
        end_date="2028-01-01",
    ),
    _case(
        "break_fee_scale_not_applicable_long_term",
        {"nsw.break_fee_cap": "green"},
        break_fee_amount="5000",
        end_date="2030-01-01",
    ),
    _case(
        "combined_bond_and_security",
        {"nsw.bond_max_4_weeks": "red", "nsw.no_other_security": "red"},
        bond_amount="3000",
        other_security_amount="500",
    ),
    _case(
        "combined_upfront_money_violations",
        {
            "nsw.bond_max_4_weeks": "red",
            "nsw.rent_in_advance_max": "red",
            "nsw.holding_fee_max_1_week": "red",
        },
        bond_amount="3000",
        rent_in_advance_amount="1800",
        holding_deposit_amount="700",
    ),
    _case(
        "combined_increase_violations",
        {
            "nsw.rent_increase_frequency": "red",
            "nsw.rent_increase_first_year": "red",
            "nsw.rent_increase_notice": "red",
        },
        rent_increases=[
            {"effective_on": "2026-07-01", "new_amount": "620"},
            {"effective_on": "2027-02-01", "new_amount": "640", "notice_given_on": "2026-12-18"},
        ],
    ),
    _case(
        "monthly_rent_bond_at_cap",
        {"nsw.bond_max_4_weeks": "green"},
        rent_amount="2600",
        rent_frequency="monthly",
        bond_amount="2400",
    ),
    _case(
        "fortnightly_rent_advance_over_cap",
        {"nsw.rent_in_advance_max": "red"},
        rent_amount="1200",
        rent_frequency="fortnightly",
        rent_in_advance_amount="1300",
    ),
]
