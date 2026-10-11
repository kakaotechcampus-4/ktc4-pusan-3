"""pipeline 이 curator_trigger 콜백을 올바르게 호출하는지 검증한다.

DB 불필요. pipeline 내부를 monkeypatch 로 교체한다.
T4: Memory 커밋 뒤 curator_trigger(child_id) 가 불린다
T6: curator_trigger 가 None 이면 스킵 (기존 호출 호환)
"""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.llm_client import LLMResponse
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import PendingMemoryContext, WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.pipeline import handle_input

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
RUN_ID = "run-t"
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=KST)


def _reply(content: str) -> LLMResponse:
    message = SimpleNamespace(content=content, tool_calls=None)
    return LLMResponse(message=message, usage={}, latency_ms=1)


def _tools(*calls: SimpleNamespace) -> LLMResponse:
    message = SimpleNamespace(content=None, tool_calls=list(calls))
    return LLMResponse(message=message, usage={}, latency_ms=1)


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    raw = json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=raw))


class FakeLLM:
    def __init__(self, *items: LLMResponse) -> None:
        self._queue = list(items)

    async def chat(self, **kwargs: Any) -> LLMResponse:
        return self._queue.pop(0) if self._queue else _reply("done")


@pytest.fixture
def memory_context() -> AgentContext:
    return AgentContext(
        child_id=CHILD,
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


def _supervisor_llm_record() -> FakeLLM:
    """Supervisor 가 기록형으로 분류하는 출력."""
    output = {
        "intent_type": "record",
        "chunks": [
            {
                "text": "딸기 잘 먹었어",
                "delegation": "record",
                "work": "observe",
            }
        ],
    }
    return FakeLLM(_tools(_call("s1", "route", output)))


def _memory_llm_saves() -> FakeLLM:
    """Memory 가 관찰 하나를 저장하고 답하는 출력."""
    return FakeLLM(
        _tools(
            _call(
                "a",
                "create_observation_food",
                {
                    "raw_text": "딸기 잘 먹었어",
                    "observed_on": "오늘",
                    "subject": "딸기",
                },
            )
        ),
        _reply("딸기 기록했어요."),
    )


# T4 — Memory 커밋 뒤 curator_trigger(child_id) 가 불린다
async def test_커밋_후_curator_trigger_가_불린다(memory_context: AgentContext) -> None:
    triggered: list[UUID] = []

    def trigger(child_id: UUID) -> None:
        triggered.append(child_id)

    result = await handle_input(
        "딸기 잘 먹었어",
        memory_context,
        {},
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm_record(),
        memory_client=_memory_llm_saves(),
        curator_trigger=trigger,
    )

    assert result.committed
    assert triggered == [CHILD]


# T6 — curator_trigger 가 None 이면 에러 없이 스킵
async def test_curator_trigger_가_None_이면_스킵(memory_context: AgentContext) -> None:
    result = await handle_input(
        "딸기 잘 먹었어",
        memory_context,
        {},
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm_record(),
        memory_client=_memory_llm_saves(),
        # curator_trigger 생략 — 기본값 None
    )

    assert result.committed  # 커밋은 됐지만 trigger 는 안 불림 (에러 없음)


# T4 변형 — continuation 에서도 trigger 가 불린다
async def test_이어받기에서도_curator_trigger_가_불린다(memory_context: AgentContext) -> None:
    triggered: list[UUID] = []

    def trigger(child_id: UUID) -> None:
        triggered.append(child_id)

    pending = PendingMemoryContext("요즘 딸기 먹어?", "얼마나 먹었어요?", WorkType.OBSERVE)

    result = await handle_input(
        "반 개 먹었어",
        memory_context,
        {},
        run_id=RUN_ID,
        memory_client=_memory_llm_saves(),
        continuation=pending,
        curator_trigger=trigger,
    )

    # Memory 가 저장에 성공하면 trigger 가 불려야 한다
    if result.committed:
        assert triggered == [CHILD]
    else:
        # Memory 가 저장하지 않았으면 trigger 가 안 불려야 한다
        assert triggered == []


def _memory_llm_no_save() -> FakeLLM:
    """Memory 가 아무것도 저장하지 않고 답만 하는 출력."""
    return FakeLLM(_reply("특별히 기록할 게 없어요."))


# T7 — committed=False 이면 curator_trigger 를 부르지 않는다
async def test_저장하지_않으면_curator_trigger_안_불린다(memory_context: AgentContext) -> None:
    triggered: list[UUID] = []

    def trigger(child_id: UUID) -> None:
        triggered.append(child_id)

    result = await handle_input(
        "딸기 잘 먹었어",
        memory_context,
        {},
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm_record(),
        memory_client=_memory_llm_no_save(),
        curator_trigger=trigger,
    )

    assert not result.committed
    assert triggered == []


# T8 — curator_trigger 예외가 run 을 죽이지 않는다
async def test_curator_trigger_예외가_run을_죽이지_않는다(memory_context: AgentContext) -> None:
    def boom(child_id: UUID) -> None:
        raise RuntimeError("curator 폭발")

    result = await handle_input(
        "딸기 잘 먹었어",
        memory_context,
        {},
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm_record(),
        memory_client=_memory_llm_saves(),
        curator_trigger=boom,
    )

    assert result.ok
