"""관찰 삭제 확인 라이브 eval. "지워줘 → 삭제할까요? → 응 → 삭제" 두 턴을 잇는다 (#177).
    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/memory/test_delete_confirm.py -m live -s

첫 턴은 T10 의 Supervisor 정답 출력으로 돌리고, 그 run 이 남긴 pending 을 그대로 둘째 턴에 넘긴다.
pending 을 테스트가 만들어 넣으면 작업 종류가 lookup_edit 로 이어지는지를 못 본다.
lookup_edit 가 아니면 둘째 턴에 수정 묶음이 열리지 않아 update 를 부를 수 없다.

첫 턴만 보는 것은 test_memory 의 T10 이다.
기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.datetime_rules import DateRange
from app.agents.common.llm_client import LLMClient
from app.agents.memory.agent import MemoryAgentResult, ToolCallRecord, run
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.supervisor.agent import SupervisorResult
from app.agents.supervisor.routing import route
from tests.eval.agents.routing_cases import CASES_BY_ID, answer_output

pytestmark = pytest.mark.live

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
WRITER = UUID(int=2)
DOMAINS = ("food", "health", "education", "activity", "routine")

TODAY = datetime.now(KST).date()
YESTERDAY = TODAY - timedelta(days=1)
NOW = datetime(TODAY.year, TODAY.month, TODAY.day, 9, tzinfo=KST)

ASK = CASES_BY_ID["T10"]  # "어제 사과 먹었다고 저장한 기록 지워줘."

# 지웠다고 답한 말. "삭제하지 않았어요" 가 걸리지 않게 어미까지 본다
_SAID_DELETED = ("삭제했", "삭제됐", "삭제되었", "지웠")


@dataclass(frozen=True)
class Case:
    case_id: str
    answer: str  # "삭제할까요?" 에 대한 보호자의 답
    deleted: bool  # 둘째 턴이 끝난 뒤 사과 기록이 지워져 있어야 하는지


CASES = [
    Case("D01", "응", True),
    # 아니라고 하면 지우지 않는다. 이 케이스가 없으면 답과 상관없이 지우는 모델도 D01 을 통과한다
    Case("D02", "아니, 그냥 둬", False),
]


def _client() -> LLMClient:
    settings = AgentSettings()
    if not settings.MEMORY_API_KEY or not settings.MEMORY_BASE_URL:
        pytest.skip("MEMORY_API_KEY / MEMORY_BASE_URL 이 필요합니다. apps/api/.env 를 확인하세요.")
    return LLMClient(settings, role="memory")


def _context(store: InMemoryStore) -> AgentContext:
    # 턴마다 새 context 를 쓴다. 일정 초안 버퍼는 run 하나의 것이고 저장소만 이어진다
    return AgentContext(child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store)


async def _seed_apple(store: InMemoryStore) -> str:
    row = await store.create_observation(
        domain="food",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="어제 사과를 먹었어",
        observed_on=YESTERDAY,
        observed_range=DateRange(start=YESTERDAY, end=TODAY),
        fields={"subject": "사과", "action": "먹었다"},
    )
    return row.id


def _deletes(result: MemoryAgentResult) -> list[ToolCallRecord]:
    """status=deleted 로 부른 관찰 update. 성공한 것만 센다."""
    return [
        call
        for call in result.calls
        if call.success
        and call.name.startswith("update_observation_")
        and call.arguments.get("status") == "deleted"
    ]


def _writes(result: MemoryAgentResult) -> list[str]:
    return [
        call.name
        for call in result.calls
        if call.success and call.name.startswith(("create_", "update_", "delete_"))
    ]


async def _counts(store: InMemoryStore) -> dict[str, int]:
    # 조회는 지운 행을 빼고 돌려준다
    return {
        domain: len(await store.query_observations(domain=domain, child_id=CHILD))
        for domain in DOMAINS
    }


def _log(turn: str, result: MemoryAgentResult, counts: dict[str, int]) -> None:
    reply = result.reply
    print(
        f"  [{turn}] rows={counts} kind={reply.kind if reply else None} "
        f"pending={result.pending is not None} leftover={result.leftover} "
        f"tools={result.tool_names} {result.final_message!r}"
    )


async def _flow(case: Case, client: LLMClient) -> None:
    store = InMemoryStore(now=NOW)
    apple = await _seed_apple(store)

    # 첫 턴: 조회로 찾은 기록을 짚어 묻고, 지우지 않는다
    routing = route(ASK.text, SupervisorResult(output=answer_output(ASK)), run_id="eval-delete")
    assert routing.memory_task is not None
    first = await run(ASK.text, _context(store), client=client, task=routing.memory_task)
    counts = await _counts(store)
    print(f"\n[{case.case_id}]")
    _log("1", first, counts)

    assert "query_observation_food" in first.tool_names, "조회하지 않고 물었다"
    assert not _deletes(first), "묻기 전에 지웠다"
    assert counts["food"] == 1, "첫 턴에 사과 기록이 사라졌다"
    assert first.reply is not None and first.reply.kind == "question", "지울지 묻지 않았다"
    assert "사과" in first.reply.text, "어떤 기록을 지울지 짚지 않았다"
    # 화면은 pending 이 있어야 "이어서 적기" 를 연다.
    # 작업 종류가 바뀌면 둘째 턴에 update 가 안 열린다
    assert first.pending is not None, "되물었는데 이어받을 맥락이 없다"
    assert first.pending.work == WorkType.LOOKUP_EDIT, f"작업 종류가 {first.pending.work}"

    # 둘째 턴: 보호자의 답을 이어받는다
    second = await run(case.answer, _context(store), client=client, continuation=first.pending)
    counts = await _counts(store)
    _log("2", second, counts)

    # 이어받기는 조기 종료하지 않아 늘 말로 끝난다
    assert second.reply is not None and second.reply.text.strip(), "답이 비었다"
    assert second.leftover is False, "섞인 말이 없는데 따로 보내라고 했다"
    others = {domain: count for domain, count in counts.items() if domain != "food"}
    assert not any(others.values()), f"답을 새 관찰로 저장했다: {others}"

    if case.deleted:
        deletes = _deletes(second)
        assert [call.arguments.get("observation_id") for call in deletes] == [apple], (
            f"사과 기록 한 건을 지우지 않았다: {[call.name for call in deletes]}"
        )
        assert counts["food"] == 0, "지웠다는데 조회에 남아 있다"
        assert _writes(second) == [deletes[0].name], f"삭제 말고 다른 쓰기: {_writes(second)}"
        assert second.reply.kind == "message", "지운 뒤에 또 물었다"
        assert second.pending is None
        assert any(word in second.reply.text for word in _SAID_DELETED), "지웠다고 알리지 않았다"
    else:
        assert not _writes(second), f"아니라고 했는데 썼다: {_writes(second)}"
        assert counts["food"] == 1, "아니라고 했는데 사과 기록이 사라졌다"
        said = any(word in second.reply.text for word in _SAID_DELETED)
        assert not said, "안 지웠는데 지웠다고 했다"


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_삭제는_확인을_받고_지운다(case: Case, expect: Callable[..., None]) -> None:
    second = (
        "사과 기록 한 건만 status=deleted, 조회에서 빠지고 지웠다고 알린다"
        if case.deleted
        else "아무것도 쓰지 않고 사과 기록이 남는다"
    )
    expect(
        f"1턴 {ASK.text!r} → 조회 후 사과 기록을 짚어 되묻고 지우지 않는다 "
        "(kind=question · pending=lookup_edit)",
        f"2턴 {case.answer!r} → {second}",
    )
    asyncio.run(_flow(case, _client()))
