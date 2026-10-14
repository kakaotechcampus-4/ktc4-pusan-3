"""Food Agent 진입점 — 닫힌 경로와 영양소 분석(1차 출력)을 run() 끝에서 끝까지 돈다.

영양소 분석은 모델을 부르지 않는다 — 판정 결과의 식품군 출현을 코드 문장 틀로 낸다
(`영양소_계산_설계.md` §9 N-17 · N-18).
"""

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import DomainTask
from app.agents.food.agent import run
from app.agents.food.context import FoodContext
from app.agents.food.readouts import NUTRIENT_INSUFFICIENT, READOUTS
from app.agents.food.store import (
    FoodObservation,
    InMemoryConsent,
    InMemoryFoodMemory,
    InMemoryMenuCatalog,
    InMemoryProfile,
    InMemorySafety,
    MenuCatalogRow,
    SafetyEntry,
    in_memory_ports,
    unwired_ports,
)

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=KST)
TODDLER_BIRTH = date(2024, 6, 2)  # NOW 기준 28개월
INFANT_BIRTH = date(2026, 2, 2)  # NOW 기준 8개월 — infant_weaning
WINDOW_END = date(2026, 10, 1)  # 오늘(10-02) 이전 하루


class FakeLLM:
    """정해 둔 응답을 순서대로 돌려준다. 다 쓴 뒤에 또 부르면 실패한다."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        if not self._queue:
            raise AssertionError("응답을 다 썼는데 모델을 또 불렀다 — 모델 0회가 맞다")
        return self._queue.pop(0)


def _context(birth_date: date, **port_overrides: object) -> FoodContext:
    defaults: dict[str, object] = {"profile": InMemoryProfile({CHILD: birth_date})}
    defaults.update(port_overrides)
    return FoodContext(
        child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=in_memory_ports(**defaults)
    )


def _menu(name: str, groups: set[str], *, serving_g: float | None = 100.0) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=name,
        display_name=name,
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=serving_g,
        nutrients={
            "energy": 100.0,
            "protein": 3.0,
            "fat": 1.0,
            "carbohydrate": 15.0,
            "calcium": 20.0,
        },
        food_groups=frozenset(groups),
    )


def _meals(*pairs: tuple[str, int]) -> list[FoodObservation]:
    """(메뉴, 어제부터 며칠 전) 마다 기록 한 건."""
    return [
        FoodObservation(
            id=uuid4(),
            child_id=CHILD,
            observed_on=WINDOW_END - timedelta(days=offset),
            subject=name,
            amount_text="한 그릇",
            polarity=1,
            updated_at=NOW,
            confidence_source="parent_direct",
        )
        for name, offset in pairs
    ]


def _records_context(
    *, extra: tuple[tuple[str, int], ...] = (), serving_g: float | None = 100.0, **ports: object
) -> FoodContext:
    """7일 · 매일 쌀밥 두 번 + 채소가 나온 날(어제 · 2일 전 · 4일 전) — 기록 21행."""
    meals = [("쌀밥", day) for day in range(7) for _ in range(2)]
    meals += [("시금치나물", day) for day in (0, 2, 4)]
    meals += list(extra)
    return _context(
        TODDLER_BIRTH,
        memory=InMemoryFoodMemory(observations={CHILD: _meals(*meals)}),
        catalog=InMemoryMenuCatalog(
            [
                _menu("쌀밥", {"grains"}, serving_g=serving_g),
                _menu("시금치나물", {"vegetables"}, serving_g=serving_g),
                _menu("우유", {"dairy"}, serving_g=serving_g),
            ]
        ),
        **ports,
    )


@pytest.fixture
def task() -> DomainTask:
    return DomainTask(
        run_id="run-1",
        agent="food",
        task_type="nutrient_analysis",
        request_texts=("요즘 영양은 괜찮은지 봐줘",),
    )


@pytest.fixture
def context_infant() -> FoodContext:
    return _context(INFANT_BIRTH)


@pytest.fixture
def context_unwired() -> FoodContext:
    ports = unwired_ports(profile=InMemoryProfile({CHILD: TODDLER_BIRTH}))
    return FoodContext(child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=ports)


@pytest.fixture
def context() -> FoodContext:
    return _context(TODDLER_BIRTH)


@pytest.fixture
def counting_client() -> FakeLLM:
    return FakeLLM()  # 큐가 비어 있다 — 한 번이라도 부르면 AssertionError


async def test_닫힌_경로는_모델을_0회_부른다(task, context_infant, counting_client) -> None:
    result = await run(task, context_infant, client=counting_client)

    assert result.status == "closed"
    assert result.model_calls == 0
    assert result.readouts[0].body == READOUTS.get("unsupported.infant_nutrient").template


async def test_안전_조회_실패는_blocked_safety_로_닫는다(
    task, context_unwired, counting_client
) -> None:
    # 안전 구멍 ②③ — unwired_ports() 는 조회를 실패시킨다
    result = await run(task, context_unwired, client=counting_client)

    assert result.model_calls == 0
    assert result.readouts[0].body == READOUTS.get("blocked.safety").template


# ── 영양소 분석 1차 출력 — 모델 0회 · 식품군 출현 문장 (N-17 · N-18) ────────
async def test_영양소_분석은_모델을_부르지_않고_식품군_출현을_코드_문장으로_낸다(
    task, counting_client
) -> None:
    result = await run(task, _records_context(), client=counting_client)

    assert result.status == "ok"
    assert result.model_calls == 0
    assert result.tools == ()
    (readout,) = result.readouts
    assert readout.authored_by == "code"
    assert "기록된 7일 중 채소가 나온 날은 3일이에요." in readout.body.splitlines()
    assert "기록된 7일 중 곡류가 나온 날은 7일이에요." in readout.body.splitlines()


async def test_구간은_판정해_두지만_구간_문장은_화면에_안_나간다(task, counting_client) -> None:
    context = _records_context()

    result = await run(task, context, client=counting_client)

    verdict = context.state.nutrient_verdict
    assert verdict is not None and verdict.bands  # 구간은 만든다
    body = result.readouts[0].body
    assert "적은 편" not in body and "많은 음식" not in body
    assert "칼슘" not in body and "나트륨" not in body


async def test_1인분_중량이_빈_메뉴가_많아도_출현은_말한다(task, counting_client) -> None:
    # 구간은 중량이 없어 못 내도(insufficient) 출현은 "나왔는가" 라서 센다
    context = _records_context(serving_g=None)

    result = await run(task, context, client=counting_client)

    verdict = context.state.nutrient_verdict
    assert verdict is not None and verdict.insufficient is True
    assert "기록된 7일 중 채소가 나온 날은 3일이에요." in result.readouts[0].body.splitlines()


async def test_기록이_부족하면_식품군_출현을_말하지_않는다(task, context, counting_client) -> None:
    result = await run(task, context, client=counting_client)  # 기록 0건

    assert result.status == "ok"
    assert result.model_calls == 0
    assert result.readouts == (READOUTS.render(NUTRIENT_INSUFFICIENT, recorded=0),)


async def test_알레르기_식품군은_출현_문장에서_빠진다(task, counting_client) -> None:
    # 🚨 우유 알레르기 아이에게 "우유·유제품이 나온 날은 0일" 을 말하지 않는다 — 판정은 코드가 한다
    context = _records_context(
        consent=InMemoryConsent({CHILD: True}),
        safety=InMemorySafety([SafetyEntry(kind="allergy", label="우유", status="active")]),
    )

    result = await run(task, context, client=counting_client)

    body = result.readouts[0].body
    assert "우유" not in body and "유제품" not in body
    assert "채소가" in body  # 다른 식품군은 그대로 말한다


async def test_식품군을_모르는_메뉴뿐이면_0일이라고_말하지_않는다(task, counting_client) -> None:
    # 실제 카탈로그의 음식 행(찌개 · 나물)은 식품군이 빈다 — 안 나온 게 아니라 모르는 것이다
    context = _context(
        TODDLER_BIRTH,
        memory=InMemoryFoodMemory(
            observations={CHILD: _meals(*[("된장찌개", day) for day in range(7) for _ in range(2)])}
        ),
        catalog=InMemoryMenuCatalog([_menu("된장찌개", set())]),
    )

    result = await run(task, context, client=counting_client)

    assert "0일" not in result.readouts[0].body
    assert result.readouts == (READOUTS.render("nutrient.unscored", recorded=14),)
