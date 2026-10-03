"""handle_input() 검증. 가짜 LLM 2개 + 실제 Food mock 으로 돈다 (API 호출 0).

Supervisor 출력은 routing_cases 의 정답(RC01 등)을 그대로 쓴다 — 조각을 손으로 적지 않는다.
일부러 틀리게 나눈 출력은 재분기 테스트에서만 손으로 적는다.
여기서 보는 것:
  순서    Memory 가 끝난 뒤에 Food 가 불린다 (루트 §4 — 저장이 검색보다 먼저)
  전달    Food 는 food 로 온 REQUEST 조각과 식이 단계만 받는다 (S10)
  실패    Supervisor 실패는 강등, Memory 실패는 failed(llm_unavailable)
  재분기  Memory 가 적지 않은 조각을 Supervisor 에게 돌려주고 한 번만 다시 나눈다
  이벤트  step · saved · guidance · domain_routed · rerouted · failed · done
"""

import asyncio
import calendar
import gc
import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents import pipeline
from app.agents.common.llm_client import LLMResponse, LLMUnavailableError
from app.agents.common.schemas.task import DomainTask
from app.agents.food.context import FoodContext
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store import (
    DaycareMealRow,
    InMemoryDaycareMeals,
    InMemoryProfile,
    in_memory_ports,
)
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import PendingMemoryContext, WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.memory_bridge import StoreFoodMemory
from app.agents.pipeline import (
    DomainRouted,
    Done,
    Failed,
    MemoryNote,
    Partial,
    PendingReply,
    Rerouted,
    Saved,
    Step,
    Unavailable,
    Unwritten,
    handle_input,
)
from app.agents.run_writes import RunWrites, record_food_writes
from app.agents.supervisor import routing as routing_module
from app.agents.supervisor.routing import Guidance
from app.rules.age import Stage
from tests.eval.agents.routing_cases import CASES_BY_ID, answer_output

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=KST)
CHILD = UUID(int=1)
RUN_ID = "run-1"

# 관찰 저장 인자 — 도메인별 필수 필드만 채운다
_STRAWBERRY = {
    "raw_text": "오늘 어린이집에서 딸기는 잘 먹었대",
    "observed_on": "오늘",
    "subject": "딸기",
    "confidence_source": "parent_hearsay",
}
_LIKES_STRAWBERRY = {
    "raw_text": "요즘 딸기 좋아하는 것 같은데",
    "observed_on": "오늘",
    "subject": "딸기",
    "polarity": 1,
}
_APPLE = {
    "raw_text": "오늘 민준이가 아침에 사과를 반 개 먹었어",
    "observed_on": "오늘",
    "subject": "사과",
    "amount": "반 개",
}
_CURRY = {"raw_text": "오늘 점심에 카레 먹었어", "observed_on": "오늘", "subject": "카레"}
_VEGETABLE = {
    "raw_text": "요즘 채소를 너무 안 먹는데",
    "observed_on": "오늘",
    "subject": "채소",
    "polarity": -1,
}
_POOL = {
    "raw_text": "오늘 수영장 다녀왔어",
    "observed_on": "오늘",
    "subject": "수영장",
    "activity": "수영장 놀이",
}
_RASH = {
    "raw_text": "오늘 우유 마시고 입 주변이 빨개졌어",
    "observed_on": "오늘",
    "symptom": ["입 주변 발적"],
}


@pytest.fixture
def memory_context() -> AgentContext:
    return AgentContext(
        child_id=CHILD,
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


def _birth_date_for(stage: Stage) -> date:
    """`life_stage(생일, NOW.date()).stage` 가 이 값이 되는 생일. 경계에서 떨어진 안전한 월령."""
    months = {"infant_milk": 1, "infant_weaning": 6, "toddler": 24, "preschool": 48}[stage]
    today = NOW.date()
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, min(today.day, calendar.monthrange(year, month + 1)[1]))


def _food_context(
    stage: Stage = "toddler", *, daycare: bool = True, store: InMemoryStore | None = None
) -> FoodContext:
    """급식 행은 기본으로 하나 심어 둔다 — 기존 테스트의 tool 개수(5·7개)를 그대로 유지한다.

    store 를 주면 기억 포트가 그 store 를 읽는다 (entrypoint 와 같은 연결).
    """
    rows = (
        [
            DaycareMealRow(
                id=UUID(int=900),
                child_id=CHILD,
                serve_date=NOW.date(),
                meal_slot="lunch",
                menu_keys=("test",),
            )
        ]
        if daycare
        else []
    )
    memory: dict[str, Any] = {} if store is None else {"memory": StoreFoodMemory(store)}
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: _birth_date_for(stage)}),
        daycare=InMemoryDaycareMeals(rows),
        **memory,
    )
    return FoodContext(child_id=CHILD, run_id=RUN_ID, now=NOW, timezone=KST, ports=ports)


@pytest.fixture
def food_context() -> FoodContext:
    return _food_context()


@pytest.fixture
def events() -> list[Any]:
    return []


@pytest.fixture
def saved_when_food_ran(monkeypatch: pytest.MonkeyPatch, memory_context: AgentContext) -> list[int]:
    """Food 가 불릴 때 이미 저장돼 있던 관찰 수. Memory 가 끝난 뒤여야 한다 (루트 §4)."""
    seen: list[int] = []
    real = pipeline._RUNNERS["food"]

    async def recording(task: Any, context: Any, **kwargs: Any) -> Any:
        rows = await memory_context.store.query_observations(domain="food", child_id=CHILD)
        seen.append(len(rows))
        return await real(task, context, **kwargs)

    monkeypatch.setitem(pipeline._RUNNERS, "food", recording)
    return seen


class FakeLLM:
    """정해둔 응답을 차례로 돌려준다. 예외를 넣어 두면 그 차례에 던진다."""

    def __init__(self, *items: LLMResponse | Exception) -> None:
        self._queue = list(items)
        self.calls = 0
        self.seen: list[list[dict[str, Any]]] = []  # 매 호출의 messages

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        self.seen.append(list(kwargs.get("messages") or []))
        item = self._queue.pop(0) if self._queue else _reply("끝")
        if isinstance(item, Exception):
            raise item
        return item


def _reply(content: str) -> LLMResponse:
    message = SimpleNamespace(content=content, tool_calls=None)
    return LLMResponse(message=message, usage={}, latency_ms=1)


def _tools(*calls: SimpleNamespace) -> LLMResponse:
    message = SimpleNamespace(content=None, tool_calls=list(calls))
    return LLMResponse(message=message, usage={}, latency_ms=1)


def _call(call_id: str, name: str, arguments: dict[str, Any] | str) -> SimpleNamespace:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=raw))


def _answer(reply: dict[str, Any]) -> LLMResponse:
    """모델의 마지막 말. structured output 이라 content 가 스키마 JSON 이다."""
    return _reply(json.dumps({"pending_hint": None, **reply}, ensure_ascii=False))


def _supervisor_llm(case_id: str) -> FakeLLM:
    """그 케이스의 정답 Supervisor 출력을 route tool 호출로 돌려준다."""
    output = answer_output(CASES_BY_ID[case_id])
    return FakeLLM(_tools(_call("s1", "route", output.model_dump(mode="json", exclude_none=True))))


async def _handle(
    case_id: str,
    *,
    memory_llm: FakeLLM,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
    supervisor_llm: FakeLLM | None = None,
    contexts: dict[str, Any] | None = None,
    commit: Any = None,
) -> pipeline.PipelineResult:
    return await handle_input(
        CASES_BY_ID[case_id].text,
        memory_context,
        contexts or {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=supervisor_llm or _supervisor_llm(case_id),
        memory_client=memory_llm,
        emit=events.append,
        commit=commit,
    )


def _of(events: list[Any], kind: type) -> list[Any]:
    return [event for event in events if isinstance(event, kind)]


def _order(events: list[Any]) -> list[str]:
    return [type(event).__name__ for event in events]


# ── 혼합형 한 건을 끝까지 ────────────────────────────────────────
async def test_RC01_기록한_뒤_Food_로_넘긴다(
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
    saved_when_food_ran: list[int],
) -> None:
    # 사실("잘 먹었대")과 인상("좋아하는 것 같은데")은 같은 대상이라 한 건으로 합친다.
    # 둘로 나눠 부르면 같은 날 같은 대상이라 둘째가 막힌다 (RC08 중복 가드)
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", {**_STRAWBERRY, "polarity": 1})),
        _reply("딸기 기록 남겼어요."),
    )
    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert _order(events) == [
        "Step",  # 입력을 살펴보고
        "Step",  # 관찰을 나누고
        "Saved",
        "MemoryNote",
        "Step",  # 다음 행동을 준비하고
        "DomainRouted",
        "Done",
    ]
    assert saved_when_food_ran == [1]  # Food 를 부를 때 이미 저장돼 있었다 (루트 §4)
    assert [(step.index, step.total) for step in _of(events, Step)] == [(1, 3), (2, 3), (3, 3)]
    assert [ref.kind for ref in _of(events, Saved)[0].refs] == ["observation_food"]
    assert result.failed is None
    assert result.model_calls == 2  # Supervisor 1 + Memory 1 + Food mock 0 (S8)
    assert _of(events, Done)[0] == Done(RUN_ID, 2)


