from datetime import date, timedelta

import pytest

from app.rules.observed_label import observed_label

TODAY = date(2026, 10, 7)


@pytest.mark.parametrize(
    ("days_ago", "label"),
    [(-1, "오늘"), (0, "오늘"), (1, "어제"), (6, "6일 전"), (7, "1주 전"), (20, "2주 전")],
)
def test_observed_label(days_ago: int, label: str) -> None:
    assert observed_label(TODAY - timedelta(days=days_ago), today=TODAY) == label
