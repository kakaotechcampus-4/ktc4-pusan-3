"""삭제 제안 tool — app/agents/memory/tools/proposal.py (#535).

    ① 후보는 이 run 에서 종류마다 마지막으로 조회한 결과다 — 모델이 고른 id 는 기본 체크일 뿐이다
    ② 후보 밖 id · 조회 없는 제안 · 두 번째 제안은 실패하고 아무것도 남기지 않는다
    ③ 같은 종류를 좁혀 다시 조회하면 마지막 결과만 후보다 — 0건이면 그 종류가 빠진다

발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.datetime_rules import DateRange
from app.agents.memory.context import AgentContext
from app.agents.memory.registry import execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import DeleteCandidates

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(now=NOW)


@pytest.fixture
def context(store: InMemoryStore) -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=store,
        run_id="run-1",
    )


async def _meal(context: AgentContext, subject: str, back: int = 1) -> str:
    day = NOW.date() - timedelta(days=back)
    row = await context.store.create_observation(
        domain="food",
        child_id=context.child_id,
        source_writer=context.source_writer,
        raw_text=f"synthetic {subject}",
        observed_on=day,
        observed_range=DateRange(start=day, end=day + timedelta(days=1)),
        fields={"subject": subject, "action": "먹었다"},
    )
    return row.id


async def _event(context: AgentContext) -> str:
    row = await context.store.create_event(
        child_id=context.child_id,
        title="synthetic event",
        starts_at=NOW - timedelta(days=1),
        ends_at=None,
        all_day=False,
        fields={"event_type": "episodic", "category": "activity"},
    )
    return row.id


async def _call(context: AgentContext, name: str, args: dict | None = None):
    return await execute_tool(name, args or {}, context)


async def test_조회한_종류마다_탭을_만들고_고른_것만_기본_체크한다(
    context: AgentContext, store: InMemoryStore
) -> None:
    rice, snack = await _meal(context, "rice", 2), await _meal(context, "snack")
    event = await _event(context)
    await _call(context, "query_event", {"date_from": "2026-09-13", "date_to": "2026-09-15"})
    await _call(context, "query_observation_food")

    result = await _call(context, "propose_delete", {"ids": [rice, event]})

    assert result.success is True, result.error
    assert (result.data["picked"], result.data["candidates"]) == (2, 3)
    # 탭 순서는 조회 순서가 아니라 화면 순서다 — 관찰이 앞, 일정이 끝
    assert store.proposal == (
        result.data["id"],
        (
            DeleteCandidates("observation_food", False, (rice, snack), (rice,)),
            DeleteCandidates("event", False, (event,), (event,)),
        ),
    )
    assert [p.id for p in context.proposals] == [result.data["id"]]


async def test_고른_것이_없어도_제안한다(context: AgentContext, store: InMemoryStore) -> None:
    # 모델이 못 골랐어도 보호자가 카드에서 직접 고를 수 있다
    rice = await _meal(context, "rice")
    await _call(context, "query_observation_food")

    result = await _call(context, "propose_delete", {"ids": []})

    assert result.success is True
    assert store.proposal is not None
    assert store.proposal[1] == (DeleteCandidates("observation_food", False, (rice,), ()),)


async def test_조회_없이_부르면_실패한다(context: AgentContext, store: InMemoryStore) -> None:
    rice = await _meal(context, "rice")

    result = await _call(context, "propose_delete", {"ids": [rice]})

    assert result.success is False
    assert result.error["code"] == ErrorCode.TARGET_REQUIRED
    assert store.proposal is None and context.proposals == []


async def test_후보_밖_id_가_있으면_아무것도_남기지_않는다(
    context: AgentContext, store: InMemoryStore
) -> None:
    rice = await _meal(context, "rice")
    await _call(context, "query_observation_food", {"raw_text_query": "rice"})
    other = await _meal(context, "soup")

    result = await _call(context, "propose_delete", {"ids": [rice, other]})

    assert result.success is False
    assert result.error["code"] == ErrorCode.TARGET_NOT_FOUND
    assert other in result.error["message"]
    assert store.proposal is None


async def test_두_번째_제안은_실패한다(context: AgentContext, store: InMemoryStore) -> None:
    rice = await _meal(context, "rice")
    await _call(context, "query_observation_food")
    first = await _call(context, "propose_delete", {"ids": [rice]})

    again = await _call(context, "propose_delete", {"ids": []})

    assert first.success is True
    assert again.success is False and again.error["code"] == ErrorCode.INVALID_ARGS
    assert store.proposal is not None and store.proposal[0] == first.data["id"]


async def test_좁혀_다시_조회하면_마지막_결과만_후보다(
    context: AgentContext, store: InMemoryStore
) -> None:
    rice, _ = await _meal(context, "rice"), await _meal(context, "snack")
    await _call(context, "query_observation_food")
    await _call(context, "query_observation_food", {"raw_text_query": "rice"})

    await _call(context, "propose_delete", {"ids": [rice]})

    assert store.proposal is not None
    assert store.proposal[1] == (DeleteCandidates("observation_food", False, (rice,), (rice,)),)


async def test_좁힌_조회가_0건이면_그_종류는_후보에서_빠진다(
    context: AgentContext, store: InMemoryStore
) -> None:
    # 넓은 조회 결과가 남으면 보호자가 좁힌 범위 밖의 기록이 카드에 올라 지워질 수 있다
    await _meal(context, "rice")
    event = await _event(context)
    await _call(context, "query_observation_food")
    await _call(context, "query_event", {"date_from": "2026-09-13", "date_to": "2026-09-15"})
    await _call(context, "query_observation_food", {"raw_text_query": "pizza"})

    await _call(context, "propose_delete", {"ids": [event]})

    assert store.proposal is not None
    assert [tab.kind for tab in store.proposal[1]] == ["event"]
