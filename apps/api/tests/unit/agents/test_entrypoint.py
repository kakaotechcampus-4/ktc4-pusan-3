"""entrypoint.handle_input() 검증. 컨텍스트를 만들어 pipeline 에 넘기는 데까지만 본다.

pipeline 을 가짜로 바꿔 끼우므로 LLM 호출은 없다.
"""

import calendar
import inspect
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, get_args
from uuid import UUID

import pytest

from app.agents import entrypoint, pipeline
from app.agents.activity.context import build_gate as build_activity_gate
from app.agents.activity.registry import closed_readout_key as activity_closed_key
from app.agents.activity.schemas.memory import SearchActivityMemoryArgs
from app.agents.activity.schemas.task import ActivityTaskType
from app.agents.activity.store.inmemory import InMemoryPlaces, InMemoryWeather
from app.agents.activity.tools.memory import search_activity_memory
from app.agents.common.age import life_stage
from app.agents.common.datetime_rules import build_observed_range
from app.agents.common.evidence import AffinityRow
from app.agents.common.llm_client import LLMConfigError
from app.agents.common.ports import SafetyLookupError
from app.agents.curator.embedding import embedder as embedder_module
from app.agents.curator.embedding import jev as jev_module
from app.agents.food.agent import run as food_run
from app.agents.food.context import build_gate
from app.agents.food.readouts import READOUTS
from app.agents.food.registry import closed_readout_key, tools_for
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.records import SearchFoodMemoryArgs
from app.agents.food.store import FoodPorts, MenuCatalogRow
from app.agents.food.tools.records import search_food_memory
from app.agents.memory.schemas.task import WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.memory_bridge import StoreActivityMemory
from app.agents.suggestion_save import InMemorySuggestions
from app.agents.supervisor import routing
from tests.unit.agents.food.support import ScriptedLLM, food_task

CHILD = UUID(int=1)
PARENT = UUID(int=2)


async def _no_commit() -> None:
    """InMemoryStore 라 확정할 것이 없다. commit 은 필수 인자라 아무것도 안 하는 것을 넘긴다."""


async def test_컨텍스트를_만들어_pipeline_에_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(raw_text=raw_text, memory=memory_context, contexts=contexts, **kwargs)
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
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    assert result == "RESULT"
    assert seen["raw_text"] == "딸기 잘 먹었어"
    assert seen["run_id"] == "run-1"
    assert seen["emit"] is emit

    memory, food = seen["memory"], seen["contexts"]["food"]
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
    # 구현된 Agent 마다 context 가 있어야 pipeline 이 찾는다
    assert set(seen["contexts"]) == routing.IMPLEMENTED_AGENTS


def test_이벤트_타입이_전부_밖으로_나간다() -> None:
    """pipeline 에 이벤트를 추가하면 entrypoint 에도 적어야 한다. api 는 이 파일만 본다."""
    exported = {getattr(entrypoint, name) for name in entrypoint.__all__}
    missing = [event.__name__ for event in get_args(pipeline.Event) if event not in exported]
    assert not missing


def test_commit_과_save_suggestions_는_기본값이_없다() -> None:
    """기본값이 있으면 러너가 빠뜨린 호출이 조용히 통과한다 — commit 은 기록이, save_suggestions 는
    추천이 사라진다."""
    for handle in (entrypoint.handle_input, pipeline.handle_input):
        params = inspect.signature(handle).parameters
        for name in ("commit", "save_suggestions"):
            assert params[name].default is inspect.Parameter.empty, name


async def test_넘긴_save_suggestions_를_pipeline_에_그대로_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    saver = InMemorySuggestions()

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기",
        run_id="r",
        commit=_no_commit,
        save_suggestions=saver,
    )

    assert seen["save_suggestions"] is saver


async def test_넘긴_count_records_를_pipeline_에_그대로_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "RESULT"

    async def count(agent: str) -> int:
        return 1

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="놀이 추천해줘",
        run_id="r",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
        count_records=count,
    )

    assert seen["count_records"] is count


