"""복약 코스 필드 검증 — 어느 칸이 잘못됐는지 칸 이름 목록으로 돌려준다 (#577).

칸 이름은 400 `validation_failed` 의 `error.detail.fields` 에 그대로 실린다.
길이는 글자 수다(한글 1자 = 1).
"""

from datetime import date, timedelta

import pytest

from app.rules.medication_course import (
    DOSAGE_MAX,
    DOSES_MAX,
    INDICATION_MAX,
    MAX_SPAN_DAYS,
    NOTE_MAX,
    TITLE_MAX,
    course_field_errors,
)

STARTS = date(2026, 10, 27)
PERIOD = ["schedule.starts_on", "schedule.ends_on"]


def errors(**over) -> list[str]:
    values = {
        "title": "아목시실린",
        "dosage": "5ml",
        "indication_text": "중이염",
        "note": None,
        "starts_on": STARTS,
        "ends_on": STARTS + timedelta(days=2),
        "dose_count": 3,
    }
    return course_field_errors(**values | over)


def test_limits_are_the_agreed_values() -> None:
    assert (TITLE_MAX, DOSAGE_MAX, INDICATION_MAX, NOTE_MAX, DOSES_MAX) == (200, 200, 500, 1000, 24)


def test_valid_course_has_no_errors() -> None:
    assert errors() == []


def test_optional_fields_may_be_none_or_empty() -> None:
    # 빈 문자열은 저장할 때 null 로 바뀐다 — 여기서는 오류가 아니다
    assert errors(dosage=None, indication_text=None, note=None) == []
    assert errors(dosage="", indication_text="", note="") == []


# ── title ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("title", ["", " ", "   ", "\t\n", "　"])  # 마지막은 전각 공백
def test_blank_title_is_an_error(title: str) -> None:
    assert errors(title=title) == ["schedule.title"]


def test_title_with_surrounding_spaces_is_fine() -> None:
    assert errors(title="  아목시실린 ") == []


@pytest.mark.parametrize(("length", "bad"), [(1, False), (TITLE_MAX, False), (TITLE_MAX + 1, True)])
def test_title_length_boundary(length: int, bad: bool) -> None:
    assert errors(title="가" * length) == (["schedule.title"] if bad else [])


def test_title_length_counts_characters_not_bytes() -> None:
    assert errors(title="가" * TITLE_MAX) == []  # 한글 200자는 600 바이트다


# ── 선택 칸 길이 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("dosage", DOSAGE_MAX),
        ("indication_text", INDICATION_MAX),
        ("note", NOTE_MAX),
    ],
)
def test_optional_field_length_boundary(field: str, limit: int) -> None:
    assert errors(**{field: "가" * limit}) == []
    assert errors(**{field: "가" * (limit + 1)}) == [f"schedule.{field}"]


# ── 기간 ─────────────────────────────────────────────────────────────────


def test_one_day_period_is_fine() -> None:
    assert errors(ends_on=STARTS) == []


def test_period_of_exactly_max_days_is_fine() -> None:
    assert errors(ends_on=STARTS + timedelta(days=MAX_SPAN_DAYS - 1)) == []


def test_period_over_max_days_is_an_error() -> None:
    assert errors(ends_on=STARTS + timedelta(days=MAX_SPAN_DAYS)) == PERIOD


def test_reversed_period_is_an_error() -> None:
    assert errors(ends_on=STARTS - timedelta(days=1)) == PERIOD


# ── 하루 시각 패턴 개수 ──────────────────────────────────────────────────


@pytest.mark.parametrize(("count", "bad"), [(0, True), (1, False), (DOSES_MAX, False), (25, True)])
def test_dose_count_boundary(count: int, bad: bool) -> None:
    assert errors(dose_count=count) == (["doses"] if bad else [])


# ── 여러 개가 한꺼번에 틀렸을 때 ─────────────────────────────────────────


def test_every_wrong_field_is_reported_once_in_a_fixed_order() -> None:
    result = errors(
        title=" ",
        dosage="가" * (DOSAGE_MAX + 1),
        indication_text="가" * (INDICATION_MAX + 1),
        note="가" * (NOTE_MAX + 1),
        ends_on=STARTS - timedelta(days=1),
        dose_count=0,
    )

    assert result == [
        "schedule.title",
        "schedule.dosage",
        "schedule.indication_text",
        "schedule.note",
        "schedule.starts_on",
        "schedule.ends_on",
        "doses",
    ]
