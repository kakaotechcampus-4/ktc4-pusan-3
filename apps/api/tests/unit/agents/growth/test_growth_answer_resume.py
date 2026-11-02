"""루틴 되묻기에 답하면 같은 요청을 이어서 돈다 — 질문 → 답 → 추천.

되묻기가 화면 안내로만 끝나면 답이 맥락 없는 새 입력이 된다. "아직 내가 다 해줘" 는 무엇에 대한
말인지 몰라 버려지고, 저장돼도 추천이 돌지 않아 다시 요청하면 같은 질문이 되풀이됐다.
"""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents import entrypoint, pipeline
from app.agents.common.datetime_rules import build_observed_range
from app.agents.common.llm_client import LLMResponse
from app.agents.common.result import DomainPending
from app.agents.common.schemas.task import DomainTask
from app.agents.growth.readouts import (
    ASK_HABIT_TRIGGER,
    ASK_ROUTINE_CURRENT,
    LEVEL_UNREAD,
    READOUTS,
)
from app.agents.growth.store.inmemory import InMemoryGrowthDocs
from app.agents.growth.tools.routine import ask_question
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import PendingMemoryContext, WorkType
from app.agents.memory.store import InMemoryStore
from tests.unit.agents.growth.support import fixture_docs
from tests.unit.agents.growth.test_growth_wiring import (
    FakeLLM,
    ReadsToolResult,
    call,
    route,
)
from tests.unit.agents.growth.test_growth_wiring import contexts as built_contexts

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 15, 10, 0, tzinfo=KST)
TODAY = NOW.date()
CHILD = UUID(int=1)
PARENT = UUID(int=2)

REQUEST = "양치 혼자 하게 하고 싶어"
QUESTION = ask_question(ASK_ROUTINE_CURRENT, "양치")
PENDING = DomainPending(
    "growth",
    ASK_ROUTINE_CURRENT,
    QUESTION,
    {"subject": "양치", "category": "self_care", "field": "assistance_level"},
)
RESUME = DomainTask("run-1", "growth", "routine_coaching", (REQUEST,))
ASKED = PendingMemoryContext(REQUEST, QUESTION, WorkType.OBSERVE, resume=RESUME, asked=PENDING)

HABIT_REQUEST = "손톱 물어뜯는 걸 고치고 싶어"
HABIT_QUESTION = ask_question(ASK_HABIT_TRIGGER, "손톱 물어뜯기")
HABIT_ASKED = PendingMemoryContext(
    HABIT_REQUEST,
    HABIT_QUESTION,
    WorkType.OBSERVE,
    resume=DomainTask("run-1", "growth", "routine_coaching", (HABIT_REQUEST,)),
    asked=DomainPending(
        "growth",
        ASK_HABIT_TRIGGER,
        HABIT_QUESTION,
        {"subject": "손톱 물어뜯기", "category": "habit", "field": "trigger"},
    ),
)


@pytest.fixture(autouse=True)
def _fixture_docs(monkeypatch):
    monkeypatch.setattr(InMemoryGrowthDocs, "from_seed", classmethod(lambda cls: fixture_docs()))


async def _no_commit() -> None:
    return None


def asks_level() -> FakeLLM:
    """관찰이 없어 모델이 단계를 비워 낸다 — 코드가 되묻는다."""
    return FakeLLM(
        call("search_routine_memory", {}),
        call("propose_routine_plan", {"category": "self_care", "subject": "양치", "items": []}),
    )


def asks_habit() -> FakeLLM:
    return FakeLLM(
        call("search_routine_memory", {}),
        call(
            "propose_routine_plan",
            {"category": "habit", "subject": "손톱 물어뜯기", "items": []},
        ),
    )


def memory_says(text: str, kind: str = "message", *, answering: bool = True) -> LLMResponse:
    """Memory 의 마지막 말. 이어받기 스키마에만 leftover 칸이 있다."""
    body: dict[str, Any] = {"pending_hint": None, "text": text, "kind": kind}
    if answering:
        body["leftover"] = False
    return LLMResponse(
        message=SimpleNamespace(content=json.dumps(body, ensure_ascii=False), tool_calls=None),
        usage={},
        latency_ms=1,
    )


def saves(tool: str, **fields: Any) -> LLMResponse:
    return call(
        tool,
        {
            "raw_text": "칫솔질은 혼자 해",
            "observed_on": "오늘",
            "temporal_direction": "past",
            **fields,
        },
    )


