"""handle_input() 검증. 가짜 LLM 2개 + 실제 Food mock 으로 돈다 (API 호출 0).

Supervisor 출력은 routing_cases 의 정답(RC01 등)을 그대로 쓴다 — 조각을 손으로 적지 않는다.
일부러 틀리게 나눈 출력은 재분기 테스트에서만 손으로 적는다.
여기서 보는 것:
  순서    Memory 가 끝난 뒤에 Food 가 불린다 (루트 §4 — 저장이 검색보다 먼저)
  전달    Food 는 food 로 온 REQUEST 조각과 식이 단계만 받는다 (S10)
  실패    Supervisor 실패는 강등, Memory 실패는 failed(llm_unavailable)
  재분기  Memory 가 적지 않은 조각을 Supervisor 에게 돌려주고 한 번만 다시 나눈다
  이벤트  step · saved · guidance · food_routed · rerouted · failed · done
"""

import json
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents import pipeline
from app.agents.common.llm_client import LLMResponse, LLMUnavailableError
from app.agents.food.context import FoodContext
from app.agents.food.schemas.common import FeedingStage, FoodTaskType
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore
from app.agents.pipeline import (
    Done,
    Failed,
    FoodRouted,
    MemoryNote,
    Rerouted,
    Saved,
    Step,
    Unavailable,
    Unwritten,
    handle_input,
)
from app.agents.supervisor.routing import Guidance
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


def _food_context(stage: FeedingStage = FeedingStage.TODDLER) -> FoodContext:
    return FoodContext(
        child_id=CHILD,
        now=NOW,
        timezone=KST,
        stage=stage,
        memory=None,  # type: ignore[arg-type]
        profile=None,  # type: ignore[arg-type]
        safety=None,  # type: ignore[arg-type]
        menu=None,  # type: ignore[arg-type]
        nutrition=None,  # type: ignore[arg-type]
    )


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
    real = pipeline.run_food

    async def recording(task: Any, context: Any, **kwargs: Any) -> Any:
        rows = await memory_context.store.query_observations(domain="food", child_id=CHILD)
        seen.append(len(rows))
        return await real(task, context, **kwargs)

    monkeypatch.setattr(pipeline, "run_food", recording)
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
) -> pipeline.PipelineResult:
    return await handle_input(
        CASES_BY_ID[case_id].text,
        memory_context,
        food_context,
        run_id=RUN_ID,
        supervisor_client=supervisor_llm or _supervisor_llm(case_id),
        memory_client=memory_llm,
        emit=events.append,
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
        "FoodRouted",
        "Done",
    ]
    assert saved_when_food_ran == [1]  # Food 를 부를 때 이미 저장돼 있었다 (루트 §4)
    assert [(step.index, step.total) for step in _of(events, Step)] == [(1, 3), (2, 3), (3, 3)]
    assert [ref.kind for ref in _of(events, Saved)[0].refs] == ["observation_food"]
    assert result.failed is None
    assert result.model_calls == 3  # Supervisor 1 + Memory 2 + Food mock 0 (S8)
    assert _of(events, Done)[0] == Done(RUN_ID, 3)


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

    task = result.routing.food_tasks[0]
    assert task.request_texts == ("저녁에는 뭐 먹이는 게 좋을까?",)
    routed = _of(events, FoodRouted)[0]
    assert routed.task_type == FoodTaskType.MEAL_RECOMMENDATION
    assert (routed.stage, routed.status) == (FeedingStage.TODDLER, "mock")
    assert len(routed.tools) == 5  # 식단 추천 × 유아기
    assert routed.requires_safety_check is True  # health_safety 사전 확인 대상 (S6)


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
    routed = _of(events, FoodRouted)[0]
    assert routed.task_type == FoodTaskType.NUTRIENT_ANALYSIS
    assert len(routed.tools) == 7  # 영양소 분석 × 유아기
    assert routed.requires_safety_check is False
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
        food_context=_food_context(FeedingStage.INFANT),
        events=events,
    )

    routed = _of(events, FoodRouted)[0]
    assert routed.status == "unsupported_stage"
    assert routed.tools == ()
    assert result.food[0].model_calls == 0


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
    assert len(result.food) == 1  # Food 는 그대로 돈다
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
    assert len(result.food) == 1  # food 만 실제로 돌았다
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
    assert result.food == ()  # 어디로 보낼지 모르니 Food 는 부르지 않는다
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

    assert _of(events, Rerouted) == [Rerouted(1, ("meal_recommendation",))]
    assert [food.task_type for food in result.food] == [FoodTaskType.MEAL_RECOMMENDATION]
    assert result.routing.food_tasks[0].request_texts == ("저녁에는 뭘 먹이면 좋을까?",)
    assert result.rerouted is not None
    # Memory 는 다시 돌리지 않았다 — 간식은 한 번만 저장됐다
    assert memory_llm.calls == 2
    rows = await memory_context.store.query_observations(domain="food", child_id=CHILD)
    assert [row.fields["subject"] for row in rows] == ["딸기케이크"]
    assert _order(events).index("Rerouted") < _order(events).index("FoodRouted")
    assert result.model_calls == 4  # Supervisor 2 + Memory 2 — 다시 나눈 run 의 예산은 4


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
    assert result.food == ()
    assert result.failed is None  # 간식은 저장됐다
    assert result.model_calls == 4  # 실패한 재시도도 호출은 호출이다


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


async def test_호출_예산을_넘으면_경고를_남긴다(
    caplog: pytest.LogCaptureFixture,
    memory_context: AgentContext,
    food_context: FoodContext,
    events: list[Any],
) -> None:
    # 계약서 — done.model_calls 가 3 을 넘으면 서버 알람 (NF-01 · S8)
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

    assert result.model_calls == 5  # Supervisor 1 + Memory 4
    assert "model_calls 예산 초과" in caplog.text
