"""요일 반복 회차 날짜와 반복 문구 (#738).

2026-11-09 는 월요일이다.
"""

from datetime import date

import pytest

from app.rules.recurrence import MAX_REPEAT_DAYS, RepeatError, occurrence_dates, repeat_label


def test_mon_wed_until():
    assert occurrence_dates(date(2026, 11, 9), [1, 3], date(2026, 11, 25)) == [
        date(2026, 11, 9),
        date(2026, 11, 11),
        date(2026, 11, 16),
        date(2026, 11, 18),
        date(2026, 11, 23),
        date(2026, 11, 25),
    ]


def test_first_day_off_weekday_starts_at_next_match():
    # 11/7 은 토요일이라 첫 회차는 다음 월요일이다
    assert occurrence_dates(date(2026, 11, 7), [1, 3], date(2026, 11, 11)) == [
        date(2026, 11, 9),
        date(2026, 11, 11),
    ]


def test_duplicate_and_unordered_weekdays_are_one_set():
    assert occurrence_dates(date(2026, 11, 9), [3, 1, 3, 1], date(2026, 11, 12)) == [
        date(2026, 11, 9),
        date(2026, 11, 11),
    ]


def test_span_is_90_days_including_first():
    assert MAX_REPEAT_DAYS == 90
    # 첫 날 + 89일 = 2027-02-06 까지는 된다
    assert occurrence_dates(date(2026, 11, 9), [1], date(2027, 2, 6))[-1] == date(2027, 2, 1)
    with pytest.raises(RepeatError) as exc:
        occurrence_dates(date(2026, 11, 9), [1], date(2027, 2, 7))
    assert exc.value.code == "too_long"


def test_span_is_measured_from_the_first_occurrence():
    # 11/7(토)에서 말했어도 첫 회차는 11/9(월)이다. 90일은 거기서 센다 — 묶음에 저장되는 날짜가
    # 첫 회차라서 만들 때와 고칠 때가 같은 기준이어야 한다
    assert occurrence_dates(date(2026, 11, 7), [1], date(2027, 2, 5))[-1] == date(2027, 2, 1)
    with pytest.raises(RepeatError) as exc:
        occurrence_dates(date(2026, 11, 7), [1], date(2027, 2, 7))
    assert exc.value.code == "too_long"


def test_until_before_the_first_occurrence_has_no_occurrence():
    # 11/7(토) 다음 월요일은 11/9 인데 끝이 11/8 이다
    with pytest.raises(RepeatError) as exc:
        occurrence_dates(date(2026, 11, 7), [1], date(2026, 11, 8))
    assert exc.value.code == "no_occurrence"


@pytest.mark.parametrize(
    ("weekdays", "until", "code"),
    [
        ([], date(2026, 11, 25), "no_weekday"),
        ([0], date(2026, 11, 25), "bad_weekday"),
        ([8], date(2026, 11, 25), "bad_weekday"),
        ([1], date(2026, 11, 8), "until_before_start"),
        ([5], date(2026, 11, 10), "no_occurrence"),
    ],
)
def test_rejects(weekdays, until, code):
    with pytest.raises(RepeatError) as exc:
        occurrence_dates(date(2026, 11, 9), weekdays, until)
    assert exc.value.code == code


def test_until_on_first_day_is_one_occurrence():
    assert occurrence_dates(date(2026, 11, 9), [1], date(2026, 11, 9)) == [date(2026, 11, 9)]


def test_label():
    assert repeat_label([3, 1], date(2026, 12, 20)) == "12월 20일까지 매주 월, 수"
    assert repeat_label([1, 2, 3, 4, 5], date(2026, 12, 20)) == "12월 20일까지 평일"
    assert repeat_label(range(1, 8), date(2026, 12, 20)) == "12월 20일까지 매일"
    assert repeat_label([6], date(2026, 12, 20)) == "12월 20일까지 매주 토"


def test_label_rejects_unknown_weekday():
    with pytest.raises(RepeatError) as exc:
        repeat_label([1, 9], date(2026, 12, 20))
    assert exc.value.code == "bad_weekday"
