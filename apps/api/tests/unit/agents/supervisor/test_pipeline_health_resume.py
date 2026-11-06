"""Health 되묻기 이어받기 — 질문을 답을 받는 질문으로 내고, 답 run 이 Health 로 이어 간다.

가짜 모델로 돈다 (API 호출 0). 앞 턴의 `PendingReply.context` 를 다음 턴의 `continuation` 으로 넘겨
러너가 보관함에서 꺼내 주는 것을 흉내 낸다. 답 run 의 Supervisor · Memory 는 응답이 없는 가짜라서
부르면 터진다 — 모델 0회가 곧 "부르지 않았다" 의 증거다.
  Task 2  질문을 답을 받는 질문으로 내고 맥락에 원문을 싣지 않는다
  Task 3  답 run 은 Supervisor · Memory 없이 Health 로 이어 간다
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.agents.health.readouts import READOUTS
from app.agents.health.store.inmemory import InMemoryMedicationStore, course_with_doses
from app.agents.memory.schemas.task import PendingMemoryContext
from app.agents.pipeline import (
    LEFTOVER_NOTE,
    LEFTOVER_NOTE_QUESTION,
    AgentQuestion,
    AgentResult,
    Failed,
    MedicationDrafts,
    MemoryNote,
    PendingReply,
    PipelineResult,
    Saved,
    handle_input,
)
from app.agents.run_writes import RunWrites, record_medication_writes
from app.agents.suggestion_save import InMemorySuggestions
from app.agents.supervisor.routing import Guidance
from tests.unit.agents.health.support import TODAY, FakeLLM, call, context
from tests.unit.agents.supervisor.test_pipeline_health import (
    PATTERN,
    RUN_ID,
    _fever,
    _memory_context,
    _no_commit,
    _of,
    _segment,
    _supervisor,
)

REPLY_RUN_ID = "run-2"


@pytest.fixture
def events() -> list[Any]:
    return []


def _world(*titles: str) -> tuple[Any, InMemoryMedicationStore]:
    """진행 중 코스가 `titles` 만큼 있는 Health context 와 그 저장소(쓰기를 보려고 같이 준다)."""
    seeds = [
        course_with_doses(title, PATTERN, TODAY - timedelta(days=1), TODAY + timedelta(days=1))
        for title in titles
    ]
    store = InMemoryMedicationStore([c for c, _ in seeds], [d for _, rows in seeds for d in rows])
    return context(medication=store), store


def _stop(title: str = "항생제") -> FakeLLM:
    return FakeLLM(call("stop_medication_schedule", {"title_span": title}))


async def _run(
    raw: str,
    supervisor: FakeLLM,
    health: Any,
    events: list[Any],
    *,
    health_llm: FakeLLM | None = None,
    memory_llm: FakeLLM | None = None,
    continuation: PendingMemoryContext | None = None,
    run_id: str = RUN_ID,
    child_months: int | None = None,
    writes: RunWrites | None = None,
) -> PipelineResult:
    return await handle_input(
        raw,
        _memory_context(),
        {"health": health},
        run_id=run_id,
        supervisor_client=supervisor,
        memory_client=memory_llm or FakeLLM(),
        domain_clients=None if health_llm is None else {"health": health_llm},
        emit=events.append,
        continuation=continuation,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
        child_months=child_months,
        writes=writes,
    )


async def _ask_stop(events: list[Any], *titles: str) -> tuple[Any, InMemoryMedicationStore]:
    """ "항생제 끊었어" 로 중단 확인 질문까지 낸 run. 그 Health context 와 저장소를 돌려준다."""
    raw = "항생제 끊었어"
    health, store = _world(*titles)
    await _run(
        raw, _supervisor(_segment(raw, "medication")), health, events, health_llm=_stop(titles[0])
    )
    return health, store


# ── Task 2 — 질문을 답을 받는 질문으로 낸다 ──────────────────────────────


async def test_중단_확인은_답을_받는_질문이고_맥락이_먼저_간다(events: list[Any]) -> None:
    await _ask_stop(events, "항생제")

    kinds = [type(event).__name__ for event in events]
    assert kinds.index("PendingReply") < kinds.index("AgentQuestion")
    [question] = _of(events, AgentQuestion)
    assert question.agent == "health" and question.awaits_reply
    assert question.text == READOUTS.render("med.stop_confirm", title="항생제").body


async def test_보관하는_Health_맥락에_원문이_없다(events: list[Any]) -> None:
    await _ask_stop(events, "항생제")

    [reply] = _of(events, PendingReply)
    context_ = reply.context
    assert reply.run_id == RUN_ID
    assert context_.hint_text == ""
    assert context_.resume is not None and context_.resume.request_texts == ()
    assert context_.resume.record_texts == () and context_.resume.answer_texts == ()
    assert context_.asked is not None
    assert (context_.asked.agent, context_.asked.kind) == ("health", "medication_stop")


async def test_Memory_가_물으면_Health_질문은_내지_않는다(events: list[Any]) -> None:
    """한 run 의 질문은 하나다(루트 §2). 코드가 체온을 물으면 Health 질문은 나가지 않는다."""
    raw = "열이 나요. 항생제 끊었어"
    supervisor = _supervisor(
        {"text": "열이 나요", "kind": "record", "work": "observe"},
        _segment("항생제 끊었어", "medication"),
    )
    health, _ = _world("항생제")

    await _run(
        raw,
        supervisor,
        health,
        events,
        health_llm=_stop(),
        memory_llm=_fever("열이 나요"),
        child_months=48,
    )

    [reply] = _of(events, PendingReply)
    assert reply.context.asks_temperature and reply.context.asked is None
    assert not _of(events, AgentQuestion)
    assert not [e for e in _of(events, AgentResult) if e.agent == "health" and e.readouts]


# ── Task 3 — 답 run 은 Supervisor · Memory 없이 Health 로 이어 간다 ─────────────


def _context_of(events: list[Any]) -> PendingMemoryContext:
    [reply] = _of(events, PendingReply)
    return reply.context


async def _reply(
    answer: str, events: list[Any], health: Any, continuation: PendingMemoryContext
) -> PipelineResult:
    """보호자가 `reply_to` 로 답한 run. Supervisor · Memory 는 응답이 없는 가짜라 부르면 터진다."""
    return await _run(
        answer, FakeLLM(), health, events, continuation=continuation, run_id=REPLY_RUN_ID
    )


async def _ask(
    raw: str, label: str, health: Any, events: list[Any], llm: FakeLLM
) -> PendingMemoryContext:
    await _run(raw, _supervisor(_segment(raw, label)), health, events, health_llm=llm)
    return _context_of(events)


async def test_응_이면_멈추고_그_run_의_모델은_0회(events: list[Any]) -> None:
    health, store = await _ask_stop(events, "항생제")
    replies: list[Any] = []

    result = await _reply("응", replies, health, _context_of(events))

    assert result.model_calls == 0 and result.failed is None
    assert len(store.writes) == 1 and len(store.stopped) == 1
    [card] = _of(replies, AgentResult)
    assert [r.body for r in card.readouts] == [READOUTS.render("med.stopped", title="항생제").body]
    assert not _of(replies, MemoryNote) and not _of(replies, Saved)
    assert result.memory is None


async def test_아니면_바꾸지_않는다(events: list[Any]) -> None:
    health, store = await _ask_stop(events, "항생제")
    replies: list[Any] = []

    result = await _reply("아니 그냥 둬", replies, health, _context_of(events))

    assert result.model_calls == 0 and store.writes == []
    [card] = _of(replies, AgentResult)
    assert [r.body for r in card.readouts] == [
        READOUTS.render("med.stop_kept", title="항생제").body
    ]


async def test_대상_약_답_뒤의_중단_확인도_답을_받는다(events: list[Any]) -> None:
    health, store = _world("항생제", "해열제")
    which = await _ask(
        "약 끊었어", "medication", health, events, FakeLLM(call("stop_medication_schedule", {}))
    )
    assert which.asked is not None and which.asked.kind == "medication_which"
    second: list[Any] = []

    await _reply("항생제", second, health, which)

    [question] = _of(second, AgentQuestion)
    assert question.awaits_reply
    [reply] = _of(second, PendingReply)
    assert reply.run_id == REPLY_RUN_ID
    assert reply.context.asked is not None and reply.context.asked.kind == "medication_stop"
    assert reply.context.hint_text == "" and store.writes == []
    third: list[Any] = []

    result = await _reply("응", third, health, reply.context)

    assert result.model_calls == 0 and len(store.writes) == 1  # 항생제 하나만 멈춘다


async def test_식후요_면_초안_한_장_저장_0건(events: list[Any]) -> None:
    health, store = _world()
    asked = await _ask(
        "하루 세 번 먹여야 해",
        "medication",
        health,
        events,
        FakeLLM(call("create_medication_schedule", {"timing_spans": ["하루 세 번"]})),
    )
    assert asked.asked is not None and asked.asked.kind == "dose_timing"
    replies: list[Any] = []

    result = await _reply("식후요", replies, health, asked)

    assert result.model_calls == 0
    [drafts] = _of(replies, MedicationDrafts)
    [draft] = drafts.drafts
    assert draft.notice_times == ("08:30", "12:30", "19:30") and store.writes == []
    assert not _of(replies, PendingReply) and not _of(replies, AgentQuestion)


async def test_못_가린_답은_쓰기_0건_다시_묻지_않는다(events: list[Any]) -> None:
    health, store = _world("항생제", "해열제")
    which = await _ask(
        "약 끊었어", "medication", health, events, FakeLLM(call("stop_medication_schedule", {}))
    )
    replies: list[Any] = []

    await _reply("글쎄", replies, health, which)

    [card] = _of(replies, AgentResult)
    assert [r.body for r in card.readouts] == [READOUTS.render("med.which_unclear").body]
    assert store.writes == []
    assert not _of(replies, PendingReply) and not _of(replies, AgentQuestion)


async def test_응급이_걸린_답은_Health_를_돌리지_않는다(events: list[Any]) -> None:
    health, store = await _ask_stop(events, "항생제")
    replies: list[Any] = []

    result = await _reply("응 지금 숨을 못 쉬어", replies, health, _context_of(events))

    assert result.emergency and result.model_calls == 0 and store.writes == []
    assert [g.code for g in _of(replies, Guidance)] == ["emergency"]
    assert not _of(replies, AgentResult)


async def test_답에_체온이_섞이면_반영하지_않았다는_안내가_붙는다(events: list[Any]) -> None:
    health, store = await _ask_stop(events, "항생제")
    replies: list[Any] = []

    await _reply("응 그리고 오늘 열 38도였어", replies, health, _context_of(events))

    assert len(store.writes) == 1  # 중단은 답대로 된다
    assert [(n.text, n.kind) for n in _of(replies, MemoryNote)] == [(LEFTOVER_NOTE, "message")]


async def test_짧은_확인_답에는_안내가_붙지_않는다(events: list[Any]) -> None:
    health, _ = await _ask_stop(events, "항생제")
    replies: list[Any] = []

    await _reply("네 알겠어요", replies, health, _context_of(events))

    assert not _of(replies, MemoryNote)


async def test_다시_묻는_답에_다른_내용이_섞이면_질문에_먼저_답하라는_문구가_나간다(
    events: list[Any],
) -> None:
    """다시 묻는 턴은 화면이 note 를 질문으로 잡는다.

    "따로 보내 주세요" 만 있으면 그 한 줄이 질문의 답이 된다.
    """
    health, _ = _world("항생제", "해열제")
    which = await _ask(
        "약 끊었어", "medication", health, events, FakeLLM(call("stop_medication_schedule", {}))
    )
    replies: list[Any] = []

    await _reply("항생제요 근데 열 38.5도예요", replies, health, which)

    [question] = _of(replies, AgentQuestion)
    assert question.awaits_reply
    assert [(n.text, n.kind) for n in _of(replies, MemoryNote)] == [
        (LEFTOVER_NOTE_QUESTION, "message")
    ]


async def test_중단이_되면_그_run_은_쓴_것으로_센다(events: list[Any]) -> None:
    """쓴 run 이 시간 초과로 끝나도 실패로 보이지 않게 러너가 이 표시를 본다."""
    health, store = await _ask_stop(events, "항생제")
    writes = RunWrites()
    recorded = replace(
        health,
        ports=replace(health.ports, medication_writer=record_medication_writes(store, writes)),
    )

    result = await _run(
        "응",
        FakeLLM(),
        recorded,
        [],
        continuation=_context_of(events),
        run_id=REPLY_RUN_ID,
        writes=writes,
    )

    assert len(store.writes) == 1 and result.committed


async def test_망가진_맥락은_다시_시도를_권하지_않고_안내도_붙이지_않는다(
    events: list[Any],
) -> None:
    """모델을 부르지 않은 run 이다. "연결 실패" 로 보이면 같은 실패를 15분 동안 되풀이한다."""
    health, store = await _ask_stop(events, "항생제")
    broken = _context_of(events)
    broken = replace(broken, asked=replace(broken.asked, payload={}))
    replies: list[Any] = []

    await _reply("응 그리고 열 38도였어", replies, health, broken)

    [failed] = _of(replies, Failed)
    assert failed.reason == "unparsable"
    assert not _of(replies, MemoryNote) and store.writes == []