async def test_RC01_Food_는_요청_조각과_식이_단계만_받는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # S10 — 아이 이름·다른 도메인 기록은 넘기지 않는다. 맥락은 Food 가 직접 조회한다
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _STRAWBERRY)), _reply("기록했어요.")
    )
    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    task = result.routing.domain_tasks[0]
    assert task.request_texts == ("저녁에는 뭐 먹이는 게 좋을까?",)
    assert _of(events, DomainRouted) == [DomainRouted("food", "meal_recommendation", "mock", 0)]
    food = result.domain[0]  # tool 묶음은 Food mock 결과가 들고 있다
    assert food.task_type == FoodTaskType.MEAL_RECOMMENDATION
    assert (food.stage, food.status) == ("toddler", "mock")
    assert len(food.tools) == 5  # 식단 추천 × 유아기
    assert food.requires_safety_check is True  # health_safety 사전 확인 대상 (S6)


async def test_T07_Memory_가_저장한_관찰을_같은_run_의_Food_가_기억_포트로_읽는다(
    monkeypatch: pytest.MonkeyPatch, memory_context: AgentContext, events: list[Any]
) -> None:
    # Food 는 Memory store 를 직접 보지 않는다. 자기 기억 포트로 읽어도 방금 저장한 것이 보여야 한다
    seen: list[list[str]] = []
    real = pipeline._RUNNERS["food"]

    async def reading(task: Any, context: Any, **kwargs: Any) -> Any:
        rows = await context.ports.memory.observations(
            child_id=CHILD, date_from=NOW.date(), date_to=NOW.date()
        )
        seen.append([row.subject for row in rows])
        return await real(task, context, **kwargs)

    monkeypatch.setitem(pipeline._RUNNERS, "food", reading)
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", {**_STRAWBERRY, "polarity": 1})),
        _reply("딸기 기록 남겼어요."),
    )

    await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=_food_context(store=memory_context.store),
        events=events,
    )

    assert seen == [["딸기"]]


async def test_RC21_영양소_분석은_사전_확인이_없고_묶음이_다르다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory_llm = FakeLLM(
        _tools(
            _call("a", "create_observation_food", _CURRY),
            _call("b", "create_observation_food", _VEGETABLE),
        )
    )
    result = await _handle(
        "RC21",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert result.memory is not None
    assert result.memory.ended_by == "coverage"  # 힌트를 다 채워 요약 호출 없이 끝났다
    assert not _of(events, MemoryNote)  # 조기 종료면 메모가 없다 — 응답 문구는 화면이 만든다
    food = result.domain[0]
    assert food.task_type == FoodTaskType.NUTRIENT_ANALYSIS
    assert len(food.tools) == 7  # 영양소 분석 × 유아기
    assert food.requires_safety_check is False
    assert result.failed is None


async def test_RC21_영아기에는_영양소_분석을_하지_않는다(
    memory_context: AgentContext, events: list[Any]
) -> None:
    # 같은 입력, 식이 단계만 영아기. 모델을 부르지 않고 status 로 알린다
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _CURRY)), _reply("기록했어요.")
    )
    result = await _handle(
        "RC21",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=_food_context("infant_weaning"),
        events=events,
    )

    food = result.domain[0]
    assert food.status == "unsupported_stage"
    assert food.tools == ()
    assert food.model_calls == 0


# ── 순수 요청형은 Memory 를 건너뛴다 ─────────────────────────────
async def test_RC20_기록할_조각이_없으면_Memory_를_부르지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # Supervisor 가 의도를 제대로 나눴다고 본다. 원문이 전부 요청이면 저장할 게 없다
    memory_llm = FakeLLM(_reply("불리면 안 된다"))
    result = await _handle(
        "RC20",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert memory_llm.calls == 0
    assert result.memory is None
    assert result.routing.memory_task is None
    assert [step.index for step in _of(events, Step)] == [1, 3]  # 저장 단계가 없다
    assert len(result.domain) == 1  # Food 는 그대로 돈다
    assert result.failed is None
    assert result.model_calls == 1  # Supervisor 한 번뿐


async def test_RC07_미구현_agent_만_짚었어도_실패가_아니다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 놀이 추천 요청 단독 — Memory 도 Food 도 안 돈다. 그래도 "준비 중" 을 띄울 수 있다
    memory_llm = FakeLLM(_reply("불리면 안 된다"))
    result = await _handle(
        "RC07",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert memory_llm.calls == 0
    assert result.memory is None
    assert _of(events, Unavailable)[0].agents == ("activity",)
    assert result.failed is None  # unparsable 이 아니다 — 못 읽은 게 아니라 아직 없는 agent 다


async def test_RC16_미구현_agent_는_이름만_남기고_부르지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory_llm = FakeLLM(_tools(_call("a", "create_observation_activity", _POOL)))
    result = await _handle(
        "RC16",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert _of(events, Unavailable)[0].agents == ("activity",)
    assert len(result.domain) == 1  # food 만 실제로 돌았다
    assert result.routing.dropped_agents == ()


# ── Supervisor 가 실패해도 기록은 남는다 (S2 · S5) ───────────────
async def test_Supervisor_출력이_깨져도_Memory_는_돈다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    broken = FakeLLM(_tools(_call("s1", "route", '{"segments": [{"text":')))
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _STRAWBERRY)), _reply("기록했어요.")
    )
    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        supervisor_llm=broken,
    )

    assert result.routing.degraded is True
    assert result.routing.memory_task is not None
    assert result.routing.memory_task.hints == ()  # 힌트 없이 원문 전체로 돈다
    assert _of(events, Saved)  # 기록은 남았다
    assert result.domain == ()  # 어디로 보낼지 모르니 Food 는 부르지 않는다
    assert result.failed is None  # Supervisor 실패만으로는 입력 실패가 아니다
    assert result.disagreement.count == 0  # 강등이면 비교하지 않는다


async def test_Supervisor_가_죽어도_입력은_실패가_아니다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    dead = FakeLLM(LLMUnavailableError("망"))
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _STRAWBERRY)), _reply("기록했어요.")
    )
    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        supervisor_llm=dead,
    )

    assert result.routing.degraded is True
    assert result.failed is None
    rows = await memory_context.store.query_observations(domain="food", child_id=CHILD)
    assert len(rows) == 1


