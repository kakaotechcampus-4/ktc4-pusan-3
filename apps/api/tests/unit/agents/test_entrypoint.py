"""entrypoint.handle_input() 검증. 컨텍스트를 만들어 pipeline 에 넘기는 데까지만 본다.

pipeline 을 가짜로 바꿔 끼우므로 LLM 호출은 없다.
"""

import calendar
from datetime import date, datetime
from typing import Any, get_args
from uuid import UUID

import pytest

from app.agents import entrypoint, pipeline
from app.agents.food.store import FoodPorts
from app.agents.memory.store import InMemoryStore
from app.rules.age import life_stage

CHILD = UUID(int=1)
PARENT = UUID(int=2)


async def test_컨텍스트를_만들어_pipeline_에_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, food_context: Any, **kwargs: Any) -> str:
        seen.update(raw_text=raw_text, memory=memory_context, food=food_context, **kwargs)
        return "RESULT"

    # import 시점에 이름이 박히므로 pipeline 쪽을 바꿔 끼우면 안 먹는다
    monkeypatch.setattr(entrypoint, "_handle_input", fake)

    events: list[Any] = []

    def emit(event: Any) -> None:
        events.append(event)

    result = await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기 잘 먹었어",
        run_id="run-1",
        emit=emit,
    )

    assert result == "RESULT"
    assert seen["raw_text"] == "딸기 잘 먹었어"
    assert seen["run_id"] == "run-1"
    assert seen["emit"] is emit

    memory, food = seen["memory"], seen["food"]
    assert memory.child_id == CHILD
    assert memory.source_writer == PARENT  # 보호자 발화가 아이 것으로 저장되지 않게
    assert isinstance(memory.store, InMemoryStore)
    assert food.child_id == CHILD
    assert food.run_id == "run-1"
    assert isinstance(food.ports, FoodPorts)
    # 생일을 안 넘기면 fallback 이 toddler·preschool 쪽으로 연다 (§2 를 닫지 않는 값)
    default_birth_date = await food.ports.profile.birth_date(child_id=CHILD)
    assert life_stage(default_birth_date, food.today).stage in {"toddler", "preschool"}
    assert memory.now == food.now  # 기준일이 두 Agent 사이에서 갈리지 않게
    assert memory.timezone is food.timezone is entrypoint.KST
    assert memory.now.tzinfo is entrypoint.KST


def test_이벤트_타입이_전부_밖으로_나간다() -> None:
    """pipeline 에 이벤트를 추가하면 entrypoint 에도 적어야 한다. api 는 이 파일만 본다."""
    exported = {getattr(entrypoint, name) for name in entrypoint.__all__}
    missing = [event.__name__ for event in get_args(pipeline.Event) if event not in exported]
    assert not missing


async def test_넘긴_store_를_그대로_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """api 가 만든 store 에 써야 저장된 행을 api 가 다시 읽을 수 있다."""
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, food_context: Any, **kwargs: Any) -> str:
        seen["memory"] = memory_context
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    store = InMemoryStore()

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기 잘 먹었어",
        run_id="run-1",
        store=store,
    )

    assert seen["memory"].store is store


def _months_ago(months: int) -> date:
    """오늘 기준으로 딱 그만큼 나이 먹은 아이의 생일."""
    today = datetime.now(entrypoint.KST).date()
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, min(today.day, calendar.monthrange(year, month + 1)[1]))


@pytest.mark.parametrize(
    ("months", "expected_stage"),
    [
        (0, "infant_milk"),
        (3, "infant_milk"),
        (4, "infant_weaning"),
        (11, "infant_weaning"),
        (12, "toddler"),
    ],
)
async def test_생일을_주면_월령에서_단계를_고른다(
    monkeypatch: pytest.MonkeyPatch, months: int, expected_stage: str
) -> None:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, food_context: Any, **kwargs: Any) -> str:
        seen["food"] = food_context
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    birth_date = _months_ago(months)

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="오늘 뭐 먹일까",
        run_id="run-1",
        birth_date=birth_date,
    )

    food = seen["food"]
    stored_birth_date = await food.ports.profile.birth_date(child_id=CHILD)
    assert stored_birth_date == birth_date
    assert life_stage(birth_date, food.today).stage == expected_stage
