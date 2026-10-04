"""Gate 로 Food tool 을 여닫는 registry 검증.

- `tools_for` — `docs/agents/food/Food_Tool_명세.md` §1 게이팅 표의 칸마다 한 케이스.
  경계 월령(3/4 · 11/12) 양쪽을 덮는다.
- `closed_readout_key` — 닫힌 경로가 정확한 코드 문구 키를 돌려주는지.
- `build_gate` — 동의가 없으면 `health_safety` 를 아예 읽지 않는지, 동의가 없어도
  세 라벨이 모두 열리는지.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.agents.common.gate import DataReady, Gate
from app.agents.food.context import FoodContext, build_gate
from app.agents.food.registry import closed_readout_key, tools_for
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store import (
    DaycareMealRow,
    InMemoryConsent,
    InMemoryDaycareMeals,
    InMemoryProfile,
    InMemorySafety,
    SafetyEntry,
    in_memory_ports,
)
from app.rules.age import LifeStage, stage_of

CHILD = UUID(int=1)
TODAY = date(2026, 9, 27)
NOW = datetime(2026, 9, 27, tzinfo=UTC)

_DAYCARE_BUNDLE = ("lookup_daycare_menu", "update_daycare_meal", "delete_daycare_meal")
_WEANING_MEAL_BUNDLE = ("search_food_memory", "guide_weaning_stage", "propose_meal_candidates")
_TODDLER_MEAL_WITH_DAYCARE = (
    "search_food_memory",
    "analyze_meal_records",
    "check_repeated_menus",
    "lookup_daycare_menu",
    "propose_meal_candidates",
)
_TODDLER_MEAL_NO_DAYCARE = (
    "search_food_memory",
    "analyze_meal_records",
    "check_repeated_menus",
    "propose_meal_candidates",
)
_TODDLER_NUTRIENT_WITH_DAYCARE = (
    "search_food_memory",
    "analyze_meal_records",
    "check_repeated_menus",
    "lookup_daycare_menu",
    "lookup_nutrition",
    "compare_diet_balance",
    "report_nutrient_analysis",
)
_TODDLER_NUTRIENT_NO_DAYCARE = (
    "search_food_memory",
    "analyze_meal_records",
    "check_repeated_menus",
    "lookup_nutrition",
    "compare_diet_balance",
    "report_nutrient_analysis",
)


def _gate(
    months: int, *, daycare: bool = True, safety_ok: bool = True, consent: bool = True
) -> Gate:
    stage = LifeStage(
        months=months, stage=stage_of(months), big="infant" if months < 12 else "toddler"
    )
    return Gate(
        stage=stage,
        consent_child_health=consent,
        safety_ok=safety_ok,
        data=DataReady(daycare_meal=daycare),
    )


# ── tools_for — 게이팅 표 칸마다 한 케이스 ──────────────────────────
@pytest.mark.parametrize(
    ("months", "task", "daycare", "expected"),
    [
        # 0–3개월(infant_milk) — meal_recommendation·nutrient_analysis 닫힘
        (0, FoodTaskType.MEAL_RECOMMENDATION, True, ()),
        (3, FoodTaskType.MEAL_RECOMMENDATION, False, ()),
        (3, FoodTaskType.NUTRIENT_ANALYSIS, True, ()),
        # 4–11개월(infant_weaning) — meal_recommendation 은 고정 3개, daycare 유무와 무관
        (4, FoodTaskType.MEAL_RECOMMENDATION, False, _WEANING_MEAL_BUNDLE),
        (4, FoodTaskType.MEAL_RECOMMENDATION, True, _WEANING_MEAL_BUNDLE),
        (11, FoodTaskType.MEAL_RECOMMENDATION, True, _WEANING_MEAL_BUNDLE),
        (11, FoodTaskType.NUTRIENT_ANALYSIS, True, ()),
        # 12개월+ — 급식 행 있음
        (12, FoodTaskType.MEAL_RECOMMENDATION, True, _TODDLER_MEAL_WITH_DAYCARE),
        (12, FoodTaskType.NUTRIENT_ANALYSIS, True, _TODDLER_NUTRIENT_WITH_DAYCARE),
        # 12개월+ — 급식 행 없음(daycare 빠진 묶음)
        (12, FoodTaskType.MEAL_RECOMMENDATION, False, _TODDLER_MEAL_NO_DAYCARE),
        (12, FoodTaskType.NUTRIENT_ANALYSIS, False, _TODDLER_NUTRIENT_NO_DAYCARE),
        # daycare_meal — 단계로 닫지 않는다. 급식 행 유무로만 갈린다
        (3, FoodTaskType.DAYCARE_MEAL, True, _DAYCARE_BUNDLE),
        (4, FoodTaskType.DAYCARE_MEAL, True, _DAYCARE_BUNDLE),
        (11, FoodTaskType.DAYCARE_MEAL, False, ()),
        (12, FoodTaskType.DAYCARE_MEAL, True, _DAYCARE_BUNDLE),
        (48, FoodTaskType.DAYCARE_MEAL, False, ()),
    ],
)
def test_tools_for(
    months: int, task: FoodTaskType, daycare: bool, expected: tuple[str, ...]
) -> None:
    assert tools_for(task, _gate(months, daycare=daycare)) == expected


# ── closed_readout_key — 닫힌 이유의 코드 문구 키 ────────────────────
def test_milk_meal_은_unsupported_milk_meal() -> None:
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, _gate(2)) == "unsupported.milk_meal"


@pytest.mark.parametrize("months", [3, 6, 11])
def test_영아기_영양소_분석은_unsupported_infant_nutrient(months: int) -> None:
    assert (
        closed_readout_key(FoodTaskType.NUTRIENT_ANALYSIS, _gate(months))
        == "unsupported.infant_nutrient"
    )


def test_급식_행_없으면_closed_no_daycare() -> None:
    assert (
        closed_readout_key(FoodTaskType.DAYCARE_MEAL, _gate(24, daycare=False))
        == "closed.no_daycare"
    )


def test_열린_조합은_closed_readout_key_가_None() -> None:
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, _gate(24)) is None
    assert closed_readout_key(FoodTaskType.NUTRIENT_ANALYSIS, _gate(24)) is None
    assert closed_readout_key(FoodTaskType.DAYCARE_MEAL, _gate(24)) is None


# ── safety_ok=False — 식단 추천만 닫힌다 ────────────────────────────
def test_안전_조회_실패는_식단_추천만_닫는다() -> None:
    gate = _gate(24, safety_ok=False)

    assert tools_for(FoodTaskType.MEAL_RECOMMENDATION, gate) == ()
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, gate) == "blocked.safety"
    # 영양소 분석·급식 갱신은 안전 조회와 무관하게 그대로 연다
    assert tools_for(FoodTaskType.NUTRIENT_ANALYSIS, gate) == _TODDLER_NUTRIENT_WITH_DAYCARE
    assert tools_for(FoodTaskType.DAYCARE_MEAL, gate) == _DAYCARE_BUNDLE


def test_0에서_3개월은_안전_조회_실패여도_milk_meal이_우선한다() -> None:
    # 단계로 닫힌 것과 안전 조회 실패가 같이 오면 단계 사유가 먼저다
    gate = _gate(1, safety_ok=False)
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, gate) == "unsupported.milk_meal"


# ── build_gate — 동의 없어도 안 닫히고, health_safety 도 안 읽는다 ────
def _context(*, ports) -> FoodContext:
    return FoodContext(child_id=CHILD, run_id="run-1", now=NOW, timezone=UTC, ports=ports)


class _CountingSafety:
    """호출 횟수를 세는 SafetyReader 스파이. build_gate 가 동의 없을 때 건너뛰는지 본다."""

    def __init__(self, inner: InMemorySafety) -> None:
        self._inner = inner
        self.calls = 0

    async def food_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        self.calls += 1
        return await self._inner.food_safety(child_id=child_id)


async def test_build_gate_는_동의_없는_아이의_health_safety_를_읽지_않는다() -> None:
    spy = _CountingSafety(InMemorySafety())
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: date(2024, 9, 27)}),  # 24개월 — toddler
        consent=InMemoryConsent({CHILD: False}),
        safety=spy,
        daycare=InMemoryDaycareMeals(
            [
                DaycareMealRow(
                    id=UUID(int=900),
                    child_id=CHILD,
                    serve_date=TODAY,
                    meal_slot="lunch",
                    menu_keys=("test",),
                )
            ]
        ),
    )

    gate = await build_gate(_context(ports=ports))

    assert spy.calls == 0
    assert gate.allergy_states == ()
    assert gate.safety_ok is True  # 동의가 없는 건 조회 실패가 아니다
    # 동의가 없어도 세 라벨 모두 열린다
    assert tools_for(FoodTaskType.MEAL_RECOMMENDATION, gate) != ()
    assert tools_for(FoodTaskType.NUTRIENT_ANALYSIS, gate) != ()
    assert tools_for(FoodTaskType.DAYCARE_MEAL, gate) != ()
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, gate) is None
    assert closed_readout_key(FoodTaskType.NUTRIENT_ANALYSIS, gate) is None
    assert closed_readout_key(FoodTaskType.DAYCARE_MEAL, gate) is None


async def test_build_gate_는_안전_조회_실패를_safety_ok_False_로_옮긴다() -> None:
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: date(2024, 9, 27)}),
        consent=InMemoryConsent({CHILD: True}),
        safety=InMemorySafety(fail=True),
    )

    gate = await build_gate(_context(ports=ports))

    assert gate.safety_ok is False
    assert tools_for(FoodTaskType.MEAL_RECOMMENDATION, gate) == ()
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, gate) == "blocked.safety"


async def test_build_gate_는_daycare_meal_행_유무를_그대로_옮긴다() -> None:
    ports_without_rows = in_memory_ports(profile=InMemoryProfile({CHILD: date(2024, 9, 27)}))
    gate = await build_gate(_context(ports=ports_without_rows))

    assert gate.data.daycare_meal is False
    assert closed_readout_key(FoodTaskType.DAYCARE_MEAL, gate) == "closed.no_daycare"