# ── 실패 ────────────────────────────────────────────────────────
async def test_Memory_가_죽으면_입력을_그대로_돌려준다(
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
    saved_when_food_ran: list[int],
) -> None:
    result = await _handle(
        "RC01",
        memory_llm=FakeLLM(LLMUnavailableError("망")),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    failed = _of(events, Failed)[0]
    assert failed == Failed("llm_unavailable", CASES_BY_ID["RC01"].text)
    assert result.failed == failed
    assert result.memory is None
    assert saved_when_food_ran == []  # 근거가 없으니 Food 도 부르지 않는다
    assert _order(events)[-2:] == ["Failed", "Done"]


async def test_아무것도_못_했으면_unparsable(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 저장 0 · Food 0 · 안내 0 · 메모 없음
    result = await _handle(
        "RC18",
        # 빈 턴은 한 번 다시 묻는다. 두 번째도 비면 그대로 끝난다 — 메모가 없는 상태
        memory_llm=FakeLLM(_reply(""), _reply("")),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert result.failed == Failed("unparsable", CASES_BY_ID["RC18"].text)


# ── 말만 하고 안 쓴 run 을 표시한다 ──────────────────────────────
async def test_저장했다고_답하고_tool_을_안_부르면_표시한다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    """라이브 RC01 — Memory 가 tool 을 한 번도 안 부르고 "기록했어요" 라고 답했다(저장 0건).

    사용자 응답은 그대로 둔다. 판정도 하지 않는다 — 빈도를 보려고 남기는 표시다.
    """
    memory_llm = FakeLLM(_reply("딸기 기록 남겼어요."))
    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert _of(events, Unwritten) == [Unwritten(hints=3, tools=0, note=True)]
    assert _of(events, MemoryNote)[0].text == "딸기 기록 남겼어요."  # 응답은 그대로 나간다
    assert result.failed is None


async def test_쓰기가_있으면_표시하지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", {**_STRAWBERRY, "polarity": 1})),
        _reply("기록했어요."),
    )
    await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert not _of(events, Unwritten)


# ── 잘못 위임되면 다시 나눈다 ────────────────────────────────────
_CAKE = {
    "raw_text": "오늘 간식으로 딸기케이크를 먹였어",
    "observed_on": "오늘",
    "subject": "딸기케이크",
}

# 저녁 메뉴 물음까지 record 로 보낸 출력. 그대로면 이 물음은 아무 데서도 답을 못 받는다
_T14_ALL_RECORD = {
    "segments": [
        {"text": "오늘 간식으로 딸기케이크를 먹였어", "kind": "record", "work": "observe"},
        {"text": "저녁에는 뭘 먹이면 좋을까?", "kind": "record", "work": "observe"},
    ]
}


def _route_reply(output: dict[str, Any]) -> LLMResponse:
    return _tools(_call("s1", "route", output))


def _t14_answer() -> dict[str, Any]:
    return answer_output(CASES_BY_ID["T14"]).model_dump(mode="json", exclude_none=True)


async def test_T14_Memory_가_적지_않은_물음은_Supervisor_가_다시_나눠_Food_로_보낸다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    supervisor_llm = FakeLLM(_route_reply(_T14_ALL_RECORD), _route_reply(_t14_answer()))
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _CAKE)), _reply("간식을 기록했어요.")
    )
    result = await _handle(
        "T14",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        supervisor_llm=supervisor_llm,
    )

    # Supervisor 에게 무엇이 안 됐는지 알려 줬다 — 두 번째 호출의 마지막 메시지
    assert supervisor_llm.calls == 2
    feedback = supervisor_llm.seen[1][-1]["content"]
    assert feedback.startswith("[다시 나누기]")  # Supervisor 프롬프트의 [다시 나눌 때] 와 같은 표시
    assert "저녁에는 뭘 먹이면 좋을까?" in feedback
    assert "딸기케이크" not in feedback  # 적힌 조각은 되돌리지 않는다

    assert _of(events, Rerouted) == [Rerouted(1, ("food:meal_recommendation",))]
    assert [food.task_type for food in result.domain] == [FoodTaskType.MEAL_RECOMMENDATION]
    assert result.routing.domain_tasks[0].request_texts == ("저녁에는 뭘 먹이면 좋을까?",)
    assert result.rerouted is not None
    # Memory 는 다시 돌리지 않았다 — 간식은 한 번만 저장됐다
    assert memory_llm.calls == 2
    rows = await memory_context.store.query_observations(domain="food", child_id=CHILD)
    assert [row.fields["subject"] for row in rows] == ["딸기케이크"]
    assert _order(events).index("Rerouted") < _order(events).index("DomainRouted")
    assert result.model_calls == 3  # Supervisor 2 + Memory 1 — 다시 나눈 run 의 예산은 5


async def test_Food_로_간_조각이_있으면_다시_나누지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # RC01 — Memory 가 "딸기 잘 먹었대" 와 "딸기 좋아하는 것 같은데" 를 한 건으로 합쳐 적었다.
    # 힌트 하나가 raw_text 에 안 남지만 잘못 위임된 게 아니다. 요청은 이미 Food 로 갔다
    merged = {**_STRAWBERRY, "polarity": 1}
    supervisor_llm = _supervisor_llm("RC01")
    result = await _handle(
        "RC01",
        memory_llm=FakeLLM(_tools(_call("a", "create_observation_food", merged)), _reply("")),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        supervisor_llm=supervisor_llm,
    )

    assert supervisor_llm.calls == 1
    assert result.rerouted is None
    assert not _of(events, Rerouted)


async def test_다시_나누기가_실패하면_처음_나눈_대로_간다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 두 번째 Supervisor 가 route 를 안 부르고 말로 답했다. 입력을 실패로 만들지 않는다 (S5)
    supervisor_llm = FakeLLM(_route_reply(_T14_ALL_RECORD), _reply("다시 나눌게요"))
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _CAKE)), _reply("간식을 기록했어요.")
    )
    result = await _handle(
        "T14",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        supervisor_llm=supervisor_llm,
    )

    assert supervisor_llm.calls == 2  # 한 번만 다시 묻는다
    assert result.rerouted is None
    assert result.domain == ()
    assert result.failed is None  # 간식은 저장됐다
    assert result.model_calls == 3  # 실패한 재시도도 호출은 호출이다


# ── 안내 ────────────────────────────────────────────────────────
async def test_RC10_알레르기_등록은_안내하고_증상은_저장한다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 루트 §2 — health_safety 는 보호자 직접 입력. 안내 때문에 관찰이 사라지면 안 된다
    memory_llm = FakeLLM(_tools(_call("a", "create_observation_health", _RASH)))
    result = await _handle(
        "RC10",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    guidance = _of(events, Guidance)[0]
    assert guidance.code == "safety_record"
    assert guidance.deeplink == "settings/health-safety"
    rows = await memory_context.store.query_observations(domain="health", child_id=CHILD)
    assert len(rows) == 1
    assert result.failed is None


# ── 불일치 기록 ─────────────────────────────────────────────────
async def test_Supervisor_가_짚지_않은_기록을_Memory_가_하면_남긴다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # RC18 정답 조각은 관찰 하나뿐인데 Memory 가 일정까지 만들었다 — Supervisor 가 놓쳤을 수 있다
    memory_llm = FakeLLM(
        _tools(
            _call("a", "create_observation_food", _APPLE),
            _call(
                "b",
                "create_event",
                {"title": "아침 식사", "starts_on": "내일", "starts_time": "오전 8시"},
            ),
        ),
        _reply("기록했어요."),
    )
    result = await _handle(
        "RC18",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert result.disagreement.memory_only == ("schedule",)
    assert result.disagreement.supervisor_only == ()


async def test_힌트를_짚었는데_Memory_가_저장하지_않으면_남긴다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # RC01 정답에는 관찰 2 + 일정 1 이 있는데 Memory 는 관찰만 저장했다
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _STRAWBERRY)), _reply("기록했어요.")
    )
    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert result.disagreement.supervisor_only == ("schedule",)
    assert result.disagreement.memory_only == ()


# ── 로그 ────────────────────────────────────────────────────────
async def test_로그에_원문도_조각도_메모도_없다(
    caplog: pytest.LogCaptureFixture,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 루트 §2 · S10 — 로그에는 개수·라벨·코드만
    caplog.set_level(logging.INFO, logger="app.agents")
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _STRAWBERRY)),
        _reply("딸기 기록 남겼어요."),
    )
    await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert f"pipeline run_id={RUN_ID}" in caplog.text  # 로그가 비어서 통과하는 게 아니다
    for fragment in ("딸기", "어린이집", "먹이는", "알림도"):
        assert fragment not in caplog.text


async def test_Memory_가_여러_바퀴_돌아도_호출_예산은_1만_먹는다(
    caplog: pytest.LogCaptureFixture,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 세는 단위는 Agent 진입이다. Memory 가 tool 을 네 번 불러도 예산에서는 1이다.
    # 예산(MAX_MODEL_CALLS)을 넘으면 서버 알람이 뜬다 (NF-01 · S8)
    caplog.set_level(logging.WARNING, logger="app.agents.pipeline")
    memory_llm = FakeLLM(
        _tools(_call("a", "query_event", {})),
        _tools(_call("b", "query_event", {})),
        _tools(_call("c", "create_observation_food", _STRAWBERRY)),
        _reply("기록했어요."),
    )
    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert memory_llm.calls == 4  # 실제 왕복은 네 번
    assert result.model_calls == 2  # Supervisor 1 + Memory 1
    assert "model_calls 예산 초과" not in caplog.text


async def test_단계마다_걸린_시간을_남긴다(
    two_agents: None,
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 도메인 단계에 최소 예산을 줄지는 이 값을 보고 정한다 (#202 범위 밖)
    caplog.set_level(logging.INFO, logger="app.agents.pipeline")
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food", delay=0.05))

    result = await _handle(
        "RC16",
        memory_llm=_pool_memory(),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        contexts=_FAKE_CONTEXTS,
    )

    times = result.times
    assert times.supervisor_ms is not None
    assert times.memory_ms is not None
    assert times.budget_ms is not None and times.budget_ms <= pipeline.RUN_DEADLINE_S * 1000
    assert times.writers_ms is None  # 쓰는 task 가 없었다
    lines = [r.getMessage() for r in caplog.records if r.name == pipeline.__name__]
    assert any("readers_ms=" in line and "budget_ms=" in line for line in lines)
    task_lines = [line for line in lines if line.startswith("도메인 task ")]
    assert len(task_lines) == 2  # task 마다 한 줄
    assert all("result=ok" in line and " ms=" in line for line in task_lines)
    # 묶음 시간은 늦은 쪽까지 기다린 값이다. 둘 다 perf_counter 라 루프 시계와 어긋나지 않는다
    slowest = max(int(line.rsplit("ms=", 1)[1]) for line in task_lines)
    assert times.readers_ms is not None and times.readers_ms >= slowest
    assert all("수영장" not in line for line in lines)  # 조각은 남기지 않는다


async def test_Memory_를_건너뛴_run_은_기록_단계_시간이_없다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # RC20 은 순수 요청형이라 Memory 를 부르지 않는다
    result = await _handle(
        "RC20",
        memory_llm=FakeLLM(_reply("불리면 안 된다")),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert result.times.supervisor_ms is not None
    assert result.times.memory_ms is None
    assert result.times.readers_ms is not None


async def test_이어받기는_기록_단계_시간만_남긴다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_health", _RASH)),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message"}),
    )

    result = await handle_input(
        "3일 전부터",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=memory,
        emit=events.append,
        continuation=_COUGH_PENDING,
    )

    assert result.times.memory_ms is not None
    assert result.times == pipeline.StageTimes(memory_ms=result.times.memory_ms)


async def test_시작도_못_하고_끊긴_task_는_ms_가_없다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # 앞 단계가 20초를 다 쓴 run. 0ms 로 남기면 "Agent 가 빨랐다" 로 읽힌다
    caplog.set_level(logging.INFO, logger="app.agents.pipeline")
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food", delay=1))

    ran = await pipeline._run_domain((_FOOD_TASK,), _FAKE_CONTEXTS, timeout=0.0)

    assert ran.partial == Partial("timeout_20s", succeeded=(), failed=("food",))
    assert ran.readers_ms is not None  # 묶음은 돌았다(바로 끊겼다)
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("도메인 task ")]
    assert "result=timeout" in line
    assert line.endswith("ms=None")


