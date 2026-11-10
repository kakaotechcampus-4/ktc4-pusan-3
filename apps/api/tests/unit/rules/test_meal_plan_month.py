"""급식표의 연도 · 월 — 코드가 정한다. 모델에게 묻지 않는다 (루트 CLAUDE.md §2 · §3).

급식표 제목에는 연도가 없는 일이 많다("[11]월 급식계획"). 그때 모델이 연도를 지어내면 날짜 전부가
틀리거나(범위 안이면 조용히 저장된다), 범위를 벗어나 저장 전체가 막혔다(2026-11-11 운영 장애).
월은 모델이 읽은 것을 받되, 연도는 사진의 글자에 적혀 있을 때만 믿고 아니면 받은 날로 정한다.
"""

from datetime import date

import pytest

from app.rules.meal_plan_month import decide_year_month, parse_model_month

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


def test_연도가_없는_급식표는_받은_날로_연도를_정한다() -> None:
    # 운영 장애 사진 — 모델이 "11" 만 냈다. 형식 검사로 표 전체를 버리지 않는다
    assert decide_year_month(["11", "11"], transcript="[11]월 급식계획", received_on=RECEIVED) == (
        2026,
        11,
    )


def test_원문에_없는_연도는_모델이_지어낸_것이라_버린다() -> None:
    # 범위 밖 연도면 저장 전체가 400, 범위 안이면 틀린 날짜로 조용히 저장된다
    got = decide_year_month(
        ["2022-11", "2022-11"], transcript="[11]월 급식계획 총 471 kcal", received_on=RECEIVED
    )

    assert got == (2026, 11)


def test_원문에_연도가_적혀_있으면_그_연도를_믿는다() -> None:
    # 저장소 정답셋 급식표(2025-01) — 받은 날이 지금이어도 사진에 적힌 연도가 맞다
    got = decide_year_month(
        ["2025-01", "2025-01"], transcript="2025년 1월 영양식단", received_on=RECEIVED
    )

    assert got == (2025, 1)


@pytest.mark.parametrize(
    "transcript",
    [
        "2025.01 식단",
        "2025-1월 식단",
        "2025년 [1]월 식단",
        "25년 1월 식단",
    ],
)
def test_연도는_월과_붙어_적혀_있으면_믿는다(transcript: str) -> None:
    assert decide_year_month(["2025-01"], transcript=transcript, received_on=RECEIVED) == (2025, 1)


@pytest.mark.parametrize(
    "transcript",
    [
        # 학년도는 연도가 아니다 — 2025학년도 1월은 2026년 1월이다
        "2025학년도 1월 식단",
        # 연도 숫자가 따로 있어도 그 달과 붙어 있지 않으면 근거가 아니다
        "2025 개정 영양기준 · 1월 식단",
        "2025년 11월 식단",
    ],
)
def test_월과_붙지_않은_연도는_믿지_않는다(transcript: str) -> None:
    assert decide_year_month(["2025-01"], transcript=transcript, received_on=RECEIVED) == (2027, 1)


def test_12월에_받은_1월_급식표는_다음_해_1월이다() -> None:
    got = decide_year_month(["1"], transcript="1월 식단", received_on=date(2026, 12, 20))

    assert got == (2027, 1)


def test_1월에_받은_12월_급식표는_지난해_12월이다() -> None:
    # 공지 날짜 규칙(`notice_dates._nearest`)은 지난해를 보지 않아 2026-12 가 된다 — 11개월 뒤다
    got = decide_year_month(["12"], transcript="12월 식단", received_on=date(2026, 1, 5))

    assert got == (2025, 12)


@pytest.mark.parametrize(
    ("month", "expected"),
    [
        (11, (2026, 11)),
        (12, (2026, 12)),
        (10, (2026, 10)),
        (6, (2026, 6)),
        (4, (2027, 4)),
    ],
)
def test_받은_날에_가장_가까운_그_달이다(month: int, expected: tuple) -> None:
    assert decide_year_month([str(month)], transcript="", received_on=RECEIVED) == expected


def test_반년_앞뒤가_같으면_지난_달로_본다() -> None:
    # 11월에 받은 5월 급식표 — 반년 뒤의 급식표는 아직 만들어지지 않았다. 지난 것을 올린 것이다
    assert decide_year_month(["5"], transcript="", received_on=RECEIVED) == (2026, 5)


def test_두_호출의_월이_다르면_정하지_않는다() -> None:
    # 어느 쪽이 맞는지 모른다 — 한쪽 월로 다른 쪽 날짜를 조용히 옮기지 않는다
    assert decide_year_month(["11", "12"], transcript="", received_on=RECEIVED) is None


def test_한_호출만_월을_읽었으면_그_월을_쓴다() -> None:
    assert decide_year_month(["급식", "11"], transcript="", received_on=RECEIVED) == (2026, 11)


def test_월을_하나도_못_읽으면_정하지_않는다() -> None:
    assert decide_year_month(["급식", ""], transcript="", received_on=RECEIVED) is None


def test_같은_달을_다른_모양으로_적어도_같은_월이다() -> None:
    assert decide_year_month(["11", "2026-11"], transcript="", received_on=RECEIVED) == (2026, 11)
