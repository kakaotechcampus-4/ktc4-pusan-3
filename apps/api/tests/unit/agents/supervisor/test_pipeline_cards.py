"""조회 카드 · 삭제 카드 내보내기 — pipeline._send_cards (#535).

    ① 카드 행은 Memory commit 뒤에 읽는다(`load_cards`) — 읽다 실패하면 run 세션을 rollback 한다
    ② 조회만 하고 끝난 조회 · 수정 run 에는 조회 카드, 저장이 섞이면 붙지 않는다
    ③ 삭제 제안을 남긴 run 은 삭제 카드를 내고, 모델이 물어도 되묻기 맥락을 넘기지 않는다
    ④ 되묻기에 답하는 run 에는 조회 카드를 붙이지 않는다

발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from app.agents.common.datetime_rules import DateRange
from app.agents.memory.agent import PROPOSED_REPLY
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import FoundRef, PendingMemoryContext, WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.pipeline import (
    CardTab,
    DeleteProposed,
    FoundTabs,
    MemoryNote,
    PendingReply,
    Ref,
    Saved,
    handle_input,
)
from app.agents.suggestion_save import InMemorySuggestions
from tests.eval.agents.routing_cases import CASES_BY_ID
from tests.unit.agents.supervisor.test_pipeline import (
    CHILD,
    KST,
    NOW,
    RUN_ID,
    FakeLLM,
    _answer,
    _call,
    _default_food_llm,
    _food_context,
    _supervisor_llm,
    _tools,
)


@pytest.fixture
def memory_context() -> AgentContext:
    return AgentContext(
        child_id=CHILD,
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
        run_id=RUN_ID,
    )


class _Log:
    """commit · load_cards · emit 의 순서를 한 줄로 남긴다."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.events: list[Any] = []
        self.loaded: list[tuple[Ref, ...]] = []

    async def commit(self) -> None:
        self.order.append("commit")

    async def load_cards(self, refs: tuple[Ref, ...]) -> None:
        self.order.append("load_cards")
        self.loaded.append(refs)

    def emit(self, event: Any) -> None:
        self.events.append(event)
        if isinstance(event, FoundTabs | DeleteProposed):
            self.order.append(type(event).__name__)

    def of(self, kind: type) -> list[Any]:
        return [event for event in self.events if isinstance(event, kind)]


async def _activity(context: AgentContext, subject: str) -> str:
    day = NOW.date() - timedelta(days=1)
    row = await context.store.create_observation(
        domain="activity",
        child_id=context.child_id,
        source_writer=context.source_writer,
        raw_text=f"synthetic {subject}",
        observed_on=day,
        observed_range=DateRange(start=day, end=day + timedelta(days=1)),
        fields={"subject": subject, "activity": f"{subject} 놀이"},
    )
    return row.id


async def _run(
    case_id: str,
    memory_context: AgentContext,
    memory_llm: FakeLLM,
    log: _Log,
    *,
    continuation: PendingMemoryContext | None = None,
    text: str | None = None,
) -> None:
    await handle_input(
        text or CASES_BY_ID[case_id].text,
        memory_context,
        {"food": _food_context()},
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm(case_id),
        memory_client=memory_llm,
        domain_clients={"food": _default_food_llm()},
        emit=log.emit,
        commit=log.commit,
        save_suggestions=InMemorySuggestions(),
        load_cards=log.load_cards,
        continuation=continuation,
    )


async def test_조회만_하고_끝나면_commit_뒤에_조회_카드를_낸다(
    memory_context: AgentContext,
) -> None:
    blocks = await _activity(memory_context, "블록")
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_activity", {"date_from": "어제", "date_to": "어제"})),
        _answer({"text": "어제 블록놀이 기록이 있어요.", "kind": "message"}),
    )
    log = _Log()

    await _run("T08", memory_context, llm, log)

    assert log.order == ["commit", "load_cards", "FoundTabs"]
    assert log.of(FoundTabs) == [FoundTabs((CardTab("observation_activity", False, (blocks,)),))]
    assert log.loaded == [(Ref("observation_activity", blocks),)]
    # 카드는 AI 답 아래에 붙는다 — note 가 먼저 나간다
    kinds = [type(event) for event in log.events if isinstance(event, MemoryNote | FoundTabs)]
    assert kinds == [MemoryNote, FoundTabs]