def saves_routine(subject: str = "양치하기", **fields: Any) -> LLMResponse:
    return saves(
        "create_observation_routine", subject=subject, routine_category="self_care", **fields
    )


async def run_events(
    raw: str,
    store: InMemoryStore,
    *,
    growth_llm: Any,
    memory_llm: Any | None = None,
    supervisor_llm: Any | None = None,
    continuation: PendingMemoryContext | None = None,
) -> list[Any]:
    events: list[Any] = []
    await pipeline.handle_input(
        raw,
        AgentContext(child_id=CHILD, source_writer=PARENT, now=NOW, timezone=KST, store=store),
        built_contexts(store),
        run_id="run-2" if continuation is not None else "run-1",
        supervisor_client=supervisor_llm or FakeLLM(),
        memory_client=memory_llm or FakeLLM(),
        domain_clients={"growth": growth_llm},
        emit=events.append,
        continuation=continuation,
        commit=_no_commit,
        save_suggestions=entrypoint.InMemorySuggestions(),
    )
    return events


def of(events: list[Any], kind: type) -> list[Any]:
    return [event for event in events if isinstance(event, kind)]


MIXED = "오늘 그림 그렸어. 양치 혼자 하게 하고 싶어"
MIXED_SEGMENTS = [
    {"text": "오늘 그림 그렸어", "kind": "record", "work": "observe"},
    {"text": REQUEST, "kind": "request", "agent": "growth", "growth_task": "routine_coaching"},
]


async def test_루틴_되묻기는_답을_받는_질문으로_나가고_요청을_맥락에_남긴다():
    events = await run_events(
        REQUEST,
        InMemoryStore(now=NOW),
        growth_llm=asks_level(),
        supervisor_llm=route(REQUEST, "routine_coaching"),
    )

    (question,) = of(events, pipeline.AgentQuestion)
    (pending,) = of(events, pipeline.PendingReply)
    assert (question.text, question.awaits_reply) == (QUESTION, True)
    # 맥락이 먼저 들어가야 질문 카드가 보일 때 답(reply_to)이 400 이 아니다
    assert events.index(pending) < events.index(question)
    assert of(events, pipeline.MemoryNote) == []
    assert pending.run_id == "run-1"
    assert pending.context.question == QUESTION
    assert pending.context.resume.task_type == "routine_coaching"
    assert pending.context.resume.request_texts == (REQUEST,)
    assert pending.context.asked == PENDING


async def test_Memory_가_말한_run_도_답을_받는_질문은_따로_나간다():
    """기록 + 요청으로 나뉜 입력도 답할 수 있다. Memory 의 말은 따로 두고 잇지 않는다."""
    said = "그림 기록은 잘 받았어요."  # 저장하지 않았으니 코드 문구로 바뀐다 (Task 8)

    events = await run_events(
        MIXED,
        InMemoryStore(now=NOW),
        growth_llm=asks_level(),
        supervisor_llm=FakeLLM(call("route", {"segments": MIXED_SEGMENTS})),
        memory_llm=FakeLLM(memory_says(said, answering=False)),
    )

    assert [note.text for note in of(events, pipeline.MemoryNote)] == [pipeline.REQUEST_ONLY_NOTE]
    (question,) = of(events, pipeline.AgentQuestion)
    assert question.awaits_reply is True
    (pending,) = of(events, pipeline.PendingReply)
    assert pending.context.question == QUESTION  # 맥락에는 질문만 싣는다


async def test_이어받을_맥락에_기록_조각_원문은_없다():
    """처음 발화의 기록 조각까지 15분 메모리에 두면 맥락이 발화 원문 전체가 된다."""
    events = await run_events(
        MIXED,
        InMemoryStore(now=NOW),
        growth_llm=asks_level(),
        supervisor_llm=FakeLLM(call("route", {"segments": MIXED_SEGMENTS})),
        memory_llm=FakeLLM(memory_says("그림 기록은 잘 받았어요.", answering=False)),
    )

    (pending,) = of(events, pipeline.PendingReply)
    assert pending.context.resume.record_texts == ()
    assert pending.context.resume.recorded_subjects == ()
    assert pending.context.hint_text == REQUEST


