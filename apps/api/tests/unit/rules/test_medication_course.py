from datetime import date, timedelta

import pytest

from app.rules.medication_course import MAX_SPAN_DAYS, dose_dates, pattern_day, span_days

TODAY = date(2026, 10, 27)


def test_max_span_is_90_days() -> None:
    assert MAX_SPAN_DAYS == 90


@pytest.mark.parametrize(
    ("starts_on", "ends_on", "days"),
    [
        (date(2026, 10, 27), date(2026, 10, 27), 1),  # 하루짜리 — 끝 − 시작 + 1
        (date(2026, 10, 27), date(2026, 10, 29), 3),
        (date(2026, 10, 30), date(2026, 11, 2), 4),  # 월 경계
        (date(2028, 2, 28), date(2028, 3, 1), 3),  # 윤년 2월 29일이 끼어 있다
        (date(2027, 2, 28), date(2027, 3, 1), 2),  # 평년
        (date(2026, 12, 31), date(2027, 1, 1), 2),  # 연 경계
    ],
)
def test_span_days_counts_both_ends(starts_on: date, ends_on: date, days: int) -> None:
    assert span_days(starts_on, ends_on) == days


def test_span_days_rejects_reversed_period() -> None:
    with pytest.raises(ValueError):
        span_days(date(2026, 10, 29), date(2026, 10, 27))


def test_future_course_gets_every_day() -> None:
    assert dose_dates(date(2026, 10, 28), date(2026, 10, 30), today=TODAY) == [
        date(2026, 10, 28),
        date(2026, 10, 29),
        date(2026, 10, 30),
    ]


def test_course_starting_today_includes_today() -> None:
    assert dose_dates(TODAY, date(2026, 10, 29), today=TODAY) == [
        TODAY,
        date(2026, 10, 28),
        date(2026, 10, 29),
    ]


def test_past_start_skips_past_days_and_keeps_today() -> None:
    # 지난 날짜의 회차는 만들지 않는다. 오늘은 통째로 만든다
    # (provisional — 오늘 이미 지난 시각의 회차를 만들지는 미정)
    assert dose_dates(date(2026, 10, 25), date(2026, 10, 28), today=TODAY) == [
        TODAY,
        date(2026, 10, 28),
    ]


def test_course_ending_today_has_only_today() -> None:
    assert dose_dates(date(2026, 10, 20), TODAY, today=TODAY) == [TODAY]


def test_course_that_ended_yesterday_has_no_days() -> None:
    # provisional — 기간 전체가 과거인 코스를 400 으로 거절할지는 미정. 규칙은 빈 목록만 돌려준다
    assert dose_dates(date(2026, 10, 20), TODAY - timedelta(days=1), today=TODAY) == []


def test_one_day_course_today() -> None:
    assert dose_dates(TODAY, TODAY, today=TODAY) == [TODAY]


def test_max_span_course_expands_to_90_days() -> None:
    starts = date(2026, 11, 1)
    dates = dose_dates(starts, starts + timedelta(days=MAX_SPAN_DAYS - 1), today=TODAY)
    assert len(dates) == 90
    assert dates == sorted(set(dates))  # 오름차순 · 중복 없음


def test_expansion_crosses_leap_day() -> None:
    assert dose_dates(date(2028, 2, 28), date(2028, 3, 1), today=date(2028, 2, 1)) == [
        date(2028, 2, 28),
        date(2028, 2, 29),
        date(2028, 3, 1),
    ]


def test_dose_dates_rejects_reversed_period() -> None:
    with pytest.raises(ValueError):
        dose_dates(date(2026, 10, 29), date(2026, 10, 27), today=TODAY)


# ── 하루 시각 패턴을 읽을 날 ───────────────────────────────────────────


def days(*offsets: int) -> list[date]:
    return [TODAY + timedelta(days=n) for n in offsets]


@pytest.mark.parametrize(
    ("offsets", "expected"),
    [
        ((-2, -1, 0, 1, 2), 1),  # 오늘 다음 날부터 첫 날 — 오늘과 지난 날의 옛 시각을 건너뛴다
        ((0, 1, 2), 1),
        ((1, 2, 3), 1),  # 아직 시작 전인 코스
        ((-2, -1, 0), 0),  # 오늘 끝난다 — 마지막 날이 오늘이다
        ((-3, -2, -1), -1),  # 이미 끝났다 — 마지막 날
        ((0,), 0),  # 하루짜리 코스가 오늘이다
        ((-1,), -1),
        ((5,), 5),
    ],
)
def test_pattern_day_is_first_day_after_today_else_last_day(
    offsets: tuple[int, ...], expected: int
) -> None:
    assert pattern_day(days(*offsets), today=TODAY) == TODAY + timedelta(days=expected)


def test_pattern_day_ignores_order_and_duplicates() -> None:
    # 회차는 날마다 여러 줄이라 같은 날짜가 거듭 온다
    assert pattern_day(days(2, 0, 2, 1, 1, -1), today=TODAY) == TODAY + timedelta(days=1)


def test_pattern_day_of_no_days_is_none() -> None:
    assert pattern_day([], today=TODAY) is None