async def test_넘긴_store_를_그대로_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """api 가 만든 store 에 써야 저장된 행을 api 가 다시 읽을 수 있다."""
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
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
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
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

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen["food"] = contexts["food"]
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    birth_date = _months_ago(months)

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="오늘 뭐 먹일까",
        run_id="run-1",
        birth_date=birth_date,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    food = seen["food"]
    stored_birth_date = await food.ports.profile.birth_date(child_id=CHILD)
    assert stored_birth_date == birth_date
    assert life_stage(birth_date, food.today).stage == expected_stage


async def test_이어받기_맥락을_pipeline_에_그대로_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(raw_text=raw_text, **kwargs)
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    pending = entrypoint.PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE)

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="3일 전부터",
        run_id="run-2",
        continuation=pending,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    assert seen["raw_text"] == "3일 전부터"
    assert seen["continuation"] is pending


async def test_이어받기가_없으면_none_을_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기",
        run_id="r",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    assert seen["continuation"] is None


async def test_Food_기억_포트는_같은_run_의_Memory_store_를_읽는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """방금 저장한 관찰이 같은 run 의 Food 근거로 잡혀야 한다."""
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(memory=memory_context, food=contexts["food"])
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기 잘 먹었어",
        run_id="run-1",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    memory, food = seen["memory"], seen["food"]
    today = food.today
    await memory.store.create_observation(
        domain="food",
        child_id=CHILD,
        source_writer=PARENT,
        raw_text="딸기 잘 먹었어",
        observed_on=today,
        observed_range=build_observed_range(today),
        fields={"subject": "딸기"},
    )
    rows = await food.ports.memory.observations(child_id=CHILD, date_from=today, date_to=today)
    assert [row.subject for row in rows] == ["딸기"]


async def test_commit_을_pipeline_에_그대로_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """기록 단계를 확정하는 함수는 러너가 만든다. agents 는 받아서 부르기만 한다."""
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)

    async def commit() -> None:
        return None

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기",
        run_id="r",
        commit=commit,
        save_suggestions=InMemorySuggestions(),
    )

    assert seen["commit"] is commit


async def test_포트를_안_넘긴_run_의_Food_는_안전_조회_실패로_닫힌다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "동의 있음 + 알레르기 0행" 으로 열리면 알레르기가 있는 아이에게도 거르지 않은 안내가 나간다.

    안전 어댑터를 안 넘긴 호출(테스트 · eval)에서 Food 는 모델 0회로 닫힌다. 식단 추천 ·
    급식 갱신의 `NOT_READY` 가 비어 있어도 그렇다 — 닫는 것은 안전 조회 실패다.
    """
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen["food"] = contexts["food"]
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기",
        run_id="r",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    gate = await build_gate(seen["food"])
    assert gate.safety_ok is False
    for task in (FoodTaskType.MEAL_RECOMMENDATION, FoodTaskType.NUTRIENT_ANALYSIS):
        assert closed_readout_key(task, gate) == "blocked.safety"
        assert tools_for(task, gate) == ()
        # run() 끝까지 — 모델은 한 번도 불리지 않고 추천 · 문장 없이 blocked.safety 문구만 나간다
        llm = ScriptedLLM()
        result = await food_run(food_task(task.value), seen["food"].for_task(), client=llm)
        assert (result.status, result.model_calls, llm.calls) == ("blocked", 0, 0)
        assert result.suggestions == ()
        assert [readout.body for readout in result.readouts] == [
            READOUTS.get("blocked.safety").template
        ]


async def test_Food_쓰기_포트를_감싸_쓰기_표시를_pipeline_에_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """급식 갱신이 성공하면 같은 run의 표시가 남아야 pipeline이 failed로 끝내지 않는다."""
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(food=contexts["food"], **kwargs)
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="두유",
        run_id="r",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    writes = seen["writes"]
    assert writes.wrote is False
    # 메뉴 카탈로그 저장은 표시를 세우지 않지만 급식 삭제는 세운다
    # (없는 id 는 조용히 넘어간다)
    await seen["food"].ports.catalog.put(
        MenuCatalogRow(
            menu_key="두유",
            display_name="두유",
            source="manual",
            resolved=True,
            synced_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        )
    )
    assert writes.wrote is False
    await seen["food"].ports.daycare.delete(child_id=CHILD, row_ids=(UUID(int=9),))
    assert writes.wrote is True


async def _food_of(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Any:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **_: Any) -> str:
        seen["food"] = contexts["food"]
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기",
        run_id="r",
        **kwargs,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )
    return seen["food"]


@pytest.mark.parametrize("granted", [True, False])
async def test_API_가_읽어_넘긴_생일_동의가_Food_포트로_들어간다(
    monkeypatch: pytest.MonkeyPatch, granted: bool
) -> None:
    """#372 PR 2 — 값 하나를 감싼 포트가 `ChildProfileReader` · `ConsentReader` 자리에 들어간다."""
    birth_date = _months_ago(30)

    food = await _food_of(monkeypatch, birth_date=birth_date, child_health_granted=granted)

    assert await food.ports.profile.birth_date(child_id=CHILD) == birth_date
    assert await food.ports.consent.child_health_granted(child_id=CHILD) is granted


