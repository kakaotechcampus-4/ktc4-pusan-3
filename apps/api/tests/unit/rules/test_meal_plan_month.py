"""급식표의 연도 · 월 — 코드가 정한다. 모델에게 묻지 않는다 (루트 CLAUDE.md §2 · §3).

급식표 제목에는 연도가 없는 일이 많다("[11]월 급식계획"). 그때 모델이 연도를 지어내면 날짜 전부가
틀리거나(범위 안이면 조용히 저장된다), 범위를 벗어나 저장 전체가 막혔다(2026-11-11 운영 장애).
월은 모델이 읽은 것을 받되, 연도는 사진의 글자에 적혀 있을 때만 믿는다. 적혀 있지 않으면 받은 날의
지난달 · 이번 달 · 다음 달일 때만 코드가 정하고(화면에 알린다), 그 밖이면 정하지 않는다 (11-11 PM).
"""

from datetime import date

import pytest

from app.rules.meal_plan_month import MealMonth, decide_year_month, parse_model_month

RECEIVED = date(2026, 11, 11)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("11", (None, 11)),
        ("11월", (None, 11)),
        ("[11]월", (None, 11)),
        (" 1 ", (None, 1)),
        ("2026-11", (2026, 11)),
        ("2026.11", (2026, 11)),
        ("2026/11", (2026, 11)),
        ("2026년 11월", (2026, 11)),
        ("202611", (2026, 11)),
        ("26-11", (2026, 11)),
        ("26년 11월", (2026, 11)),
        ("2025-01", (2025, 1)),
    ],
)
def test_모델이_낸_연월은_모양이_달라도_월을_읽는다(value: str, expected: tuple) -> None:
    assert parse_model_month(value) == expected


@pytest.mark.parametrize("value", ["", "급식", "13", "0", "2026-13", "2026-00", "11월 2주"])
def test_월을_못_읽으면_None(value: str) -> None:
    assert parse_model_month(value) is None


def _decide(values, transcript="", received_on=RECEIVED) -> MealMonth | None:
    return decide_year_month(values, transcript=transcript, received_on=received_on)


# ── ① 원문에 연도가 적혀 있다 — 그대로 쓰고 알리지 않는다 ──


def test_원문에_연도가_적혀_있으면_그_연도를_믿는다() -> None:
    # 저장소 정답셋 급식표(2025-01) — 받은 날이 지금이어도 사진에 적힌 연도가 맞다
    got = _decide(["2025-01", "2025-01"], "2025년 1월 영양식단")

    assert got == MealMonth(month=1, year=2025, source="written")


def test_모델이_연도를_안_냈어도_원문에_있으면_그_연도다() -> None:
    assert _decide(["11"], "2026년 11월 급식계획") == MealMonth(11, 2026, "written")


@pytest.mark.parametrize(
    "transcript",
    ["2025.01 식단", "2025-1월 식단", "2025년 [1]월 식단", "25년 1월 식단", "2025. 1. 식단"],
)
def test_연도는_월과_붙어_적혀_있으면_믿는다(transcript: str) -> None:
    assert _decide(["2025-01"], transcript) == MealMonth(1, 2025, "written")


# ── ② 원문에 연도가 없다 — 받은 날의 앞뒤 한 달 안이면 코드가 정하고 알린다 ──


def test_연도가_없는_급식표는_받은_날로_연도를_정하고_추정이라고_남긴다() -> None:
    # 운영 장애 사진 — 모델이 "11" 만 냈다. 형식 검사로 표 전체를 버리지 않는다
    assert _decide(["11", "11"], "[11]월 급식계획") == MealMonth(11, 2026, "guessed")


def test_원문에_없는_연도는_모델이_지어낸_것이라_버린다() -> None:
    # 범위 밖 연도면 저장 전체가 400, 범위 안이면 틀린 날짜로 조용히 저장된다
    got = _decide(["2022-11", "2022-11"], "[11]월 급식계획 총 471 kcal")

    assert got == MealMonth(11, 2026, "guessed")