async def test_Memory_도_되물었으면_질문_이벤트도_Growth_맥락도_없다():
    events = await run_events(
        MIXED,
        InMemoryStore(now=NOW),
        growth_llm=asks_level(),
        supervisor_llm=FakeLLM(call("route", {"segments": MIXED_SEGMENTS})),
        memory_llm=FakeLLM(memory_says("언제 그렸어요?", "question", answering=False)),
    )

    (pending,) = of(events, pipeline.PendingReply)
    (note,) = of(events, pipeline.MemoryNote)
    assert of(events, pipeline.AgentQuestion) == []
    assert note.kind == "question"
    assert pending.context.question == "언제 그렸어요?"
    assert pending.context.resume is None


async def test_답을_받는_질문은_05_빈_안내를_남기지_않는다():
    """남기면 05 가 24시간 동안 처음 run 의 "0개 안내" 를 다시 낸다."""
    events = await run_events(
        REQUEST,
        InMemoryStore(now=NOW),
        growth_llm=asks_level(),
        supervisor_llm=route(REQUEST, "routine_coaching"),
    )

    assert of(events, pipeline.EmptyResult) == []


async def test_답을_저장하면_Growth_가_이어서_돌아_추천을_낸다():
    events = await run_events(
        "칫솔질은 혼자 해",
        InMemoryStore(now=NOW),
        growth_llm=ReadsToolResult(),
        memory_llm=FakeLLM(
            saves_routine(assistance_level="partial_assist"), memory_says("기록해 둘게요.")
        ),
        continuation=ASKED,
    )

    (card,) = of(events, pipeline.AgentResult)
    assert (card.status, len(card.suggestion_ids)) == ("completed", 1)
    assert card.kind == "personalized"
    assert of(events, pipeline.PendingReply) == []
    assert of(events, pipeline.AgentQuestion) == []
    assert isinstance(events[-1], pipeline.Done)


async def test_이어서_도는_run_은_진행_단계가_둘이다():
    events = await run_events(
        "칫솔질은 혼자 해",
        InMemoryStore(now=NOW),
        growth_llm=ReadsToolResult(),
        memory_llm=FakeLLM(
            saves_routine(assistance_level="partial_assist"), memory_says("기록해 둘게요.")
        ),
        continuation=ASKED,
    )

    assert [(step.index, step.total) for step in of(events, pipeline.Step)] == [(1, 2), (2, 2)]


async def test_답에서_수준을_못_읽으면_다시_묻지_않고_안내로_닫는다():
    """수준도 수행 결과도 없는 답이다. 수준을 짚지 않고 같은 질문도 되풀이하지 않는다."""
    events = await run_events(
        "그럭저럭 해",
        InMemoryStore(now=NOW),
        growth_llm=asks_level(),
        memory_llm=FakeLLM(saves_routine(), memory_says("기록해 둘게요.")),
        continuation=ASKED,
    )

    (card,) = of(events, pipeline.AgentResult)
    assert card.status == "blocked"
    assert [r.body for r in card.readouts] == [READOUTS.get(LEVEL_UNREAD).template]
    assert card.suggestion_ids == ()
    assert of(events, pipeline.AgentQuestion) == []
    assert of(events, pipeline.PendingReply) == []


async def test_답을_저장하지_못했으면_Growth_를_다시_부르지_않는다():
    growth = ReadsToolResult()

    events = await run_events(
        "글쎄",
        InMemoryStore(now=NOW),
        growth_llm=growth,
        memory_llm=FakeLLM(memory_says("적어 주신 내용을 확인했어요.")),
        continuation=ASKED,
    )

    assert growth.calls == 0
    assert of(events, pipeline.AgentResult) == []


async def test_Memory_가_또_물으면_다음_답에도_이어서_돌_맥락이_남는다():
    growth = ReadsToolResult()
    asked = memory_says("칫솔은 아이가 쥐나요?", "question")
    memory = FakeLLM(asked, asked)

    events = await run_events(
        "글쎄",
        InMemoryStore(now=NOW),
        growth_llm=growth,
        # 한 번은 저장하라고 되돌린다 — 그래도 물으면 맥락을 잃지 않는다
        memory_llm=memory,
        continuation=ASKED,
    )

    (pending,) = of(events, pipeline.PendingReply)
    assert pending.context.resume == RESUME
    assert pending.context.asked == PENDING
    assert growth.calls == 0
    assert memory.calls == 2