@pytest.mark.parametrize("granted", [True, False])
async def test_동의와_상관없이_Food_는_안전_조회_실패로_닫힌다(
    monkeypatch: pytest.MonkeyPatch, granted: bool
) -> None:
    """🚨 health_safety 어댑터를 안 넘긴 호출(테스트 · eval)은 동의하지 않은 아이도 Food 가
    열리면 안 된다.

    동의가 없으면 build_gate 가 health_safety 를 읽지 않아서, 포트를 그냥 실제 동의 값으로
    바꾸면 동의하지 않은 아이에게는 "알레르기 없는 아이 기준" 으로 추천이 열린다.
    """
    food = await _food_of(monkeypatch, birth_date=_months_ago(30), child_health_granted=granted)

    gate = await build_gate(food)

    assert gate.consent_child_health is granted
    assert gate.safety_ok is False
    for task in (FoodTaskType.MEAL_RECOMMENDATION, FoodTaskType.NUTRIENT_ANALYSIS):
        assert closed_readout_key(task, gate) == "blocked.safety"
        assert tools_for(task, gate) == ()


async def test_동의_값을_못_받았어도_동의_없음으로_바꾸지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None 은 "값을 안 넘긴 호출" 이다 — False(동의 안 함)로 바뀌어 읽히지 않는다."""
    food = await _food_of(monkeypatch)

    assert await food.ports.consent.child_health_granted(child_id=CHILD) is True
    gate = await build_gate(food)
    assert gate.safety_ok is False


async def test_다른_아이의_생일_동의는_받지_못한다(monkeypatch: pytest.MonkeyPatch) -> None:
    food = await _food_of(monkeypatch, birth_date=_months_ago(30), child_health_granted=True)

    with pytest.raises(LookupError):
        await food.ports.profile.birth_date(child_id=UUID(int=99))
    with pytest.raises(LookupError):
        await food.ports.consent.child_health_granted(child_id=UUID(int=99))


async def test_받은_curator_trigger_를_그대로_pipeline_에_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)

    def trigger(child_id: UUID, today: date) -> None:
        pass

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기 잘 먹었어",
        run_id="run-1",
        commit=_no_commit,
        curator_trigger=trigger,
        save_suggestions=InMemorySuggestions(),
    )

    assert seen["curator_trigger"] is trigger


async def test_트리거를_안_넘기면_Curator_를_띄우지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """agents 는 DB 를 열지 않는다.

    테스트 · eval 이 임베딩 키를 가졌어도 실제 DB Curator 가 뜨면 안 된다.
    """
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기 잘 먹었어",
        run_id="run-1",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    assert seen["curator_trigger"] is None


def test_curator_deps_는_키가_없으면_None_이다(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_key(*args: Any, **kwargs: Any) -> Any:
        raise LLMConfigError("키 없음")

    monkeypatch.setattr(embedder_module, "Embedder", no_key)
    monkeypatch.setattr(jev_module, "JevJudge", no_key)
    entrypoint.curator_deps.cache_clear()
    try:
        assert entrypoint.curator_deps() == (None, None)
    finally:
        entrypoint.curator_deps.cache_clear()


async def test_러너가_준_도메인_store_로_Food_기억_포트를_읽는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """도메인 Agent 의 기억 포트는 run 의 store 가 아니라 러너가 준 opener 로 읽는다 (#257)."""
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> str:
        seen["food"] = contexts["food"]
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    domain_store = InMemoryStore()
    opened: list[int] = []

    @asynccontextmanager
    async def opener() -> AsyncIterator[Any]:
        opened.append(1)
        yield domain_store

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="딸기",
        run_id="r",
        commit=_no_commit,
        domain_store=opener,
        save_suggestions=InMemorySuggestions(),
    )
    today = seen["food"].today
    await seen["food"].ports.memory.observations(child_id=CHILD, date_from=today, date_to=today)

    assert opened == [1]


