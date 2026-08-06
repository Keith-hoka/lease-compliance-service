from datetime import date

import pytest

from app.rules.base import add_months


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2025, 1, 15), 12, date(2026, 1, 15)),
        (date(2023, 3, 1), 12, date(2024, 3, 1)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2025, 1, 31), 1, date(2025, 2, 28)),
        (date(2025, 3, 31), 1, date(2025, 4, 30)),
        (date(2025, 11, 15), 2, date(2026, 1, 15)),
        (date(2023, 3, 1), 24, date(2025, 3, 1)),
        (date(2023, 3, 1), 36, date(2026, 3, 1)),
        (date(2024, 2, 29), 48, date(2028, 2, 29)),
    ],
)
def test_add_months_corresponding_date_rule(start, months, expected):
    assert add_months(start, months) == expected
