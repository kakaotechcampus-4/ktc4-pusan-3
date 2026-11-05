"""요청과 같이 온 기록 조각을 저장하지 않았을 때의 안내는 코드 문구 하나다.

모델이 쓰면 매번 달라서("담당 Agent 가 답해 드릴게요" · "범위 밖이에요" · "기록할 내용이 없어요")
보호자가 무엇이 일어났는지 알기 어렵다. 저장했거나, 조회 · 수정이거나, 위험 물질 · 안전 거절이면
모델 문장을 그대로 낸다 (`pipeline._request_only`).
"""

from typing import Any

import pytest

from app.agents import pipeline
from app.agents.common.schemas.task import DomainTask
from app.agents.memory.agent import MemoryAgentResult, MemoryReply, ToolCallRecord
from app.agents.memory.schemas.task import MemoryHint, MemoryTask, WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.supervisor.routing import Routing
from tests.unit.agents.growth.test_growth_answer_resume import (
    NOW,
    REQUEST,
    asks_level,
    memory_says,
    of,
    run_events,
    saves,
)
from tests.unit.agents.growth.test_growth_wiring import FakeLLM, call

RAW = "오늘 그림 그렸어. 양치 혼자 하게 하고 싶어"
REQUEST_SEGMENT = {
    "text": REQUEST,
    "kind": "request",
    "agent": "growth",
    "growth_task": "routine_coaching",
}


def route(record_work: str = "observe", *, with_request: bool = True) -> FakeLLM:
    segments: list[dict[str, Any]] = [
        {"text": "오늘 그림 그렸어", "kind": "record", "work": record_work}
    ]
    if with_request:
        segments.append(REQUEST_SEGMENT)
    return FakeLLM(call("route", {"segments": segments}))


async def notes(memory: FakeLLM, **kwargs: Any) -> list[pipeline.MemoryNote]:
    events = await run_events(
        RAW,
        InMemoryStore(now=NOW),
        growth_llm=asks_level(),
        supervisor_llm=kwargs.pop("supervisor", route()),
        memory_llm=memory,
    )
    return of(events, pipeline.MemoryNote)


@pytest.mark.parametrize(
    "said",
    [
        "양치 지도는 담당 Agent가 따로 답해 드릴 거예요.",
        "기록할 내용이 없어서 저장하지 않았어요.",
        "그건 범위 밖이라 도와드리기 어려워요.",
    ],
)
async def test_요청과_같이_온_말을_저장하지_않으면_안내는_코드_문구_하나다(said: str) -> None:
    (note,) = await notes(FakeLLM(memory_says(said, answering=False)))

    assert (note.text, note.kind) == (pipeline.REQUEST_ONLY_NOTE, "message")


def test_저장한_run_은_Memory_문장_그대로다() -> None:
    """저장했으면 그 run 의 말은 저장 결과를 담는다. 코드 문구로 덮으면 저장했다는 말이 사라진다."""
    task = MemoryTask(RAW, hints=(MemoryHint("오늘 그림 그렸어", WorkType.OBSERVE),))
    routing = Routing(
        intent_type="mixed",
        memory_task=task,
        domain_tasks=(DomainTask("run-1", "growth", "routine_coaching", (REQUEST,)),),
    )
    written = ToolCallRecord(
        "create_observation_activity", {}, {"success": True, "resource": "observation_activity"}
    )
    reply = MemoryReply("그림 기록을 남겼어요.", "message")

    def memory(*calls: ToolCallRecord) -> MemoryAgentResult:
        return MemoryAgentResult(reply=reply, completed=True, steps=1, calls=list(calls))

    assert pipeline._request_only(memory(), routing) is True
    assert pipeline._request_only(memory(written), routing) is False


async def test_도메인_task_가_없으면_Memory_문장_그대로다() -> None:
    memory = FakeLLM(memory_says("저장할 내용이 없어요.", answering=False))

    (note,) = await notes(memory, supervisor=route(with_request=False))

    assert note.text == "저장할 내용이 없어요."


async def test_조회_수정_문장은_바꾸지_않는다() -> None:
    memory = FakeLLM(memory_says("그 기록은 지우지 않을게요.", answering=False))

    (note,) = await notes(memory, supervisor=route("lookup_edit"))

    assert note.text == "그 기록은 지우지 않을게요."


async def test_위험_물질_거절_문장은_바꾸지_않는다() -> None:
    memory = FakeLLM(
        saves("create_observation_food", subject="소주"),
        memory_says("기록하지 않았어요.", answering=False),
    )

    (note, *_) = await notes(memory)

    assert note.text == "기록하지 않았어요."


async def test_Memory_질문은_바꾸지_않는다() -> None:
    memory = FakeLLM(memory_says("언제 그렸어요?", "question", answering=False))

    (note,) = await notes(memory)

    assert (note.text, note.kind) == ("언제 그렸어요?", "question")


def test_맥락_없이_message_로_내려갈_Memory_질문은_바꾸지_않는다() -> None:
    """이어 붙일 조각을 못 골라 질문이 message 로 내려가도(NO_CONTEXT_NOTE) 묻던 말은 남는다."""
    task = MemoryTask(RAW, hints=(MemoryHint("오늘 그림 그렸어", WorkType.OBSERVE),))
    routing = Routing(
        intent_type="mixed",
        memory_task=task,
        domain_tasks=(DomainTask("run-1", "growth", "routine_coaching", (REQUEST,)),),
    )
    asked = MemoryAgentResult(
        reply=MemoryReply("언제 그랬어요?", "question"), completed=True, steps=1
    )

    assert pipeline._request_only(asked, routing) is False


async def test_저장을_시도했다_실패한_run_은_Memory_문장_그대로다() -> None:
    """시도하지 않은 것과 시도했다 못 한 것은 다르다. 실패 이유를 담은 말은 덮지 않는다."""
    memory = FakeLLM(
        call("create_observation_activity", {"raw_text": "오늘 그림 그렸어"}),
        memory_says("저장하지 못했어요. 다시 적어 주세요.", answering=False),
    )

    (note, *_) = await notes(memory)

    assert note.text == "저장하지 못했어요. 다시 적어 주세요."