# ── Activity (#380) ─────────────────────────────────────────────
async def _activity_of(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Any:
    seen: dict[str, Any] = {}

    async def fake(raw_text: str, memory_context: Any, contexts: Any, **_: Any) -> str:
        seen.update(contexts=contexts, memory=memory_context)
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_handle_input", fake)
    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="주말에 뭐 하지",
        run_id="r",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
        **kwargs,
    )
    return seen


@pytest.mark.parametrize("granted", [True, False])
async def test_Activity_도_동의와_상관없이_안전_조회_실패로_닫힌다(
    monkeypatch: pytest.MonkeyPatch, granted: bool
) -> None:
    """🚨 어댑터를 안 넘긴 호출(테스트 · eval)은 동의하지 않은 아이도 환경 알레르기 확인 없이
    열리면 안 된다."""
    seen = await _activity_of(monkeypatch, birth_date=_months_ago(30), child_health_granted=granted)
    activity = seen["contexts"]["activity"]

    gate = await build_activity_gate(activity, outdoor_ok=False)

    assert gate.consent_child_health is granted
    assert gate.safety_ok is False
    assert activity_closed_key(ActivityTaskType.ACTIVITY_RECOMMENDATION, gate) == "blocked.safety"


async def test_Activity_는_Food_와_같은_생일_기준일_store_를_쓴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    birth_date = _months_ago(30)
    seen = await _activity_of(monkeypatch, birth_date=birth_date, child_health_granted=True)
    activity, food = seen["contexts"]["activity"], seen["contexts"]["food"]

    assert await activity.ports.profile.birth_date(child_id=CHILD) == birth_date
    assert activity.now == food.now and activity.timezone is food.timezone
    assert isinstance(activity.ports.memory, StoreActivityMemory)
    assert activity.ports.places is None  # 장소 어댑터 전이라 장소 조회가 닫힌다


async def test_좌표와_날씨_어댑터를_넘기면_Activity_가_받는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weather = InMemoryWeather()
    seen = await _activity_of(
        monkeypatch,
        location=entrypoint.CoarseLocation(lat=35.177019, lon=129.076952),
        activity_weather=weather,
    )
    activity = seen["contexts"]["activity"]

    assert activity.ports.weather is weather
    assert (activity.location.lat, activity.location.lon) == (35.18, 129.08)  # 한 번 더 자른다


async def test_장소_어댑터를_넘기면_Activity_가_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    places = InMemoryPlaces()
    seen = await _activity_of(
        monkeypatch,
        location=entrypoint.CoarseLocation(lat=35.18, lon=129.08),
        activity_places=places,
    )
    activity = seen["contexts"]["activity"]

    assert activity.ports.places is places
    gate = await build_activity_gate(activity, outdoor_ok=True)
    assert gate.data.places is True and gate.has_location is True


async def test_좌표를_안_넘기면_Activity_는_실내_전용이다(monkeypatch: pytest.MonkeyPatch) -> None:
    activity = (await _activity_of(monkeypatch))["contexts"]["activity"]
    assert activity.location is None
    assert activity.ports.weather is None


