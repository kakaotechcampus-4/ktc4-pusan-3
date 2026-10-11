"""되묻기를 여러 run 으로 잇는 라이브 eval (#246). 화면이 받는 이벤트까지 본다.
    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/memory/test_multirun.py -m live -s -rs

러너가 하는 대로 잇는다. 첫 run 은 입력을 pipeline 에 넣고, run 이 PendingReply 를 내면 그
맥락을 다음 run 의 continuation 으로 넘긴다. Supervisor 는 정답 출력으로 고정하고 Memory 만
실제 모델을 부른다. 나누기가 흔들리면 이어받기까지 가지 못한다.

진료 시각을 모르는 보호자가 계속 모른다고 답하면, 재질문 상한에서 질문 대신 상한 문구가
message 로 나가고 PendingReply 가 없어 사슬이 끝나야 한다. Memory 만 떼어 본 상한 eval 은
test_continuation 에 있고, 이 파일은 그 위의 pipeline(이벤트 · 문구 · 맥락 넘기기)을 본다.
모델이 상한 전에 그만 물으면 상한 문구를 보는 테스트는 skip 된다(-rs 로 사유를 본다).

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMClient, LLMResponse
from app.agents.memory.agent import MAX_QUESTIONS, MemoryAgentResult
from app.agents.memory.bundles import MUTATING_PREFIXES
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import PendingMemoryContext
from app.agents.memory.store import InMemoryStore
from app.agents.pipeline import (
    ASK_LIMIT_NOTE,
    LEFTOVER_NOTE,
    LEFTOVER_NOTE_NO_CONTEXT,
    NO_CONTEXT_NOTE,
    MemoryNote,
    PendingReply,
    PipelineResult,
    handle_input,
)
from tests.eval.agents.routing_cases import SCHEDULE, R, RoutingCase, answer_output

pytestmark = pytest.mark.live

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
WRITER = UUID(int=2)

TODAY = datetime.now(KST).date()
NOW = datetime(TODAY.year, TODAY.month, TODAY.day, 9, tzinfo=KST)

# 시각이 없는 일정. 프롬프트대로라면 만들기 전에 몇 시인지 묻는다
CASE = RoutingCase(
    "MR01",
    "다음 주 수요일 병원 예약 있어.",
    (R("다음 주 수요일 병원 예약 있어", SCHEDULE),),
    watch="시각 없는 일정. 몇 시인지 되묻고, 계속 모른다고 하면 상한에서 끝난다",
)
_VAGUE_ANSWERS = ("잘 모르겠어요", "아직 몰라요", "글쎄요", "모르겠어요")


class _AnswerSupervisor:
    """정답 Supervisor 출력을 route tool 호출로 돌려준다. 첫 run 의 나누기를 고정한다."""

    def __init__(self, case: RoutingCase) -> None:
        output = answer_output(case).model_dump(mode="json", exclude_none=True)
        route = SimpleNamespace(name="route", arguments=json.dumps(output, ensure_ascii=False))
        call = SimpleNamespace(id="s1", function=route)
        message = SimpleNamespace(content=None, tool_calls=[call])
        self._response = LLMResponse(message=message, usage={}, latency_ms=0)

    async def chat(self, **_: Any) -> LLMResponse:
        return self._response


@dataclass(frozen=True)
class Turn:
    run_id: str
    said: str  # 이 run 에 넣은 말. 첫 run 은 입력, 그 뒤는 답
    events: list[Any]
    result: PipelineResult

    @property
    def order(self) -> list[str]:
        return [type(event).__name__ for event in self.events]

    @property
    def notes(self) -> list[MemoryNote]:
        return [event for event in self.events if isinstance(event, MemoryNote)]

    @property
    def pending(self) -> PendingReply | None:
        """러너가 보관함에 넣을 맥락. 화면에는 가지 않는다."""
        return next((event for event in self.events if isinstance(event, PendingReply)), None)


def _client() -> LLMClient:
    settings = AgentSettings()
    if not settings.MEMORY_API_KEY or not settings.MEMORY_BASE_URL:
        pytest.skip("MEMORY_API_KEY / MEMORY_BASE_URL 이 필요합니다. apps/api/.env 를 확인하세요.")
    return LLMClient(settings, role="memory")


def _context(store: InMemoryStore) -> AgentContext:
    # run 마다 새 context 를 쓴다. 일정 초안 버퍼는 run 하나의 것이고 저장소만 이어진다
    return AgentContext(child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store)


def _wrote(memory: MemoryAgentResult) -> bool:
    """그 run 에서 성공한 쓰기가 있는가. 일정 초안도 센다. 실패한 쓰기는 저장이 아니다."""
    return any(call.success and call.name.startswith(MUTATING_PREFIXES) for call in memory.calls)


async def _chain(memory_client: LLMClient) -> list[Turn]:
    store = InMemoryStore(now=NOW)
    supervisor = _AnswerSupervisor(CASE)

    async def turn(n: int, said: str, continuation: PendingMemoryContext | None) -> Turn:
        run_id = f"eval-multirun-{n}"
        events: list[Any] = []
        result = await handle_input(
            said,
            _context(store),
            {},  # 기록만 있는 나누기라 도메인 Agent 를 부르지 않는다
            run_id=run_id,
            supervisor_client=supervisor,
            memory_client=memory_client,
            emit=events.append,
            continuation=continuation,
        )
        return Turn(run_id, said, events, result)

    turns = [await turn(1, CASE.text, None)]
    for n, answer in enumerate(_VAGUE_ANSWERS, start=2):
        pending = turns[-1].pending
        if pending is None:
            break
        turns.append(await turn(n, answer, pending.context))
    return turns


@pytest.fixture(scope="module")
def chain() -> list[Turn]:
    turns = asyncio.run(_chain(_client()))
    for n, turn in enumerate(turns, start=1):
        notes = [(note.kind, note.text) for note in turn.notes]
        print(f"\n[run {n}] {turn.said!r} → {turn.order} {notes}")
    return turns


def test_시각_없는_일정은_만들지_않고_몇_시인지_되묻는다(chain: list[Turn]) -> None:
    first = chain[0]
    assert "Failed" not in first.order, f"첫 run 이 실패: {first.order}"
    assert not {"Saved", "EventDrafts"} & set(first.order), "시각 없이 일정을 만듦"
    assert first.pending is not None, "되묻지 않아 이어받기를 볼 수 없음"
    # 화면이 보여 준 질문과 러너가 맡아 둘 질문이 같아야 답이 그 질문으로 이어진다
    assert [note.kind for note in first.notes] == ["question"]
    assert first.pending.context.question in first.notes[0].text
    assert first.pending.run_id == first.run_id


def test_답이_계속_모자라면_run_마다_맥락이_넘어가고_상한_안에서_끝난다(chain: list[Turn]) -> None:
    if len(chain) == 1:
        pytest.skip("첫 run 이 되묻지 않아 이어 갈 run 이 없음")
    for n, turn in enumerate(chain, start=1):
        # Done 은 실패한 run 도 마지막에 낸다. 실패는 Failed 로 본다
        assert "Failed" not in turn.order, f"{n}번째 run 이 실패: {turn.order}"
        assert turn.order[-1] == "Done", f"{n}번째 run 이 Done 으로 끝나지 않음: {turn.order}"
        assert turn.order.count("PendingReply") <= 1, f"{n}번째 run 의 맥락이 여럿: {turn.order}"

    # 답한 run 은 앞 run 의 질문과 자기 답을 한 쌍 더 쌓아 넘긴다. 상한은 이 길이로 센다
    first = chain[0].pending
    assert first is not None
    for n, (before, turn) in enumerate(pairwise(chain), start=1):
        asked, pending = before.pending, turn.pending
        if pending is None:
            break
        assert asked is not None  # 맥락이 없으면 run 을 더 잇지 않는다
        assert pending.run_id == turn.run_id, f"{n}번째 답의 맥락이 다른 run 으로 감"
        assert pending.context.hint_text == first.context.hint_text, f"{n}번째 답에서 조각이 바뀜"
        assert pending.context.transcript[-2:] == (asked.context.question, turn.said), (
            f"{n}번째 답이 안 쌓임"
        )
        assert len(pending.context.transcript) == 2 * n, f"{n}번째 맥락의 칸 수가 틀림"

    assert chain[-1].pending is None, "상한까지 답했는데 질문이 또 열림"
    assert len(chain) <= 1 + MAX_QUESTIONS


def test_상한에서_또_물으면_질문_대신_상한_문구로_끝난다(chain: list[Turn]) -> None:
    if len(chain) == 1:
        pytest.skip("첫 run 이 되묻지 않아 이어 갈 run 이 없음")
    last = chain[-1]
    memory = last.result.memory
    reply = memory.reply if memory is not None else None
    n = len(chain) - 1  # 답한 횟수
    if memory is None or reply is None or n < MAX_QUESTIONS or reply.kind != "question":
        kind = reply.kind if reply is not None else None
        pytest.skip(
            f"모델이 {n}번째 답에서 끝냄(kind={kind}). 상한에서 또 묻지 않아 이번엔 확인 못 함"
        )
    if _wrote(memory):
        # 저장한 뒤 물었으면 상한이 아니다. 맥락 없는 질문으로 내려가
        # 답을 따로 보내라는 안내가 붙는다
        assert not memory.ask_limit_reached, "저장한 run 을 상한으로 셈"
        assert [note.kind for note in last.notes] == ["message"]
        assert last.notes[0].text.endswith((NO_CONTEXT_NOTE, LEFTOVER_NOTE_NO_CONTEXT))
        return
    # 질문은 나가지 않고 상한 문구만 나간다. 섞인 말이 있으면 그것만 따로 보내라고 붙인다
    expected = f"{ASK_LIMIT_NOTE} {LEFTOVER_NOTE}" if memory.leftover else ASK_LIMIT_NOTE
    assert [(note.text, note.kind) for note in last.notes] == [(expected, "message")]
    assert last.order == ["Step", "MemoryNote", "Done"]
