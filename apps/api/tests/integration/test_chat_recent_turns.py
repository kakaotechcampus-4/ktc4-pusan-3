"""Supervisor 앞 대화 조회 — `recent_turns` 와 `agent_ports.recent_turns_loader` (DB 필요).

    ① 접수 30분 안의 턴만, 오래된 순으로
    ② 최근 세 턴만
    ③ 다른 아이 · 다른 보호자의 턴은 안 잡힌다 (보호자는 같고 아이만 다른 행도)
    ④ 자정을 넘겨 질문한 날에 붙은 답도 잡힌다 — 날짜 칸은 하루 앞까지만 좁힌다
    ⑤ 로더는 턴과 추천 content 를 읽어 DTO 로 돌려준다

문장 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9). 러너가 여는 짧은 세션은 테스트 세션으로
바꿔 끼운다. 끝나면 같이 롤백된다.
"""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest

from app.api import agent_ports
from app.api.chat_replies import CHAT_REPLIES, AgentResultReply, DoneReply
from app.domains.chat.models import ChatTurn
from app.domains.chat.repository import add_turn, recent_turns
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.identity.models import Parent
from app.domains.suggestion.models import SuggestionAgent, SuggestionKind
from app.domains.suggestion.repository import create_suggestion

SINCE = datetime(2026, 10, 15, 14, 40, tzinfo=UTC)  # KST 23:40
DAY = date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _same_session(monkeypatch, session):
    @asynccontextmanager
    async def same():
        yield session

    monkeypatch.setattr(agent_ports, "background_session", same)


async def make_parent(session) -> Parent:
    parent = Parent()
    session.add(parent)
    await session.flush()
    return parent