async def test_러너가_준_도메인_store_로_Activity_기억_포트를_읽는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Food 와 같다 — run 세션을 도메인 task 들이 같이 타지 않게 (#257 · #380)."""
    opened: list[int] = []

    @asynccontextmanager
    async def opener() -> AsyncIterator[Any]:
        opened.append(1)
        yield InMemoryStore()

    activity = (await _activity_of(monkeypatch, domain_store=opener))["contexts"]["activity"]
    today = activity.today
    await activity.ports.memory.observations(child_id=CHILD, date_from=today, date_to=today)

    assert opened == [1]


# ── 안전 어댑터 (#460) ─────────────────────────────────────────────
class _Reader:
    """`DbSafetyReader` 자리. 어느 메서드를 불렀는지 남긴다."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def food_safety(self, *, child_id: UUID) -> list:
        self.calls.append("food")
        if self.fail:
            raise SafetyLookupError("못 읽음")
        return []

    async def activity_safety(self, *, child_id: UUID) -> list:
        self.calls.append("activity")
        if self.fail:
            raise SafetyLookupError("못 읽음")
        return []


async def test_안전_어댑터를_넘기면_Food_와_Activity_가_열린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _Reader()
    seen = await _activity_of(
        monkeypatch,
        birth_date=_months_ago(30),
        child_health_granted=True,
        food_safety=reader,
        activity_safety=reader,
    )

    food_gate = await build_gate(seen["contexts"]["food"])
    activity_gate = await build_activity_gate(seen["contexts"]["activity"], outdoor_ok=False)

    assert food_gate.safety_ok is True
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, food_gate) is None
    assert activity_gate.safety_ok is True
    assert activity_closed_key(ActivityTaskType.ACTIVITY_RECOMMENDATION, activity_gate) is None


async def test_Activity_는_activity_safety_로_Food_는_food_safety_로_읽는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 읽는 행이 다르다(Food: active · kind 전부, Activity: allergy · environmental · status 전부)
    reader = _Reader()
    seen = await _activity_of(
        monkeypatch,
        birth_date=_months_ago(30),
        child_health_granted=True,
        food_safety=reader,
        activity_safety=reader,
    )

    await build_gate(seen["contexts"]["food"])
    await build_activity_gate(seen["contexts"]["activity"], outdoor_ok=False)

    assert reader.calls == ["food", "activity"]


async def test_안전_어댑터가_못_읽으면_둘_다_닫힌다(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _Reader(fail=True)
    seen = await _activity_of(
        monkeypatch,
        birth_date=_months_ago(30),
        child_health_granted=True,
        food_safety=reader,
        activity_safety=reader,
    )

    food_gate = await build_gate(seen["contexts"]["food"])
    activity_gate = await build_activity_gate(seen["contexts"]["activity"], outdoor_ok=False)

    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, food_gate) == "blocked.safety"
    assert (
        activity_closed_key(ActivityTaskType.ACTIVITY_RECOMMENDATION, activity_gate)
        == "blocked.safety"
    )


async def test_안전_어댑터를_handle_input_과_suggest_가_둘_다_받는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    original = entrypoint._domain_contexts

    def spy(**kwargs: Any) -> Any:
        seen.append((kwargs["food_safety"], kwargs["activity_safety"]))
        return original(**kwargs)

    async def fake_run(*args: Any, **kwargs: Any) -> Any:
        return pipeline.SuggestRun(outcomes=(), failed=())

    async def fake_handle(*args: Any, **kwargs: Any) -> str:
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_domain_contexts", spy)
    monkeypatch.setattr(pipeline, "run_suggestions", fake_run)
    monkeypatch.setattr(entrypoint, "_handle_input", fake_handle)
    reader = _Reader()
    readers = {"food_safety": reader, "activity_safety": reader}

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="저녁 추천해줘",
        run_id="r",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
        **readers,
    )
    await entrypoint.suggest(
        child_id=CHILD,
        tasks=[],
        request_id="q",
        save_run_id=None,
        sent_at=None,
        save_suggestions=InMemorySuggestions(),
        **readers,
    )

    assert seen == [(reader, reader)] * 2


# ── 관심 어댑터 (#572) ─────────────────────────────────────────────
class _Affinities:
    """`DbAffinityReader` 자리. (도메인, 행) 중 물은 도메인 것만 돌려준다."""

    def __init__(self, *rows: tuple[str, AffinityRow]) -> None:
        self.rows = rows

    async def affinities(self, *, child_id: UUID, domains: Sequence[str]) -> list[AffinityRow]:
        return [row for domain, row in self.rows if domain in domains]


def _liked(n: int, key: str, *, days_ago: int) -> AffinityRow:
    today = datetime.now(entrypoint.KST).date()
    return AffinityRow(
        id=UUID(int=n),
        merge_key=key,
        state="confirmed",
        polarity=1,
        strength=0.9,
        last_observed_on=today - timedelta(days=days_ago),
    )


async def _activity_memory(monkeypatch: pytest.MonkeyPatch, reader: _Affinities) -> Any:
    seen = await _activity_of(monkeypatch, birth_date=_months_ago(30), affinity_reader=reader)
    activity = seen["contexts"]["activity"]
    activity.state.gate = await build_activity_gate(activity, outdoor_ok=False)
    return await search_activity_memory(activity, SearchActivityMemoryArgs())


async def test_관심_어댑터를_넘기면_관찰이_없어도_Activity_기억이_티어_1_근거를_돌려준다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _Affinities(
        ("activity", _liked(9, "공룡", days_ago=3)), ("food", _liked(10, "딸기", days_ago=3))
    )

    result = await _activity_memory(monkeypatch, reader)

    assert [(r["label"], r["tier"]) for r in result.data["records"]] == [("공룡", 1)]


async def test_21일_넘게_관찰이_없는_관심은_근거로_나가지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _Affinities(("activity", _liked(9, "공룡", days_ago=22)))

    result = await _activity_memory(monkeypatch, reader)

    assert not result.success  # NO_RECORDS → 일반 추천


async def test_관심_어댑터를_넘기면_Food_기억은_food_관심만_돌려준다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _Affinities(
        ("activity", _liked(9, "공룡", days_ago=3)), ("food", _liked(10, "딸기", days_ago=3))
    )
    food = (await _activity_of(monkeypatch, affinity_reader=reader))["contexts"]["food"]

    result = await search_food_memory(food, SearchFoodMemoryArgs())

    assert [(r["kind"], r["label"], r["tier"]) for r in result.data["records"]] == [
        ("profile_affinity", "딸기", 1)
    ]


async def test_05_다시_만들기는_입력_run_의_날을_기준일로_읽는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_at = datetime.now(entrypoint.KST) - timedelta(days=22)
    reader = _Affinities(("activity", _liked(9, "공룡", days_ago=25)))  # 그 run 기준 3일
    contexts: list[Any] = []
    original = entrypoint._domain_contexts

    def spy(**kwargs: Any) -> Any:
        made = original(**kwargs)
        contexts.append(made[0])
        return made

    async def fake_run(*args: Any, **kwargs: Any) -> Any:
        return pipeline.SuggestRun(outcomes=(), failed=())

    monkeypatch.setattr(entrypoint, "_domain_contexts", spy)
    monkeypatch.setattr(pipeline, "run_suggestions", fake_run)

    await entrypoint.suggest(
        child_id=CHILD,
        tasks=[],
        request_id="q",
        save_run_id=None,
        sent_at=sent_at,
        save_suggestions=InMemorySuggestions(),
        affinity_reader=reader,
    )
    rows = await contexts[0]["activity"].ports.memory.affinities(child_id=CHILD)

    assert [r.state for r in rows] == ["confirmed"]


async def test_관심_어댑터를_handle_input_과_suggest_가_둘_다_받는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    original = entrypoint._domain_contexts

    def spy(**kwargs: Any) -> Any:
        seen.append(kwargs["affinity_reader"])
        return original(**kwargs)

    async def fake_run(*args: Any, **kwargs: Any) -> Any:
        return pipeline.SuggestRun(outcomes=(), failed=())

    async def fake_handle(*args: Any, **kwargs: Any) -> str:
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_domain_contexts", spy)
    monkeypatch.setattr(pipeline, "run_suggestions", fake_run)
    monkeypatch.setattr(entrypoint, "_handle_input", fake_handle)
    reader = _Affinities()

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="저녁 추천해줘",
        run_id="r",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
        affinity_reader=reader,
    )
    await entrypoint.suggest(
        child_id=CHILD,
        tasks=[],
        request_id="q",
        save_run_id=None,
        sent_at=None,
        save_suggestions=InMemorySuggestions(),
        affinity_reader=reader,
    )

    assert seen == [reader, reader]


# ── Food 카탈로그 · 추천 이력 어댑터와 문서 시드 (#442) ──────────────────────
def _food_ports(**kwargs: Any) -> Any:
    now = datetime.now(timezone.utc)
    contexts, _ = entrypoint._domain_contexts(
        child_id=CHILD,
        run_id="r",
        now=now,
        store=InMemoryStore(now=now),
        birth_date=None,
        child_health_granted=None,
        location=None,
        activity_weather=None,
        domain_store=None,
        **kwargs,
    )
    return contexts["food"].ports


def test_넘긴_Food_카탈로그_어댑터를_그대로_쓴다() -> None:
    catalog, recipes, suggestions = object(), object(), object()

    ports = _food_ports(food_catalog=catalog, food_recipes=recipes, food_suggestions=suggestions)

    assert (ports.catalog, ports.recipes, ports.suggestions) == (catalog, recipes, suggestions)


def test_안_넘기면_빈_InMemory_다() -> None:
    ports = _food_ports()

    assert type(ports.catalog).__name__ == "InMemoryMenuCatalog"
    assert type(ports.recipes).__name__ == "InMemoryRecipeCatalog"
    assert type(ports.suggestions).__name__ == "InMemorySuggestionHistory"


def test_하나만_넘겨도_나머지는_건드리지_않는다() -> None:
    recipes = object()

    ports = _food_ports(food_recipes=recipes)

    assert ports.recipes is recipes
    assert type(ports.catalog).__name__ == "InMemoryMenuCatalog"


async def test_Food_문서는_검수된_시드_행을_읽는다() -> None:
    # 일반 추천은 문서 행이 근거다 — 비어 있으면 Food 는 일반 추천을 못 낸다. 이유기 월령으로 본다:
    # 30개월은 시드가 1행뿐이라(meal_pattern · nutrient_note 행이 아직 없다) 조금만 바뀌어도 깨진다
    rows = await _food_ports().docs.search(
        months=6,
        row_types=(
            "weaning_stage",
            "weaning_ingredient",
            "meal_pattern",
            "nutrient_note",
            "feeding_tip",
        ),
        limit=200,
    )

    assert rows


async def test_넘긴_Food_어댑터를_handle_input_과_suggest_가_둘_다_받는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    original = entrypoint._domain_contexts

    def spy(**kwargs: Any) -> Any:
        seen.append((kwargs["food_catalog"], kwargs["food_recipes"], kwargs["food_suggestions"]))
        return original(**kwargs)

    async def fake_run(*args: Any, **kwargs: Any) -> Any:
        return pipeline.SuggestRun(outcomes=(), failed=())

    async def fake_handle(*args: Any, **kwargs: Any) -> str:
        return "RESULT"

    monkeypatch.setattr(entrypoint, "_domain_contexts", spy)
    monkeypatch.setattr(pipeline, "run_suggestions", fake_run)
    monkeypatch.setattr(entrypoint, "_handle_input", fake_handle)
    catalog, recipes, suggestions = object(), object(), object()
    stores = {"food_catalog": catalog, "food_recipes": recipes, "food_suggestions": suggestions}

    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="저녁 추천해줘",
        run_id="r",
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
        **stores,
    )
    await entrypoint.suggest(
        child_id=CHILD,
        tasks=[],
        request_id="q",
        save_run_id=None,
        sent_at=None,
        save_suggestions=InMemorySuggestions(),
        **stores,
    )

    assert seen == [(catalog, recipes, suggestions)] * 2
