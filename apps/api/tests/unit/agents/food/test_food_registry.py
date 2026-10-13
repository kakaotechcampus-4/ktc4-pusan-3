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

from app.agents.common.age import stage_of
from app.agents.common.gate import DataReady, Gate
from app.agents.food import registry
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
from app.rules.age import LifeStage

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
# 급식 행 유무와 무관하다 — 기록 조회 tool 은 스텁이라 열지 않는다
_TODDLER_NUTRIENT = ("lookup_nutrition", "compare_diet_balance", "report_nutrient_analysis")


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


@pytest.fixture
def not_ready_lifted(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool 이 구현돼 `registry.NOT_READY` 에서 뺀 뒤의 게이팅. 게이팅 표의 묶음이 그대로 열리는지
    본다.

    식단 추천 · 급식은 지금 스텁이라 닫혀 있지만, 그 묶음은 `tools_for` 에 남아 있다 — 구현 때
    `NOT_READY` 에서 빼기만 하면 열려야 하고, 그 사이에 표와 어긋나면 안 된다 (#357 리뷰 4-②).
    """
    monkeypatch.setattr(registry, "NOT_READY", {})


# ── tools_for — 지금 모델에게 열리는 tool ───────────────────────────
@pytest.mark.parametrize(
    ("months", "task", "daycare", "expected"),
    [
        # 0–3개월(infant_milk) — meal_recommendation·nutrient_analysis 닫힘
        (0, FoodTaskType.MEAL_RECOMMENDATION, True, ()),
        (3, FoodTaskType.MEAL_RECOMMENDATION, False, ()),
        (3, FoodTaskType.NUTRIENT_ANALYSIS, True, ()),
        # 식단 추천은 run() 이 결과를 옮기기 전이라 not_ready.meal 로 닫힌다 — 단계 · 급식 행과 무관
        (4, FoodTaskType.MEAL_RECOMMENDATION, False, ()),
        (4, FoodTaskType.MEAL_RECOMMENDATION, True, ()),
        (11, FoodTaskType.MEAL_RECOMMENDATION, True, ()),
        (12, FoodTaskType.MEAL_RECOMMENDATION, True, ()),
        (12, FoodTaskType.MEAL_RECOMMENDATION, False, ()),
        # 영양소 분석 — 0–11개월은 닫히고, 12개월+ 는 급식 행 유무와 무관하게 같은 묶음
        (11, FoodTaskType.NUTRIENT_ANALYSIS, True, ()),
        (12, FoodTaskType.NUTRIENT_ANALYSIS, True, _TODDLER_NUTRIENT),
        (12, FoodTaskType.NUTRIENT_ANALYSIS, False, _TODDLER_NUTRIENT),
        # 급식은 run() 이 결과를 옮기기 전이라 행이 있어도 not_ready.daycare 로 닫힌다
        (3, FoodTaskType.DAYCARE_MEAL, True, ()),
        (4, FoodTaskType.DAYCARE_MEAL, True, ()),
        (11, FoodTaskType.DAYCARE_MEAL, False, ()),
        (12, FoodTaskType.DAYCARE_MEAL, True, ()),
        (48, FoodTaskType.DAYCARE_MEAL, False, ()),
    ],
)
def test_tools_for(
    months: int, task: FoodTaskType, daycare: bool, expected: tuple[str, ...]
) -> None:
    assert tools_for(task, _gate(months, daycare=daycare)) == expected


# ── tools_for — not_ready 를 풀었을 때 — 게이팅 표 칸마다 한 케이스 ───
@pytest.mark.usefixtures("not_ready_lifted")
@pytest.mark.parametrize(
    ("months", "task", "daycare", "expected"),
    [
        # 4–11개월(infant_weaning) — meal_recommendation 은 고정 3개, daycare 유무와 무관
        (4, FoodTaskType.MEAL_RECOMMENDATION, False, _WEANING_MEAL_BUNDLE),
        (4, FoodTaskType.MEAL_RECOMMENDATION, True, _WEANING_MEAL_BUNDLE),
        (11, FoodTaskType.MEAL_RECOMMENDATION, True, _WEANING_MEAL_BUNDLE),
        # 12개월+ — 급식 행 있음 · 없음(daycare 빠진 묶음)
        (12, FoodTaskType.MEAL_RECOMMENDATION, True, _TODDLER_MEAL_WITH_DAYCARE),
        (12, FoodTaskType.MEAL_RECOMMENDATION, False, _TODDLER_MEAL_NO_DAYCARE),
        # daycare_meal — 단계로 닫지 않는다. 급식 행 유무로만 갈린다
        (3, FoodTaskType.DAYCARE_MEAL, True, _DAYCARE_BUNDLE),
        (4, FoodTaskType.DAYCARE_MEAL, True, _DAYCARE_BUNDLE),
        (11, FoodTaskType.DAYCARE_MEAL, False, ()),
        (12, FoodTaskType.DAYCARE_MEAL, True, _DAYCARE_BUNDLE),
        (48, FoodTaskType.DAYCARE_MEAL, False, ()),
    ],
)
def test_tools_for_not_ready_를_풀면(
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


@pytest.mark.parametrize("months", [4, 11, 12, 48])
def test_식단_추천은_tool_이_붙기_전까지_not_ready_meal(months: int) -> None:
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, _gate(months)) == "not_ready.meal"


@pytest.mark.parametrize("months", [3, 12, 48])
def test_급식_행이_있어도_tool_이_붙기_전까지_not_ready_daycare(months: int) -> None:
    gate = _gate(months, daycare=True)
    assert closed_readout_key(FoodTaskType.DAYCARE_MEAL, gate) == "not_ready.daycare"


def test_열린_조합은_closed_readout_key_가_None() -> None:
    # 지금 모델에게 열리는 건 영양소 분석(12개월+)뿐이다 — 나머지는 not_ready.* 로 닫혀 있다
    assert closed_readout_key(FoodTaskType.NUTRIENT_ANALYSIS, _gate(24)) is None


# ── safety_ok=False — 식단 추천 · 영양소 분석이 닫힌다 ───────────────
def test_안전_조회_실패는_식단_추천과_영양소_분석을_닫는다() -> None:
    gate = _gate(24, safety_ok=False)

    assert tools_for(FoodTaskType.MEAL_RECOMMENDATION, gate) == ()
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, gate) == "blocked.safety"
    # 영양소 분석도 식품 안내("달걀 반찬 늘리기")를 내서, 걸러 낼 알레르기를 모르면 열지 않는다
    assert tools_for(FoodTaskType.NUTRIENT_ANALYSIS, gate) == ()
    assert closed_readout_key(FoodTaskType.NUTRIENT_ANALYSIS, gate) == "blocked.safety"
    # 급식 갱신은 식품 안내가 아니라서 안전 조회와 무관하다 — blocked.safety 가 아니라 tool 이 붙기
    # 전이라 not_ready.daycare 로 닫힌다
    assert tools_for(FoodTaskType.DAYCARE_MEAL, gate) == ()
    assert closed_readout_key(FoodTaskType.DAYCARE_MEAL, gate) == "not_ready.daycare"


@pytest.mark.usefixtures("not_ready_lifted")
def test_not_ready_를_풀어도_급식_갱신은_안전_조회와_무관하다() -> None:
    gate = _gate(24, safety_ok=False)
    assert tools_for(FoodTaskType.DAYCARE_MEAL, gate) == _DAYCARE_BUNDLE


def test_0에서_3개월은_안전_조회_실패여도_milk_meal이_우선한다() -> None:
    # 단계로 닫힌 것과 안전 조회 실패가 같이 오면 단계 사유가 먼저다
    gate = _gate(1, safety_ok=False)
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, gate) == "unsupported.milk_meal"


@pytest.mark.parametrize("months", [3, 6, 11])
def test_영아기_영양소_분석은_안전_조회_실패여도_영아기_사유가_우선한다(months: int) -> None:
    gate = _gate(months, safety_ok=False)
    assert closed_readout_key(FoodTaskType.NUTRIENT_ANALYSIS, gate) == "unsupported.infant_nutrient"


def test_동의가_없어_안전_정보를_읽지_않은_아이의_영양소_분석은_닫지_않는다() -> None:
    # 동의 없음은 조회 실패가 아니다(safety_ok=True) — 세 라벨 모두 그대로 열린다
    gate = _gate(24, consent=False)
    assert closed_readout_key(FoodTaskType.NUTRIENT_ANALYSIS, gate) is None


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


@pytest.mark.usefixtures("not_ready_lifted")
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


# build_gate: 읽은 안전 정보를 task state 에 남긴다
_MILK = SafetyEntry(kind="allergy", label="우유", status="active")


async def test_build_gate_는_읽은_안전_정보를_state_에_담는다() -> None:
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: date(2024, 9, 27)}),
        consent=InMemoryConsent({CHILD: True}),
        safety=InMemorySafety([_MILK]),
    )
    context = _context(ports=ports)

    await build_gate(context)

    assert context.state.safety == (_MILK,)


async def test_build_gate_는_안전_정보를_읽어도_allergy_states_를_채우지_않는다() -> None:
    # 확인 안 된 알레르기(unknown)는 Food 가 세지 않는다. 행이 없는 것이 unknown 이라 이 칸으로는
    # 셀 수 없고, 안내는 추천을 승인할 때 suggestion.allergens 와 health_safety 를 대조해서 한다
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: date(2024, 9, 27)}),
        consent=InMemoryConsent({CHILD: True}),
        safety=InMemorySafety([_MILK]),
    )

    gate = await build_gate(_context(ports=ports))

    assert gate.allergy_states == ()
    assert gate.allergy_unconfirmed is False


async def test_build_gate_는_동의가_없으면_state_에_빈_안전_정보를_담는다() -> None:
    # 읽지 않았으니 게이트의 allergy_states가 빈 것과 같은 뜻
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: date(2024, 9, 27)}),
        consent=InMemoryConsent({CHILD: False}),
        safety=InMemorySafety([_MILK]),
    )
    context = _context(ports=ports)

    await build_gate(context)

    assert context.state.safety == ()


async def test_build_gate_는_조회에_실패하면_state_안전_정보를_None_으로_둔다() -> None:
    # None은 "확인 못 함"이다. 빈 튜플(제한 없음)로 대신하지 않는다
    # (행의 상태 필드의 None 의미(보호자가 없다고 답함)와는 다름)
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: date(2024, 9, 27)}),
        consent=InMemoryConsent({CHILD: True}),
        safety=InMemorySafety(fail=True),
    )
    context = _context(ports=ports)

    await build_gate(context)

    assert context.state.safety is None