@pytest.mark.parametrize(
    "transcript",
    [
        # 학년도는 연도가 아니다 — 2025학년도 1월은 2026년 1월이다
        "2025학년도 12월 식단",
        # 연도 숫자가 따로 있어도 그 달과 붙어 있지 않으면 근거가 아니다
        "2025 개정 영양기준 · 12월 식단",
        "2025년 1월 · 12월 식단",
    ],
)
def test_월과_붙지_않은_연도는_원문의_연도가_아니다(transcript: str) -> None:
    assert _decide(["2025-12"], transcript) == MealMonth(12, 2026, "guessed")


@pytest.mark.parametrize(
    ("received_on", "month", "year"),
    [
        (date(2026, 11, 11), 10, 2026),  # 지난달
        (date(2026, 11, 11), 11, 2026),  # 이번 달
        (date(2026, 11, 11), 12, 2026),  # 다음 달
        (date(2026, 12, 20), 1, 2027),  # 12월에 받은 1월 → 다음 해
        (date(2026, 1, 5), 12, 2025),  # 1월에 받은 12월 → 지난해
        (date(2026, 12, 31), 11, 2026),  # 달의 마지막 날
        (date(2026, 11, 1), 10, 2026),  # 달의 첫날
        (date(2027, 1, 1), 12, 2026),  # 해의 첫날에 받은 지난달
        (date(2026, 12, 31), 1, 2027),  # 해의 마지막 날에 받은 다음 달
    ],
)
def test_받은_날의_지난달_이번_달_다음_달이면_연도를_정한다(received_on, month, year) -> None:
    assert _decide([str(month)], received_on=received_on) == MealMonth(month, year, "guessed")


# ── ③ 원문에 연도가 없고 앞뒤 한 달 밖이다 — 정하지 않는다 ──


@pytest.mark.parametrize(
    ("received_on", "month"),
    [
        (date(2026, 11, 11), 3),  # 공지 규칙(60일)이면 "내년 3월" 이 된다
        (date(2026, 11, 11), 9),  # 두 달 전
        (date(2026, 11, 11), 1),  # 두 달 뒤
        (date(2026, 11, 1), 9),  # 달의 첫날에도 두 달 전은 밖이다
        (date(2026, 12, 31), 2),
        (date(2026, 1, 1), 11),
    ],
)
def test_앞뒤_한_달_밖이면_연도를_정하지_않는다(received_on, month) -> None:
    assert _decide([str(month)], received_on=received_on) == MealMonth(month, None, "unknown")


def test_모델이_연도를_냈어도_원문에_없으면_정하지_않는다() -> None:
    assert _decide(["2026-03"], "3월 식단") == MealMonth(3, None, "unknown")


# ── ④ 원문의 연도가 모델과 다르거나 여러 개다 ──


def test_원문의_연도가_모델과_다르면_원문의_연도를_쓰고_알린다() -> None:
    # 원문은 사진 글자다. 모델 연도는 지어낼 수 있다 — 그래도 둘이 어긋났으니 화면에 알린다
    assert _decide(["2024-01"], "2025년 1월 식단") == MealMonth(1, 2025, "written_other")


def test_원문에_그_달의_연도가_여럿이면_받은_날에_가까운_것을_쓰고_알린다() -> None:
    got = _decide(["1"], "2025년 1월 식단 (2023년 1월 개정 기준)")

    assert got == MealMonth(1, 2025, "written_other")


def test_원문의_연도들이_받은_날에서_똑같이_멀면_정하지_않는다() -> None:
    # 2026-07 에 받은 1월 급식표, 원문에 2026년 1월과 2027년 1월 — 6개월 앞뒤다
    got = _decide(["1"], "2026년 1월 · 2027년 1월", received_on=date(2026, 7, 1))

    assert got == MealMonth(1, None, "unknown")


# ── 월 ──


def test_두_호출의_월이_다르면_정하지_않는다() -> None:
    # 어느 쪽이 맞는지 모른다 — 한쪽 월로 다른 쪽 날짜를 조용히 옮기지 않는다
    assert _decide(["11", "12"]) is None


def test_한_호출만_월을_읽었으면_그_월을_쓴다() -> None:
    assert _decide(["급식", "11"]) == MealMonth(11, 2026, "guessed")


def test_월을_하나도_못_읽으면_정하지_않는다() -> None:
    assert _decide(["급식", ""]) is None


def test_같은_달을_다른_모양으로_적어도_같은_월이다() -> None:
    assert _decide(["11", "2026-11"], "2026년 11월") == MealMonth(11, 2026, "written")