async def make_child(session, parent: Parent, *, nickname: str = "test child") -> Child:
    # 보호자 한 명은 아이 한 명에만 연결된다(uq_parent_child_parent_id) — 형제는 못 만든다
    child = Child(owner_parent_id=parent.id, nickname=nickname, birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    session.add(
        ParentChild(parent_id=parent.id, child_id=child.id, relation=ParentChildRelation.OTHER)
    )
    await session.flush()
    return child


async def other_family(session) -> tuple[Parent, Child]:
    parent = await make_parent(session)
    return parent, await make_child(session, parent, nickname="other child")


@pytest.fixture
async def family(session):
    parent = await make_parent(session)
    child = await make_child(session, parent)
    return parent, child


def replies(*, suggestion_ids: list[str] | None = None) -> list:
    card = AgentResultReply(
        agent="activity",
        task_type=None,
        status="completed",
        readouts=[],
        question=None,
        suggestion_ids=suggestion_ids or [],
        kind="general",
        reason=None,
        record_count=0,
    )
    return CHAT_REPLIES.dump_python([card, DoneReply(completed=True)], mode="json")


async def put(
    session, parent: Parent, child: Child, run_id: str, text: str, created_at: datetime, **kw
):
    await add_turn(
        session,
        parent_id=parent.id,
        child_id=child.id,
        run_id=run_id,
        conversation_date=kw.pop("day", DAY),
        raw_text=text,
        reply_to=None,
        replies=kw.pop("replies", replies()),
        created_at=created_at,
    )


async def read(session, parent, child, *, since=SINCE, limit=3) -> list[str]:
    rows = await recent_turns(
        session, parent_id=parent.id, child_id=child.id, since=since, limit=limit
    )
    return [row.raw_text for row in rows]


async def test_30분_안의_턴만_오래된_순으로(session, family) -> None:
    parent, child = family
    await put(session, parent, child, "r1", "너무 오래된 말", SINCE - timedelta(minutes=10))
    await put(session, parent, child, "r2", "나중 말", SINCE + timedelta(minutes=10))
    await put(session, parent, child, "r3", "먼저 말", SINCE + timedelta(minutes=5))

    assert await read(session, parent, child) == ["먼저 말", "나중 말"]


async def test_최근_세_턴만(session, family) -> None:
    parent, child = family
    for number in range(4):
        await put(
            session, parent, child, f"r{number}", f"말 {number}", SINCE + timedelta(minutes=number)
        )

    assert await read(session, parent, child) == ["말 1", "말 2", "말 3"]


async def test_다른_아이의_턴은_안_잡힌다(session, family) -> None:
    parent, child = family
    other_parent, other_child = await other_family(session)
    await put(session, parent, child, "r1", "이 아이 말", SINCE + timedelta(minutes=1))
    await put(session, other_parent, other_child, "r2", "다른 집 말", SINCE + timedelta(minutes=2))
    # 보호자는 같고 아이만 다른 행 — add_turn 은 연결이 없으면 쓰지 않아서 행을 직접 넣어 본다
    session.add(
        ChatTurn(
            parent_id=parent.id,
            child_id=other_child.id,
            run_id="r3",
            conversation_date=DAY,
            raw_text="아이만 다른 말",
            reply_to=None,
            replies=replies(),
            created_at=SINCE + timedelta(minutes=3),
        )
    )
    await session.flush()

    assert await read(session, parent, child) == ["이 아이 말"]


async def test_다른_보호자의_턴은_안_잡힌다(session, family) -> None:
    parent, child = family
    other = await make_parent(session)
    session.add(
        ParentChild(parent_id=other.id, child_id=child.id, relation=ParentChildRelation.OTHER)
    )
    await session.flush()
    await put(session, parent, child, "r1", "내 말", SINCE + timedelta(minutes=1))
    await put(session, other, child, "r2", "다른 보호자 말", SINCE + timedelta(minutes=2))

    assert await read(session, parent, child) == ["내 말"]


async def test_자정을_넘긴_턴도_잡힌다(session, family) -> None:
    parent, child = family
    # 10-15 질문에 KST 00:10(10-16)에 답한 턴 — 접수 시각은 10-16 이지만 날짜는 질문한 날이다
    since = datetime(2026, 10, 15, 15, 5, tzinfo=UTC)  # KST 00:05, 10-16
    answered = datetime(2026, 10, 15, 15, 10, tzinfo=UTC)
    await put(session, parent, child, "r1", "자정 넘긴 답", answered, day=date(2026, 10, 15))

    assert await read(session, parent, child, since=since) == ["자정 넘긴 답"]


async def test_로더는_턴과_추천_content_를_읽는다(session, family) -> None:
    parent, child = family
    now = datetime.now(UTC)
    suggestion = await create_suggestion(
        session,
        child_id=child.id,
        agent=SuggestionAgent.ACTIVITY,
        kind=SuggestionKind.GENERAL,
        content="블록 쌓기",
        reason=None,
        citations=[],
        expires_at=now + timedelta(days=1),
        run_id="r1",
        requested=True,
    )
    await put(
        session,
        parent,
        child,
        "r1",
        "실내 놀이 추천해줘",
        now - timedelta(minutes=5),
        day=now.date(),
        replies=replies(suggestion_ids=[str(suggestion.id)]),
    )

    context = await agent_ports.recent_turns_loader(parent.id, child.id)()

    (turn,) = context.turns
    assert turn.text == "실내 놀이 추천해줘"
    assert turn.results[0].agent == "activity"
    assert turn.results[0].suggestions == ("블록 쌓기",)


async def test_다른_아이의_추천_id_는_content_를_못_읽는다(session, family) -> None:
    parent, child = family
    _, other_child = await other_family(session)
    now = datetime.now(UTC)
    theirs = await create_suggestion(
        session,
        child_id=other_child.id,
        agent=SuggestionAgent.ACTIVITY,
        kind=SuggestionKind.GENERAL,
        content="형제의 추천",
        reason=None,
        citations=[],
        expires_at=now + timedelta(days=1),
        run_id="r9",
        requested=True,
    )
    await put(
        session,
        parent,
        child,
        "r1",
        "실내 놀이 추천해줘",
        now - timedelta(minutes=5),
        day=now.date(),
        replies=replies(suggestion_ids=[str(theirs.id)]),
    )

    context = await agent_ports.recent_turns_loader(parent.id, child.id)()

    assert context.turns[0].results[0].suggestions == ()


async def test_로더는_뺀_턴_뒤의_쓸_수_있는_턴을_찾는다(session, family) -> None:
    """최근 세 턴이 모두 빼는 턴이어도 그 앞의 쓸 수 있는 턴을 읽는다 — 조회 상한이 거름보다
    먼저면 "[앞 대화] 없음" 이 거짓으로 나간다."""
    from app.api.chat_replies import GuidanceReply

    parent, child = family
    now = datetime.now(UTC)
    start = now - timedelta(minutes=10)
    await put(session, parent, child, "r0", "실내 놀이 추천해줘", start, day=now.date())
    hazard = CHAT_REPLIES.dump_python(
        [GuidanceReply(code="hazard", message="119", deeplink=None)], mode="json"
    )
    for number in range(1, 4):
        await put(
            session,
            parent,
            child,
            f"r{number}",
            f"막힌 말 {number}",
            start + timedelta(minutes=number),
            day=now.date(),
            replies=hazard,
        )

    context = await agent_ports.recent_turns_loader(parent.id, child.id)()

    assert [turn.text for turn in context.turns] == ["실내 놀이 추천해줘"]