def test_구현된_agent_마다_실행_함수가_있다() -> None:
    # 한쪽만 늘리면 routing 이 보낸 task 를 pipeline 이 못 찾는다
    assert set(pipeline._RUNNERS) == routing_module.IMPLEMENTED_AGENTS


# ── 도메인 Agent 실행 (공통_구현_계획 §5 · NF-06) ─────────────────
@dataclass(frozen=True)
class _Outcome:
    """가짜 도메인 결과. pipeline 은 agent · task_type · status · model_calls 만 읽는다."""

    agent: str
    task_type: str | None = None
    status: str = "completed"
    model_calls: int = 1


@dataclass(frozen=True)
class _FakeContext:
    def for_task(self) -> "_FakeContext":
        return _FakeContext()


def _runner(
    agent: str, *, delay: float = 0.0, log: list[str] | None = None, name: str | None = None
) -> Any:
    label = name or agent

    async def run(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        if log is not None:
            log.append(f"{label} 시작")
        await asyncio.sleep(delay)
        if log is not None:
            log.append(f"{label} 끝")
        return _Outcome(agent, task.task_type)

    return run


def _failing(error: Exception) -> Any:
    async def run(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        raise error

    return run


_FAKE_CONTEXTS = {"food": _FakeContext(), "activity": _FakeContext()}


@pytest.fixture
def two_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """activity 를 구현된 것처럼 연다. Activity 연결 전까지 두 Agent 동시 실행은 이렇게 본다."""
    monkeypatch.setattr(routing_module, "IMPLEMENTED_AGENTS", frozenset({"food", "activity"}))
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food"))
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity"))


def _pool_memory() -> FakeLLM:
    return FakeLLM(_tools(_call("a", "create_observation_activity", _POOL)), _reply("기록했어요."))


async def test_RC16_도메인_Agent_둘은_동시에_돈다(
    two_agents: None,
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    log: list[str] = []
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food", delay=0.01, log=log))
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity", delay=0.01, log=log))

    result = await _handle(
        "RC16",
        memory_llm=_pool_memory(),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        contexts=_FAKE_CONTEXTS,
    )

    assert set(log[:2]) == {"food 시작", "activity 시작"}  # 하나가 끝나기 전에 둘 다 시작했다
    assert [item.agent for item in result.domain] == ["food", "activity"]  # task 순서
    assert result.partial is None
    assert result.model_calls == 4  # Supervisor 1 + Memory 1 + 도메인 2


async def test_RC16_한_Agent_가_죽어도_나머지_결과는_나간다(
    two_agents: None,
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _failing(RuntimeError("부서졌다")))

    result = await _handle(
        "RC16",
        memory_llm=_pool_memory(),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        contexts=_FAKE_CONTEXTS,
    )

    assert [item.agent for item in result.domain] == ["food"]
    assert result.partial == Partial("agent_error", succeeded=("food",), failed=("activity",))
    assert result.failed is None
    assert _order(events)[-3:] == ["DomainRouted", "Partial", "Done"]
    assert result.model_calls == 3  # 죽은 쪽은 몇 번 불렀는지 몰라 세지 않는다


async def test_Agent_예외_메시지는_로그에_남기지_않는다(
    two_agents: None,
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _failing(RuntimeError("민준이 수영장")))

    with caplog.at_level(logging.INFO):
        await _handle(
            "RC16",
            memory_llm=_pool_memory(),
            memory_context=memory_context,
            food_context=food_context,
            events=events,
            contexts=_FAKE_CONTEXTS,
        )

    text = "\n".join(r.getMessage() for r in caplog.records if r.name == pipeline.__name__)
    assert "RuntimeError" in text
    assert "민준이" not in text


async def test_RC20_도메인_Agent_가_전부_죽고_남은_게_없으면_failed(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 순수 요청형이라 Memory 도 안 돈다. 보여 줄 게 하나도 없다
    monkeypatch.setitem(pipeline._RUNNERS, "food", _failing(RuntimeError("부서졌다")))

    result = await _handle(
        "RC20",
        memory_llm=FakeLLM(_reply("불리면 안 된다")),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert result.failed == Failed("llm_unavailable", CASES_BY_ID["RC20"].text)
    assert result.partial is not None and result.partial.failed == ("food",)
    assert not _of(events, Partial)  # failed 와 partial 은 함께 나가지 않는다
    assert _order(events)[-2:] == ["Failed", "Done"]


async def test_같은_Agent_의_task_둘은_state_를_따로_받는다(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 식단 추천과 영양소 분석이 동시에 돈다. state 를 같이 쓰면 한쪽 근거가 다른 쪽 검증에 섞인다
    states: list[Any] = []
    real = pipeline._RUNNERS["food"]

    async def recording(task: Any, context: Any, **kwargs: Any) -> Any:
        states.append(context.state)
        return await real(task, context, **kwargs)

    monkeypatch.setitem(pipeline._RUNNERS, "food", recording)
    raw = "영양 골고루 먹었는지 봐줘. 내일 간식은 뭐가 좋을까?"
    output = {
        "segments": [
            {
                "text": "영양 골고루 먹었는지 봐줘",
                "kind": "request",
                "agent": "food",
                "food_task": "nutrient_analysis",
            },
            {
                "text": "내일 간식은 뭐가 좋을까?",
                "kind": "request",
                "agent": "food",
                "food_task": "meal_recommendation",
            },
        ]
    }

    result = await handle_input(
        raw,
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(_tools(_call("s1", "route", output))),
        memory_client=FakeLLM(_reply("불리면 안 된다")),
        emit=events.append,
    )

    assert [item.task_type for item in result.domain] == [
        "nutrient_analysis",
        "meal_recommendation",
    ]
    assert len(states) == 2
    assert states[0] is not states[1]
    assert all(state is not food_context.state for state in states)


async def test_구현된_agent_의_context_가_빠지면_부분_실패로_숨기지_않는다(
    memory_context: AgentContext, events: list[Any]
) -> None:
    with pytest.raises(KeyError):
        await handle_input(
            CASES_BY_ID["RC20"].text,
            memory_context,
            {},
            run_id=RUN_ID,
            supervisor_client=_supervisor_llm("RC20"),
            memory_client=FakeLLM(),
            emit=events.append,
        )


async def test_RC16_20초를_넘긴_Agent_는_끊고_나머지로_끝낸다(
    two_agents: None,
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    monkeypatch.setattr(pipeline, "RUN_DEADLINE_S", 0.5)  # 앞 단계는 FakeLLM 이라 수 ms
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity", delay=5))

    result = await _handle(
        "RC16",
        memory_llm=_pool_memory(),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        contexts=_FAKE_CONTEXTS,
    )

    assert result.partial == Partial("timeout_20s", succeeded=("food",), failed=("activity",))
    assert [item.agent for item in result.domain] == ["food"]
    assert result.latency_ms < 3000  # 5초를 기다리지 않았다


async def test_RC01_앞에서_시간을_다_쓰면_도메인_Agent_는_바로_끊기고_저장은_남는다(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    monkeypatch.setattr(pipeline, "RUN_DEADLINE_S", 0.0)
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food", delay=1))
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", {**_STRAWBERRY, "polarity": 1})),
        _reply("딸기 기록 남겼어요."),
    )

    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
    )

    assert _of(events, Saved)  # Memory 는 끊지 않는다
    assert result.partial == Partial("timeout_20s", succeeded=(), failed=("food",))
    assert result.failed is None  # 저장이 있으니 실패가 아니다
    assert _order(events)[-2:] == ["Partial", "Done"]


async def test_끊긴_Agent_는_뒤에서_계속_돌지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    # _run_domain 을 직접 부른다. handle_input 으로 돌리면 앞 단계가 제한을 다 써서
    # Agent 가 시작도 안 한 채 통과할 수 있다
    log: list[str] = []

    async def slow(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        log.append("시작")
        await asyncio.sleep(0.2)
        log.append("끝")  # 여기까지 오면 취소가 안 된 것이다
        return _Outcome(task.agent)

    monkeypatch.setitem(pipeline._RUNNERS, "food", slow)
    task = DomainTask(run_id=RUN_ID, agent="food", task_type=None, request_texts=("x",))

    ran = await pipeline._run_domain((task,), {"food": _FakeContext()}, timeout=0.05)
    await asyncio.sleep(0.3)

    assert log == ["시작"]  # 시작은 했고, 끊긴 뒤 뒤에서 이어 돌지 않았다
    assert ran.outcomes == ()
    assert ran.partial == Partial("timeout_20s", succeeded=(), failed=("food",))


# 같은 Agent 의 task 둘. 하나는 급식을 고치고, 하나는 그 급식을 읽어 추천한다
_MEAL = DomainTask(
    run_id=RUN_ID, agent="food", task_type="daycare_meal", request_texts=("급식 대신 두유 받았대",)
)
_DINNER = DomainTask(
    run_id=RUN_ID,
    agent="food",
    task_type="meal_recommendation",
    request_texts=("저녁 뭐 먹일까?",),
)
_MEAL_AND_DINNER_RAW = "급식 대신 두유 받았대. 저녁 뭐 먹일까?"


def _by_type(**runners: Any) -> Any:
    """task_type 마다 다른 가짜 실행 함수. 같은 Agent 의 쓰는 task 와 읽는 task 를 나눠 본다."""

    async def run(task: DomainTask, context: Any, **kwargs: Any) -> Any:
        return await runners[str(task.task_type)](task, context, **kwargs)

    return run


def _food_requests(*pairs: tuple[str, str]) -> FakeLLM:
    """food 요청 조각만 있는 Supervisor 출력. (조각, food_task) 쌍을 순서대로 받는다."""
    output = {
        "segments": [
            {"text": text, "kind": "request", "agent": "food", "food_task": label}
            for text, label in pairs
        ]
    }
    return FakeLLM(_tools(_call("s1", "route", output)))


def test_daycare_meal_은_쓰는_task_다() -> None:
    assert pipeline._WRITING_TASKS["food"] == frozenset({"daycare_meal"})
    # 실행 함수 표에 없는 agent를 쓰는 쪽으로 표시하면 배선이 어긋난 것
    assert set(pipeline._WRITING_TASKS) <= set(pipeline._RUNNERS)


async def test_쓰는_task_가_끝난_뒤에_읽는_task_가_시작한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []
    monkeypatch.setitem(
        pipeline._RUNNERS,
        "food",
        _by_type(
            daycare_meal=_runner("food", delay=0.01, log=log, name="갱신"),
            meal_recommendation=_runner("food", log=log, name="추천"),
        ),
    )

    # 쓰는 task가 뒤에 와도 먼저 돈다
    ran = await pipeline._run_domain((_DINNER, _MEAL), {"food": _FakeContext()}, timeout=1.0)

    assert log == ["갱신 시작", "갱신 끝", "추천 시작", "추천 끝"]
    # 결과는 끝난 순서가 아니라 task 순서
    assert [item.task_type for item in ran.outcomes] == ["meal_recommendation", "daycare_meal"]
    assert ran.partial is None


async def test_쓰는_task_가_죽어도_읽는_task_는_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        pipeline._RUNNERS,
        "food",
        _by_type(
            daycare_meal=_failing(RuntimeError("부서졌다")),
            meal_recommendation=_runner("food"),
        ),
    )

    ran = await pipeline._run_domain((_MEAL, _DINNER), {"food": _FakeContext()}, timeout=1.0)

    assert [item.task_type for item in ran.outcomes] == ["meal_recommendation"]
    assert ran.partial == Partial("agent_error", succeeded=("food",), failed=("food",))


async def test_쓰는_task_도_20초_안에서_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    monkeypatch.setitem(
        pipeline._RUNNERS,
        "food",
        _by_type(
            daycare_meal=_runner("food", delay=5, log=log, name="갱신"),
            meal_recommendation=_runner("food", log=log, name="추천"),
        ),
    )

    ran = await pipeline._run_domain((_MEAL, _DINNER), {"food": _FakeContext()}, timeout=0.1)

    # 쓰는 쪽이 제한을 다 써서 끊겼고, 읽는 쪽은 남은 몫이 없어 시작도 안 했다
    assert log == ["갱신 시작"]
    assert ran.outcomes == ()
    assert ran.partial == Partial("timeout_20s", succeeded=(), failed=("food",))


# 끝나는 대로 넘기기
_FOOD_TASK = DomainTask(run_id=RUN_ID, agent="food", task_type=None, request_texts=("x",))
_ACTIVITY_TASK = DomainTask(run_id=RUN_ID, agent="activity", task_type=None, request_texts=("y",))


async def test_읽는_task_결과는_끝나는_대로_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 늦은 쪽을 기다리지 않는다. 돌려주는 outcomes는 그대로 task 순서
    # food는 activity 결과가 넘어간 뒤에야 끝난다. 다 끝난 뒤 몰아서 넘기면 food가 끊긴다
    activity_passed = asyncio.Event()

    async def food_after_activity(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        await activity_passed.wait()
        return _Outcome("food", task.task_type)

    def record(outcome: Any) -> None:
        passed.append(outcome.agent)
        if outcome.agent == "activity":
            activity_passed.set()

    monkeypatch.setitem(pipeline._RUNNERS, "food", food_after_activity)
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity"))
    passed: list[str] = []

    ran = await pipeline._run_domain(
        (_FOOD_TASK, _ACTIVITY_TASK), _FAKE_CONTEXTS, timeout=1.0, on_outcome=record
    )

    assert passed == ["activity", "food"]  # 끝난 순서
    assert [item.agent for item in ran.outcomes] == ["food", "activity"]  # task 순서


async def test_쓰는_task_결과는_읽는_task_가_시작하기_전에_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []
    monkeypatch.setitem(
        pipeline._RUNNERS,
        "food",
        _by_type(
            daycare_meal=_runner("food", delay=0.01, log=log, name="갱신"),
            meal_recommendation=_runner("food", log=log, name="추천"),
        ),
    )

    await pipeline._run_domain(
        (_DINNER, _MEAL),
        {"food": _FakeContext()},
        timeout=1.0,
        on_outcome=lambda outcome: log.append(f"넘김 {outcome.task_type}"),
    )

    assert log == [
        "갱신 시작",
        "갱신 끝",
        "넘김 daycare_meal",
        "추천 시작",
        "추천 끝",
        "넘김 meal_recommendation",
    ]


async def test_같이_끝난_결과는_task_순서로_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 같은 틱에 끝나면 순서가 run 마다 흔들리지 않게 task 순서로 넘긴다
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food"))
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity"))
    passed: list[str] = []

    await pipeline._run_domain(
        (_FOOD_TASK, _ACTIVITY_TASK),
        _FAKE_CONTEXTS,
        timeout=1.0,
        on_outcome=lambda outcome: passed.append(outcome.agent),
    )

    assert passed == ["food", "activity"]


async def test_결과를_넘기다_터지면_숨기지_않고_남은_Agent_를_끊는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 넘기는 쪽은 화면 번역이다. 번역 버그를 부분 실패로 바꾸면 화면에서 조용히 빠진다
    log: list[str] = []
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food", delay=0.2, log=log))
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity", log=log))

    def broken(outcome: Any) -> None:
        raise ValueError("번역 실패")

    with pytest.raises(ValueError):
        await pipeline._run_domain(
            (_FOOD_TASK, _ACTIVITY_TASK), _FAKE_CONTEXTS, timeout=1.0, on_outcome=broken
        )
    await asyncio.sleep(0.3)

    assert "activity 끝" in log  # 먼저 끝난 쪽을 넘기다 터졌다
    assert "food 끝" not in log  # 남은 쪽은 뒤에서 이어 돌지 않았다


async def test_넘기다_터져도_같이_끝난_Agent_의_예외_메시지는_로그에_남지_않는다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # 같이 끝난 묶음에서 앞 결과를 넘기다 터지면 뒤 task 의 예외는 꺼내지 못한 채 남는다.
    # 안 꺼내면 asyncio가 GC 때 예외 메시지째 찍는다
    async def fail_late(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        await asyncio.sleep(0)  # activity 와 같은 틱에 끝나게
        raise RuntimeError("민준이 수영장")

    def broken(outcome: Any) -> None:
        raise ValueError("번역 실패")

    caplog.set_level(logging.ERROR)
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity"))
    monkeypatch.setitem(pipeline._RUNNERS, "food", fail_late)

    with pytest.raises(ValueError):
        await pipeline._run_domain(
            (_ACTIVITY_TASK, _FOOD_TASK), _FAKE_CONTEXTS, timeout=1.0, on_outcome=broken
        )
    await asyncio.sleep(0.05)
    gc.collect()  # 버려진 task 가 여기서 거둬진다

    assert "민준이" not in caplog.text


async def test_바깥에서_취소되면_도메인_Agent_도_같이_끊긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 러너의 60초 안전망이 run 을 끊는 경우다. gather 는 자식을 같이 취소했지만 wait 는 안 한다
    log: list[str] = []
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food", delay=0.2, log=log))

    running = asyncio.create_task(pipeline._run_domain((_FOOD_TASK,), _FAKE_CONTEXTS, timeout=1.0))
    await asyncio.sleep(0.05)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0.3)

    assert log == ["food 시작"]


async def test_RC16_먼저_끝난_Agent_의_결과가_먼저_나간다(
    two_agents: None,
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # food 는 activity 의 DomainRouted 가 나간 뒤에야 끝난다. 몰아서 내보내면 food가 끊긴다
    async def food_after_activity(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        while not _of(events, DomainRouted):
            await asyncio.sleep(0)
        return _Outcome("food", task.task_type)

    monkeypatch.setattr(pipeline, "RUN_DEADLINE_S", 1.0)  # 깨지면 20초를 돌지 않게
    monkeypatch.setitem(pipeline._RUNNERS, "food", food_after_activity)
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity"))

    result = await _handle(
        "RC16",
        memory_llm=_pool_memory(),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        contexts=_FAKE_CONTEXTS,
    )

    assert [event.agent for event in _of(events, DomainRouted)] == ["activity", "food"]
    assert [item.agent for item in result.domain] == ["food", "activity"]  # 결과는 task 순서
    assert _order(events)[-1] == "Done"


@pytest.mark.parametrize(
    ("food_runner", "reason"),
    [
        (_runner("food", delay=5), "timeout_20s"),
        (_failing(RuntimeError("부서졌다")), "agent_error"),
    ],
    ids=["시간_초과", "실패"],
)
async def test_202_먼저_끝난_추천이_나가고_빠진_쪽은_partial_이며_기록은_남는다(
    two_agents: None,
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
    food_runner: Any,
    reason: str,
) -> None:
    # 이슈 완료 기준. 음식·놀이 추천을 같이 돌렸고 놀이는 끝나고 음식은 20초를 넘기거나 죽는다.
    # 추천이 빠져도 기록은 commit 된 뒤라 done + partial 로 끝나고 관찰이 남는다
    monkeypatch.setattr(pipeline, "RUN_DEADLINE_S", 0.5)  # 앞 단계는 FakeLLM 이라 수 ms
    monkeypatch.setitem(pipeline._RUNNERS, "food", food_runner)
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity", delay=0.01))

    result = await _handle(
        "RC16",
        memory_llm=_pool_memory(),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        contexts=_FAKE_CONTEXTS,
        commit=_commit_into(events),
    )

    names = _order(events)
    assert (
        names.index("_Committed")
        < names.index("Saved")
        < names.index("DomainRouted")
        < names.index("Partial")
        < names.index("Done")
    )
    assert [event.agent for event in _of(events, DomainRouted)] == ["activity"]
    assert _of(events, Partial) == [Partial(reason, succeeded=("activity",), failed=("food",))]
    assert result.failed is None  # 끝 신호는 done
    assert result.committed is True
    rows = await memory_context.store.query_observations(domain="activity", child_id=CHILD)
    assert len(rows) == 1  # 추천이 빠져도 기록은 남는다


async def test_쓰는_task_가_죽어도_읽는_task_결과는_나가고_partial_은_맨_끝에_한_번(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    monkeypatch.setitem(
        pipeline._RUNNERS,
        "food",
        _by_type(
            daycare_meal=_failing(RuntimeError("부서졌다")),
            meal_recommendation=_runner("food"),
        ),
    )

    await handle_input(
        _MEAL_AND_DINNER_RAW,
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=_food_requests(
            ("급식 대신 두유 받았대", "daycare_meal"), ("저녁 뭐 먹일까?", "meal_recommendation")
        ),
        memory_client=FakeLLM(_reply("불리면 안 된다")),
        emit=events.append,
    )

    assert [event.task_type for event in _of(events, DomainRouted)] == ["meal_recommendation"]
    assert _order(events)[-3:] == ["DomainRouted", "Partial", "Done"]
    assert _order(events).count("Partial") == 1


async def test_급식을_고친_뒤의_저녁_추천은_고친_급식을_읽는다(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    seen: list[tuple[str, ...]] = []
    today = NOW.date()

    async def update_meal(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        await asyncio.sleep(0.01)  # 동시에 돌면 그 사이 추천이 먼저 읽는다
        (row,) = await context.ports.daycare.rows(child_id=CHILD, date_from=today, date_to=today)
        await context.ports.daycare.update(replace(row, menu_keys=("두유",)))
        return _Outcome("food", task.task_type)

    async def recommend(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        rows = await context.ports.daycare.rows(child_id=CHILD, date_from=today, date_to=today)
        seen.append(rows[0].menu_keys)
        return _Outcome("food", task.task_type)

    monkeypatch.setitem(
        pipeline._RUNNERS,
        "food",
        _by_type(daycare_meal=update_meal, meal_recommendation=recommend),
    )

    result = await handle_input(
        _MEAL_AND_DINNER_RAW,
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=_food_requests(
            ("급식 대신 두유 받았대", "daycare_meal"), ("저녁 뭐 먹일까?", "meal_recommendation")
        ),
        memory_client=FakeLLM(_reply("불리면 안 된다")),
        emit=events.append,
    )

    assert seen == [("두유",)]
    assert [item.task_type for item in result.domain] == ["daycare_meal", "meal_recommendation"]
    assert result.partial is None


_MEAL_ONLY = "급식 대신 두유 받았대"


def _recorded(food_context: FoodContext) -> tuple[FoodContext, RunWrites]:
    """entrypoint 와 같이 Food 쓰기 포트를 감싼다. 표시는 포트 호출이 성공한 뒤에만 남는다."""
    writes = RunWrites()
    return replace(food_context, ports=record_food_writes(food_context.ports, writes)), writes


async def _handle_meal_only(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any], writes: RunWrites
) -> pipeline.PipelineResult:
    return await handle_input(
        _MEAL_ONLY,
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=_food_requests((_MEAL_ONLY, "daycare_meal")),
        memory_client=FakeLLM(_reply("불리면 안 된다")),
        emit=events.append,
        writes=writes,
    )


async def test_추천_직전_영양_구간을_저장했어도_모델이_실패하면_failed_다(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 질문만 한 run이다. 영양 구간 · 메뉴 카탈로그는 계산이 남기는 값이라 보호자 말이 반영된 게
    # 아니고, 덮어쓰기라 다시 보내도 같다. 원문을 돌려줘야 다시 물을 수 있다
    raw = "저녁 뭐 먹일까?"

    async def save_band_then_die(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        await context.ports.bands.save(child_id=CHILD, bands={"iron": "low"})
        raise RuntimeError("모델 호출 실패")

    monkeypatch.setitem(pipeline._RUNNERS, "food", save_band_then_die)
    context, writes = _recorded(food_context)

    result = await handle_input(
        raw,
        memory_context,
        {"food": context},
        run_id=RUN_ID,
        supervisor_client=_food_requests((raw, "meal_recommendation")),
        memory_client=FakeLLM(_reply("불리면 안 된다")),
        emit=events.append,
        writes=writes,
    )

    assert result.committed is False
    assert result.failed == Failed("llm_unavailable", raw)
    assert _order(events)[-2:] == ["Failed", "Done"]


async def test_급식을_고친_뒤_죽으면_failed_가_아니라_partial_로_끝난다(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 급식은 이미 바뀌었다. failed로 끝내면 화면이 원문을 돌려주고 다시 보내게 한다
    today = NOW.date()

    async def update_then_die(task: DomainTask, context: Any, **kwargs: Any) -> _Outcome:
        (row,) = await context.ports.daycare.rows(child_id=CHILD, date_from=today, date_to=today)
        await context.ports.daycare.update(replace(row, menu_keys=("두유",)))
        raise RuntimeError("부서졌다")

    monkeypatch.setitem(pipeline._RUNNERS, "food", update_then_die)
    context, writes = _recorded(food_context)

    result = await _handle_meal_only(memory_context, context, events, writes)

    assert result.failed is None
    assert result.committed is True
    assert result.partial == Partial("agent_error", succeeded=(), failed=("food",))
    assert _order(events)[-2:] == ["Partial", "Done"]


async def test_쓰기_전에_모델_호출이_실패하면_commit_이_없어_failed_다(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 쓰는 task가 시작은 했지만 포트를 부르기 전에 죽었다. 원문을 돌려줘야 한다
    monkeypatch.setitem(pipeline._RUNNERS, "food", _failing(RuntimeError("모델 호출 실패")))
    context, writes = _recorded(food_context)

    result = await _handle_meal_only(memory_context, context, events, writes)

    assert result.committed is False
    assert result.failed == Failed("llm_unavailable", _MEAL_ONLY)
    assert _order(events)[-2:] == ["Failed", "Done"]


async def test_쓰기_전에_시간이_다_되면_commit_이_없어_failed_다(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 예산이 조금 남아 쓰는 task 가 시작했고, 모델 호출 중에 끊겼다. 포트는 불리지 않았다
    monkeypatch.setattr(pipeline, "RUN_DEADLINE_S", 0.05)
    log: list[str] = []
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food", delay=1, log=log))
    context, writes = _recorded(food_context)

    result = await _handle_meal_only(memory_context, context, events, writes)

    assert log == ["food 시작"]  # 시작은 했다
    assert result.committed is False
    assert result.failed == Failed("llm_unavailable", _MEAL_ONLY)


async def test_쓰는_task_가_시작도_못_하고_끊기면_commit_이_없어_failed_다(
    monkeypatch: pytest.MonkeyPatch,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 앞 단계가 제한을 다 썼다. 아무것도 안 썼으니 failed 로 끝내 원문을 돌려줘야 한다
    monkeypatch.setattr(pipeline, "RUN_DEADLINE_S", 0.0)
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food", delay=1))
    context, writes = _recorded(food_context)

    result = await _handle_meal_only(memory_context, context, events, writes)

    assert result.committed is False
    assert result.failed == Failed("llm_unavailable", _MEAL_ONLY)
    assert _order(events)[-2:] == ["Failed", "Done"]


# ── 되묻기 · pending ────────────────────────────────────────────
# RC04 = "오늘 2시에 모래놀이하고 떡볶이 먹었어." — OBSERVE 두 조각. 하나는 저장하고 하나는 되묻는다
# raw_text 가 조각 문장을 담아야 _bounced_hints 가 "적은 조각" 으로 본다
_SAND = {
    "raw_text": "오늘 2시에 모래놀이하고",
    "observed_on": "오늘",
    "subject": "모래놀이",
    "activity": "모래놀이",
}
_TTEOKBOKKI = {"raw_text": "떡볶이 먹었어", "observed_on": "오늘", "subject": "떡볶이"}


def _ask_second(case_id: str = "RC04") -> dict[str, Any]:
    second = answer_output(CASES_BY_ID[case_id]).segments[1].text
    return {"text": "무엇을 먹었어요?", "kind": "question", "pending_hint": second}


async def test_되묻기_run_은_note_와_pending_을_같이_낸다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    supervisor = _supervisor_llm("RC04")
    memory = FakeLLM(
        _tools(
            _call("m1", "create_observation_activity", _SAND),
        ),
        _answer(_ask_second()),
    )

    result = await handle_input(
        CASES_BY_ID["RC04"].text,
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=supervisor,
        memory_client=memory,
        emit=events.append,
    )

    note = next(event for event in events if isinstance(event, MemoryNote))
    pending = next(event for event in events if isinstance(event, PendingReply))
    done = next(event for event in events if isinstance(event, Done))
    assert note.kind == "question"
    assert pending.run_id == RUN_ID
    assert pending.context.hint_text == "떡볶이 먹었어"
    assert events.index(pending) < events.index(done)
    assert result.memory is not None and result.memory.pending is not None
    # 되묻는 중인 조각은 "위임이 어긋난 것" 이 아니다. 다시 나누지 않는다
    assert supervisor.calls == 1
    assert result.rerouted is None
    assert result.model_calls == 2


async def test_saved_가_note_보다_먼저_나간다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory = FakeLLM(
        _tools(
            _call("m1", "create_observation_activity", _SAND),
        ),
        _answer(_ask_second()),
    )

    await handle_input(
        CASES_BY_ID["RC04"].text,
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm("RC04"),
        memory_client=memory,
        emit=events.append,
    )

    names = [type(event).__name__ for event in events]
    assert names.index("Saved") < names.index("MemoryNote") < names.index("Done")


# ── 이어받기 ────────────────────────────────────────────────────
_COUGH_PENDING = PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE)


def _cont_answer(reply: dict[str, Any]) -> LLMResponse:
    """이어받기 run 의 마지막 말. 일반 스키마에 leftover 가 더 붙는다."""
    return _answer({"leftover": False, **reply})


async def test_이어받기는_supervisor_와_food_를_타지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    supervisor = FakeLLM()  # 부르면 안 된다
    memory = FakeLLM(
        _tools(
            _call("m1", "create_observation_health", _RASH),
        ),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message"}),
    )

    result = await handle_input(
        "3일 전부터",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=supervisor,
        memory_client=memory,
        emit=events.append,
        continuation=_COUGH_PENDING,
    )

    assert supervisor.calls == 0
    assert result.domain == ()
    assert result.model_calls == 1
    done = next(event for event in events if isinstance(event, Done))
    assert done.model_calls == 1
    assert not any(isinstance(event, DomainRouted) for event in events)
    rows = await memory_context.store.query_observations(domain="health", child_id=CHILD)
    assert len(rows) == 1


async def test_이어받기에서_memory_가_실패하면_failed_로_끝난다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    result = await handle_input(
        "3일 전부터",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=FakeLLM(LLMUnavailableError("down")),
        emit=events.append,
        continuation=_COUGH_PENDING,
    )

    assert result.failed is not None and result.failed.reason == "llm_unavailable"
    assert any(isinstance(event, Failed) for event in events)


async def test_이어받기에_다른_말이_섞이면_따로_보내라는_안내를_붙인다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 섞인 말은 처리하지 않는다. 처리한 것처럼 보이면 보호자가 다시 보내 두 번 저장된다
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_health", _RASH)),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message", "leftover": True}),
    )

    await handle_input(
        "3일 전부터. 그리고 오늘 수영장 다녀왔어",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=memory,
        emit=events.append,
        continuation=_COUGH_PENDING,
    )

    notes = _of(events, MemoryNote)
    assert [note.text for note in notes] == [f"기록해 둘게요. {pipeline.LEFTOVER_NOTE}"]
    assert notes[0].kind == "message"


async def test_다시_물을_때_붙인_안내는_다음_질문_맥락에_들어가지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory = FakeLLM(
        _cont_answer({"text": "정확히 며칠 전이에요?", "kind": "question", "leftover": True}),
    )

    await handle_input(
        "저녁 뭐 먹일까?",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=memory,
        emit=events.append,
        continuation=_COUGH_PENDING,
    )

    # 다시 묻는 중이면 화면이 다음 한 줄을 이 질문의 답으로 보낸다. "따로 보내 주세요" 만 있으면
    # 보호자가 섞인 말을 또 답으로 보내 같은 질문이 돌아온다
    note = _of(events, MemoryNote)[0]
    pending = _of(events, PendingReply)[0]
    assert note.kind == "question"
    assert note.text == f"정확히 며칠 전이에요? {pipeline.LEFTOVER_NOTE_QUESTION}"
    assert pending.context.question == "정확히 며칠 전이에요?"


async def test_섞인_말이_없으면_안내를_붙이지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_health", _RASH)),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message"}),
    )

    await handle_input(
        "3일 전부터",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=memory,
        emit=events.append,
        continuation=_COUGH_PENDING,
    )

    assert [note.text for note in _of(events, MemoryNote)] == ["기록해 둘게요."]


_LUNCH_PENDING = PendingMemoryContext("점심 먹었어", "점심에 무엇을 먹었어요?", WorkType.OBSERVE)


async def test_이어받기가_저장한_뒤_물으면_message_로_내리고_따로_보내라고_한다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 저장한 뒤라 맥락이 안 생긴다. question으로 내보내면 화면이 "이어서 적기" 를 열고,
    # 보호자가 그 질문에 답하면 reply_to 가 400 을 받는다
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_food", _TTEOKBOKKI)),
        _cont_answer({"text": "떡볶이를 기록했어요. 내일 소풍은 몇 시예요?", "kind": "question"}),
    )

    await handle_input(
        "떡볶이. 그리고 내일 소풍 있어",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=memory,
        emit=events.append,
        continuation=_LUNCH_PENDING,
    )

    note = _of(events, MemoryNote)[0]
    assert note.kind == "message"
    assert note.text == f"떡볶이를 기록했어요. 내일 소풍은 몇 시예요? {pipeline.NO_CONTEXT_NOTE}"
    assert not _of(events, PendingReply)


async def test_맥락_없이_물을_때_섞인_말이_있으면_안내를_한_번만_붙인다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # LEFTOVER_NOTE_QUESTION 은 "이 질문에 먼저 답한 뒤" 라서 답할 수 없는 질문에 붙으면 안 된다
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_food", _TTEOKBOKKI)),
        _cont_answer(
            {"text": "떡볶이를 기록했어요. 매웠나요?", "kind": "question", "leftover": True}
        ),
    )

    await handle_input(
        "떡볶이. 그리고 오늘 수영장 다녀왔어",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=memory,
        emit=events.append,
        continuation=_LUNCH_PENDING,
    )

    note = _of(events, MemoryNote)[0]
    assert note.kind == "message"
    assert note.text == f"떡볶이를 기록했어요. 매웠나요? {pipeline.LEFTOVER_NOTE_NO_CONTEXT}"
    assert not _of(events, PendingReply)


async def test_첫_run_에서도_맥락_없이_묻는_질문은_message_로_내린다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 이어 붙일 조각을 두 번 다 잘못 지목하면 맥락이 안 생긴다
    wrong = {"text": "무엇을 먹었어요?", "kind": "question", "pending_hint": "후보에 없는 문장"}
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_activity", _SAND)),
        _answer(wrong),
        _answer(wrong),
    )

    await handle_input(
        CASES_BY_ID["RC04"].text,
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm("RC04"),
        memory_client=memory,
        emit=events.append,
    )

    note = _of(events, MemoryNote)[0]
    assert note.kind == "message"
    assert note.text == f"무엇을 먹었어요? {pipeline.NO_CONTEXT_NOTE}"
    assert not _of(events, PendingReply)


async def test_leftover_여도_질문에_대한_답은_저장하고_done_으로_끝난다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 섞인 말을 남겼다고 답까지 실패로 치면 runner 가 맥락을 되돌리고, 같은 답이 두 번 저장된다
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_health", _RASH)),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message", "leftover": True}),
    )

    result = await handle_input(
        "그저께부터. 그리고 오늘 수영장 다녀왔어",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=memory,
        emit=events.append,
        continuation=_COUGH_PENDING,
    )

    rows = await memory_context.store.query_observations(domain="health", child_id=CHILD)
    assert len(rows) == 1
    assert [len(saved.refs) for saved in _of(events, Saved)] == [1]
    assert result.failed is None
    assert _order(events)[-1] == "Done"
    assert not _of(events, Failed)


async def test_이어받기는_앞_run_에서_저장한_관찰을_다시_저장하지_않는다(
    memory_context: AgentContext, food_context: FoodContext
) -> None:
    # 이슈의 "계란 잘 먹었어. 요즘 기침해" 를 RC04 로 옮긴 것 — 모래놀이는 저장, 떡볶이는 되묻기.
    # 두 run 이 같은 memory_context 를 써서 store 가 이어진다
    before = await handle_input(
        CASES_BY_ID["RC04"].text,
        memory_context,
        {"food": food_context},
        run_id="run-a",
        supervisor_client=_supervisor_llm("RC04"),
        memory_client=FakeLLM(
            _tools(
                _call("m1", "create_observation_activity", _SAND),
            ),
            _answer(_ask_second()),
        ),
    )
    assert before.memory is not None and before.memory.pending is not None

    after = await handle_input(
        "떡볶이 먹었어",
        memory_context,
        {"food": food_context},
        run_id="run-b",
        supervisor_client=FakeLLM(),
        memory_client=FakeLLM(
            _tools(
                _call("m3", "create_observation_food", _TTEOKBOKKI),
            ),
            _cont_answer({"text": "기록해 둘게요.", "kind": "message"}),
        ),
        continuation=before.memory.pending,
    )

    activity = await memory_context.store.query_observations(domain="activity", child_id=CHILD)
    food = await memory_context.store.query_observations(domain="food", child_id=CHILD)
    assert len(activity) == 1  # 2 면 실패다 — 이미 저장한 조각이 다시 저장된 것
    assert len(food) == 1
    assert after.model_calls == 1


async def test_이어받기에서_아무것도_못_하면_failed_로_끝난다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # done 으로 끝나면 runner 가 맥락을 되돌리지 않아 보호자의 답이 빈 성공으로 사라진다
    result = await handle_input(
        "3일 전부터",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=FakeLLM(_reply(""), _reply("")),  # 빈 턴 두 번
        emit=events.append,
        continuation=_COUGH_PENDING,
    )

    assert result.failed is not None and result.failed.reason == "unparsable"
    assert any(isinstance(event, Failed) for event in events)


@dataclass(frozen=True)
class _Committed:
    """가짜 commit 이 events 에 남기는 표시. 이벤트 사이에서 commit 시점을 본다."""


def _commit_into(events: list[Any]) -> Any:
    async def commit() -> None:
        events.append(_Committed())

    return commit


async def test_Memory_다음에_commit_하고_Saved_는_그_뒤에_나간다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", {**_STRAWBERRY, "polarity": 1})),
        _reply("딸기 기록 남겼어요."),
    )

    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        commit=_commit_into(events),
    )

    names = _order(events)
    assert names.count("_Committed") == 1
    assert (
        names.index("_Committed")
        < names.index("Saved")
        < names.index("MemoryNote")
        < names.index("DomainRouted")
    )
    assert result.committed is True


async def test_Memory_가_쓰다가_죽으면_commit_하지_않고_failed_로_끝난다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    # 한 건을 쓴 뒤 다음 호출에서 죽는다. 확정 전이라 러너가 되돌린다
    # (InMemory 는 되돌리지 못하므로 store 는 보지 않는다)
    memory_llm = FakeLLM(
        _tools(_call("a", "create_observation_food", {**_STRAWBERRY, "polarity": 1})),
        LLMUnavailableError("망"),
    )

    result = await _handle(
        "RC01",
        memory_llm=memory_llm,
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        commit=_commit_into(events),
    )

    assert not _of(events, _Committed)
    assert not _of(events, Saved)
    assert result.failed == Failed("llm_unavailable", CASES_BY_ID["RC01"].text)
    assert result.committed is False


async def test_commit_이_실패하면_저장_안내도_되묻기_맥락도_나가지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    async def broken() -> None:
        raise RuntimeError("연결 끊김")

    # RC04 는 하나를 저장하고 하나를 되묻는다 — 성공했다면 Saved · note · pending 이 다 나간다
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_activity", _SAND)), _answer(_ask_second())
    )

    with pytest.raises(RuntimeError):
        await handle_input(
            CASES_BY_ID["RC04"].text,
            memory_context,
            {"food": food_context},
            run_id=RUN_ID,
            supervisor_client=_supervisor_llm("RC04"),
            memory_client=memory,
            emit=events.append,
            commit=broken,
        )

    # 확정된 게 없다. 끝 신호는 러너가 failed 로 붙인다
    assert not _of(events, Saved)
    assert not _of(events, MemoryNote)
    assert not _of(events, PendingReply)
    assert not _of(events, Done)


async def test_기록할_조각이_없으면_commit_을_부르지_않는다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    result = await _handle(
        "RC20",
        memory_llm=FakeLLM(_reply("불리면 안 된다")),
        memory_context=memory_context,
        food_context=food_context,
        events=events,
        commit=_commit_into(events),
    )

    assert not _of(events, _Committed)
    assert result.committed is False


async def test_이어받기도_Memory_다음에_commit_하고_Saved_는_그_뒤에_나간다(
    memory_context: AgentContext, food_context: FoodContext, events: list[Any]
) -> None:
    memory = FakeLLM(
        _tools(_call("m1", "create_observation_health", _RASH)),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message"}),
    )

    result = await handle_input(
        "3일 전부터",
        memory_context,
        {"food": food_context},
        run_id=RUN_ID,
        supervisor_client=FakeLLM(),
        memory_client=memory,
        emit=events.append,
        continuation=_COUGH_PENDING,
        commit=_commit_into(events),
    )

    names = _order(events)
    assert names.count("_Committed") == 1
    assert names.index("_Committed") < names.index("Saved")
    assert result.committed is True