async def test_저장한_뒤_Memory_가_또_물어도_질문은_나가지_않고_Growth_가_이어_돈다():
    """답할 수 없는 질문과 추천이 같이 나가지 않는다 — 한 요청에 질문은 하나다."""
    memory = FakeLLM(
        saves_routine(assistance_level="partial_assist"),
        memory_says("어금니는 어느 정도 도와주나요?", "question"),
    )

    events = await run_events(
        "칫솔질은 하는데 어금니를 싫어해",
        InMemoryStore(now=NOW),
        growth_llm=ReadsToolResult(),
        memory_llm=memory,
        continuation=ASKED,
    )

    (card,) = of(events, pipeline.AgentResult)
    assert len(card.suggestion_ids) == 1
    assert of(events, pipeline.MemoryNote) == []
    assert of(events, pipeline.AgentQuestion) == []
    assert of(events, pipeline.PendingReply) == []


async def test_이어받기에서_Growth_가_또_물어도_맥락을_다시_만들지_않는다():
    """습관은 상황을 따로 묻는다. 답을 받아 다시 돈 run 이 또 물으면 끝없이 이어진다 — 질문은
    문장으로만 남긴다."""
    events = await run_events(
        "하루 종일 틈만 나면 해요",
        InMemoryStore(now=NOW),
        growth_llm=asks_habit(),
        memory_llm=FakeLLM(
            saves(
                "create_observation_routine",
                subject="손톱 물어뜯기",
                routine_category="habit",
            ),
            memory_says("기록해 둘게요."),
        ),
        continuation=HABIT_ASKED,
    )

    (question,) = of(events, pipeline.AgentQuestion)
    assert question.awaits_reply is False
    assert of(events, pipeline.PendingReply) == []


async def test_답의_글자도_Growth_닫힘_판정이_본다():
    """요청에 없던 약이 답에서 나오면 추천을 만들지 않는다 — 의료 처치는 코드가 닫는다."""
    growth = ReadsToolResult()

    events = await run_events(
        "약을 먹여 주면 혼자 닦아",
        InMemoryStore(now=NOW),
        growth_llm=growth,
        memory_llm=FakeLLM(saves_routine(), memory_says("기록해 둘게요.")),
        continuation=ASKED,
    )

    (card,) = of(events, pipeline.AgentResult)
    assert card.status == "blocked"
    assert growth.calls == 0


async def test_일부만_한_기록이_있으면_다시_요청했을_때_또_묻지_않는다():
    """답이 다른 이름으로 수준 없이 저장돼도 같은 질문을 되풀이하지 않는다."""
    store = InMemoryStore(now=NOW)
    await store.create_observation(
        domain="routine",
        child_id=CHILD,
        source_writer=PARENT,
        raw_text="앞니만 닦고 말아",
        observed_on=TODAY,
        observed_range=build_observed_range(TODAY),
        fields={
            "subject": "칫솔질하기",
            "routine_category": "self_care",
            "polarity": 0,
            "completion_status": "partial",
        },
    )
    raw = "양치 지도방법을 알려줘"

    events = await run_events(
        raw,
        store,
        growth_llm=ReadsToolResult(),
        supervisor_llm=route(raw, "routine_coaching"),
    )

    (card,) = of(events, pipeline.AgentResult)
    assert len(card.suggestion_ids) == 1
    assert of(events, pipeline.AgentQuestion) == []
    assert of(events, pipeline.MemoryNote) == []


class SeesMessages(FakeLLM):
    """받은 대화를 남기는 Memory 가짜."""

    def __init__(self, *responses: LLMResponse) -> None:
        super().__init__(*responses)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.seen.append(list(messages))
        return await super().chat(messages, **kwargs)


async def stored_routine(store: InMemoryStore) -> list[Any]:
    return await store.query_observations(domain="routine", child_id=CHILD)


async def test_Memory_는_물은_행동과_칸을_받는다():
    """일반 이어받기 머리말은 "새 답만 따로 저장하지 않는다" 라 답 저장과 부딪힌다."""
    memory = SeesMessages(saves_routine(), memory_says("기록해 둘게요."))

    await run_events(
        "칫솔질은 혼자 해",
        InMemoryStore(now=NOW),
        growth_llm=ReadsToolResult(),
        memory_llm=memory,
        continuation=ASKED,
    )

    message = memory.seen[0][-1]["content"]
    assert "[물은 행동]" + chr(10) + "양치" in message
    assert "[물은 칸]" + chr(10) + "assistance_level" in message
    assert "새 답만 따로 관찰로 저장하지 않는다" not in message
    assert "[아직 저장하지 못한 조각]" not in message
    assert QUESTION in message and REQUEST in message