async def test_찾은_행이_없으면_조회_카드를_내지_않는다(memory_context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_activity", {"date_from": "어제", "date_to": "어제"})),
        _answer({"text": "어제 기록을 찾지 못했어요.", "kind": "message"}),
    )
    log = _Log()

    await _run("T08", memory_context, llm, log)

    assert log.of(FoundTabs) == [] and log.loaded == []


async def test_수정을_되묻는_run_에도_후보를_조회_카드로_보여_준다(
    memory_context: AgentContext,
) -> None:
    first, second = await _activity(memory_context, "블록"), await _activity(memory_context, "블록")
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_activity", {"raw_text_query": "블록"})),
        _answer({"text": "어느 블록놀이 기록인가요?", "kind": "question"}),
    )
    log = _Log()

    await _run("T23", memory_context, llm, log)

    [card] = log.of(FoundTabs)
    assert card.tabs[0].ids == (first, second)
    assert log.of(PendingReply) != []


async def test_삭제_제안을_남기면_삭제_카드를_내고_되묻기_맥락은_없다(
    memory_context: AgentContext,
) -> None:
    blocks, lego = await _activity(memory_context, "블록"), await _activity(memory_context, "레고")
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_activity", {"date_from": "어제", "date_to": "어제"})),
        _tools(_call("p1", "propose_delete", {"ids": [blocks]})),
        # 모델이 물어도 코드가 일반 답변으로 끝낸다 — 지우는 것은 카드의 버튼뿐이다
        _answer({"text": "블록놀이 기록을 삭제할까요?", "kind": "question"}),
    )
    log = _Log()

    await _run("T10", memory_context, llm, log)

    assert log.order == ["commit", "load_cards", "DeleteProposed"]
    [card] = log.of(DeleteProposed)
    assert card.tabs == (CardTab("observation_activity", False, (blocks, lego), (blocks,)),)
    assert log.of(FoundTabs) == []
    assert log.of(PendingReply) == []
    assert [(note.text, note.kind) for note in log.of(MemoryNote)] == [(PROPOSED_REPLY, "message")]


async def test_저장이_섞인_run_에는_조회_카드를_붙이지_않는다(memory_context: AgentContext) -> None:
    await _activity(memory_context, "블록")
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_activity", {"date_from": "어제", "date_to": "어제"})),
        _tools(
            _call(
                "c1",
                "create_observation_activity",
                {
                    "raw_text": "오늘 레고 했어",
                    "observed_on": "오늘",
                    "subject": "레고",
                    "activity": "레고 놀이",
                },
            )
        ),
        _answer({"text": "저장했어요.", "kind": "message"}),
    )
    log = _Log()

    await _run("T08", memory_context, llm, log)

    assert log.of(Saved) != []
    assert log.of(FoundTabs) == []


async def _answer_lookup(memory_context: AgentContext, found: tuple[FoundRef, ...]) -> _Log:
    asked = PendingMemoryContext(
        hint_text="어제 블록놀이 기록 보여줘",
        question="어느 날 기록인가요?",
        work=WorkType.LOOKUP_EDIT,
        found=found,
    )
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_activity", {"date_from": "어제", "date_to": "어제"})),
        _answer({"text": "어제 블록놀이 기록이 있어요.", "kind": "message", "leftover": False}),
    )
    log = _Log()
    await _run("T08", memory_context, llm, log, continuation=asked, text="어제")
    return log


async def test_앞_턴이_후보를_보였으면_답하는_run_에는_조회_카드를_다시_붙이지_않는다(
    memory_context: AgentContext,
) -> None:
    blocks = await _activity(memory_context, "블록")

    log = await _answer_lookup(memory_context, (FoundRef("observation_activity", blocks),))

    assert memory_context.found  # 조회는 했다 — 카드만 붙지 않는다
    assert log.of(FoundTabs) == [] and log.loaded == []


async def test_앞_턴이_못_찾고_물었으면_답하는_run_이_조회_카드를_낸다(
    memory_context: AgentContext,
) -> None:
    blocks = await _activity(memory_context, "블록")

    log = await _answer_lookup(memory_context, ())

    assert log.of(FoundTabs) == [FoundTabs((CardTab("observation_activity", False, (blocks,)),))]