async def test_모델이_행동_이름을_바꿔도_물은_행동으로_저장한다():
    """모델이 "손 잡기" 로 적으면 다음 요청이 사슬을 못 찾는다. 이름은 코드가 정한다."""
    store = InMemoryStore(now=NOW)

    await run_events(
        "손 잡아줘야 겨우 해",
        store,
        growth_llm=ReadsToolResult(),
        memory_llm=FakeLLM(
            saves_routine(subject="손 잡기", assistance_level="full_assist"),
            memory_says("기록해 둘게요."),
        ),
        continuation=ASKED,
    )

    (row,) = await stored_routine(store)
    assert (row.fields["subject"], row.fields["routine_category"]) == ("양치", "self_care")


async def test_위험_물질_subject_는_고정하기_전에_거절한다():
    store = InMemoryStore(now=NOW)

    events = await run_events(
        "소주 한 모금 마시게 해",
        store,
        growth_llm=ReadsToolResult(),
        memory_llm=FakeLLM(saves_routine(subject="소주"), memory_says("기록하지 않았어요.")),
        continuation=ASKED,
    )

    assert await stored_routine(store) == []
    assert of(events, pipeline.AgentResult) == []


async def test_일반_이어받기는_저장_칸을_덮어쓰지_않는다():
    store = InMemoryStore(now=NOW)
    general = PendingMemoryContext(REQUEST, QUESTION, WorkType.OBSERVE)

    await run_events(
        "칫솔질은 혼자 해",
        store,
        growth_llm=ReadsToolResult(),
        memory_llm=FakeLLM(saves_routine(subject="손 잡기"), memory_says("기록해 둘게요.")),
        continuation=general,
    )

    (row,) = await stored_routine(store)
    assert row.fields["subject"] == "손 잡기"


async def test_Memory_가_다시_물으려_하면_한_번_되돌려_답을_저장하게_한다():
    """한 요청에 질문은 하나다 — Growth 가 이미 물었다 (루트 §2)."""
    memory = SeesMessages(
        memory_says("어금니는 어느 정도 도와주나요?", "question"),
        saves_routine(),
        memory_says("기록해 둘게요."),
    )

    events = await run_events(
        "칫솔질은 하는데 어금니를 싫어해",
        InMemoryStore(now=NOW),
        growth_llm=asks_level(),
        memory_llm=memory,
        continuation=ASKED,
    )

    assert "되묻지 않는다" in memory.seen[1][-1]["content"]
    assert [note.kind for note in of(events, pipeline.MemoryNote)] == ["message"]
    (card,) = of(events, pipeline.AgentResult)
    assert card.status == "blocked"  # 수준이 없어 안내로 닫혔다. 요점은 저장했다는 것이다


async def test_저장_없이_말로_끝낸_이어받기에는_이어받기용_안내가_간다():
    """일반 안내의 "필요한 값이 없으면 그 값을 묻는다" 는 다시 묻지 않는다는 규칙과 부딪친다."""
    memory = SeesMessages(
        memory_says("기록해 둘게요."), memory_says("적어 주신 내용을 확인했어요.")
    )
    growth = ReadsToolResult()

    events = await run_events(
        "글쎄",
        InMemoryStore(now=NOW),
        growth_llm=growth,
        memory_llm=memory,
        continuation=ASKED,
    )

    nudge = memory.seen[1][-1]["content"]
    assert "되묻지 않는다" in nudge
    assert "필요한 값이 없으면 그 값을 묻는다" not in nudge
    assert growth.calls == 0  # 그래도 저장이 없으면 Growth 는 다시 돌지 않는다
    assert of(events, pipeline.AgentResult) == []


async def test_답에_다른_기록이_섞이면_따로_보내_달라는_안내가_붙는다():
    """섞인 말을 말없이 버리면 보호자는 알레르기 언급까지 기록됐다고 생각한다."""
    mixed = LLMResponse(
        message=SimpleNamespace(
            content=json.dumps(
                {
                    "pending_hint": None,
                    "leftover": True,
                    "text": "기록해 둘게요.",
                    "kind": "message",
                },
                ensure_ascii=False,
            ),
            tool_calls=None,
        ),
        usage={},
        latency_ms=1,
    )

    events = await run_events(
        "칫솔질은 혼자 해. 어제 당근을 처음 먹었어",
        InMemoryStore(now=NOW),
        growth_llm=ReadsToolResult(),
        memory_llm=FakeLLM(saves_routine(assistance_level="partial_assist"), mixed),
        continuation=ASKED,
    )

    (note,) = of(events, pipeline.MemoryNote)
    assert note.text == f"기록해 둘게요. {pipeline.LEFTOVER_NOTE}"


class SlowGrowth:
    """마감보다 오래 걸리는 Growth 모델 가짜."""

    async def chat(self, messages, **kwargs) -> LLMResponse:
        await asyncio.sleep(1)
        return call("search_routine_memory", {})


async def test_재실행도_입력_시각부터_센_20초_안에서_돈다(monkeypatch):
    """남은 시간이 모자라 Growth 가 못 돌아도 저장한 답은 그대로고 부분 결과로 끝난다."""
    monkeypatch.setattr(pipeline, "RUN_DEADLINE_S", 0.05)
    store = InMemoryStore(now=NOW)

    events = await run_events(
        "칫솔질은 혼자 해",
        store,
        growth_llm=SlowGrowth(),
        memory_llm=FakeLLM(
            saves_routine(assistance_level="partial_assist"), memory_says("기록해 둘게요.")
        ),
        continuation=ASKED,
    )

    (partial,) = of(events, pipeline.Partial)
    assert partial.reason == "timeout_20s"
    assert of(events, pipeline.Saved) != []
    assert len(await stored_routine(store)) == 1
    assert isinstance(events[-1], pipeline.Done)


class RecordsModelInput(ReadsToolResult):
    """Growth 모델이 받은 system · user 메시지를 남기는 가짜."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.sent += [str(m.get("content") or "") for m in messages if m["role"] != "tool"]
        return await super().chat(messages, **kwargs)


async def test_답_원문은_Growth_모델에_보내지_않는다():
    """Memory 가 저장하지 않은 말까지 외부 모델로 가지 않는다. 저장한 기록은 조회로 읽는다."""
    growth = RecordsModelInput()

    events = await run_events(
        "칫솔질은 혼자 해. 어제 당근을 처음 먹었어",
        InMemoryStore(now=NOW),
        growth_llm=growth,
        memory_llm=FakeLLM(
            saves_routine(assistance_level="partial_assist"), memory_says("기록해 둘게요.")
        ),
        continuation=ASKED,
    )

    (card,) = of(events, pipeline.AgentResult)
    assert len(card.suggestion_ids) == 1  # 답은 코드 판정에만 쓰이고 추천은 그대로 나온다
    assert growth.sent and not any("당근" in text for text in growth.sent)


async def test_루틴_기록은_답_하나에_한_건만_저장한다():
    """두 번째 기록은 이름이 물은 행동으로 덮여 먼저 저장한 것과 겹친다. 이유를 알려 거절한다."""
    store = InMemoryStore(now=NOW)
    memory = SeesMessages(
        saves_routine(subject="양치", assistance_level="partial_assist"),
        saves_routine(subject="손 씻기", assistance_level="independent"),
        memory_says("기록해 둘게요."),
    )

    await run_events(
        "양치는 일부 도와주고 손 씻기는 혼자 해",
        store,
        growth_llm=ReadsToolResult(),
        memory_llm=memory,
        continuation=ASKED,
    )

    (row,) = await stored_routine(store)
    assert row.fields["assistance_level"] == "partial_assist"
    assert "한 건만 저장한다" in str(memory.seen[-1])


async def test_고정한_행동_이름도_위험_물질이면_거절한다():
    """고정하는 이름은 Growth 모델이 적은 값이다. 모델이 쓴 이름과 같은 판정을 한 번 더 건다."""
    store = InMemoryStore(now=NOW)
    risky = PendingMemoryContext(
        REQUEST,
        QUESTION,
        WorkType.OBSERVE,
        resume=RESUME,
        asked=DomainPending(
            "growth",
            ASK_ROUTINE_CURRENT,
            QUESTION,
            {"subject": "소주 마시기", "category": "self_care", "field": "assistance_level"},
        ),
    )

    await run_events(
        "혼자 해",
        store,
        growth_llm=ReadsToolResult(),
        memory_llm=FakeLLM(saves_routine(subject="양치"), memory_says("기록하지 않았어요.")),
        continuation=risky,
    )

    assert await stored_routine(store) == []
