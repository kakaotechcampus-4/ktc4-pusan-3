"""최근 Food 추천 메뉴 — 같은 메뉴를 3일 안에 다시 내지 않는다 (meal_pool RECENT_DAYS)."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.suggestion.agent_reader import DbSuggestionHistory
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionKind,
    SuggestionStatus,
)

KST = ZoneInfo("Asia/Seoul")
SINCE = date(2026, 10, 11)


@pytest.fixture
async def kids(session):
    made = []
    for nickname in ("첫째", "둘째"):
        # 보호자 한 명에 아이 한 명만 연결된다(uq_parent_child_parent_id) — 아이마다 보호자를 둔다
        owner = Parent()
        session.add(owner)
        await session.flush()
        made.append(
            await create_child(
                session,
                owner_parent_id=owner.id,
                nickname=nickname,
                birth_date=date(2023, 1, 1),
                relation=ParentChildRelation.MOTHER,
            )
        )
    return made


@pytest.fixture
def history(session):
    @asynccontextmanager
    async def same():
        yield session

    return DbSuggestionHistory(same)


async def add(
    session, child, content, *, agent=SuggestionAgent.FOOD, at, status=SuggestionStatus.DRAFT
):
    session.add(
        Suggestion(
            child_id=child.id,
            agent=agent,
            kind=SuggestionKind.GENERAL,
            content=content,
            status=status,
            expires_at=at + timedelta(hours=24),
            created_at=at,
        )
    )
    await session.flush()


async def test_그_아이의_Food_추천만_since_부터(session, kids, history):
    child, other = kids
    start = datetime(2026, 10, 11, 0, 0, tzinfo=KST)
    await add(session, child, "계란말이 (2인분)", at=start)  # 경계 — KST 자정에 바로 만든 것
    await add(session, child, "두부조림", at=start - timedelta(seconds=1))  # 하루 전
    await add(session, child, "블록 쌓기", agent=SuggestionAgent.ACTIVITY, at=start)
    await add(session, other, "멸치볶음", at=start)

    assert await history.recent_menu_keys(child_id=child.id, since=SINCE) == {"계란말이"}


async def test_거절_만료_draft_는_세고_승인한_메뉴는_다시_나올_수_있다(session, kids, history):
    """#442 — 거절한 메뉴를 바로 다시 내면 반복으로 읽힌다. 승인한 메뉴는 다시 나와도 된다."""
    child, _ = kids
    at = datetime(2026, 10, 12, 9, 0, tzinfo=KST)
    for content, status in (
        ("계란말이", SuggestionStatus.APPROVED),
        ("두부조림", SuggestionStatus.REJECTED),
        ("멸치볶음", SuggestionStatus.EXPIRED),
        ("미역국", SuggestionStatus.DRAFT),
    ):
        await add(session, child, content, at=at, status=status)

    assert await history.recent_menu_keys(child_id=child.id, since=SINCE) == {
        "두부조림",
        "멸치볶음",
        "미역국",
    }


async def test_UTC_로_저장돼도_KST_날짜로_자른다(session, kids, history):
    child, _ = kids
    # KST 10-11 00:30 = UTC 10-10 15:30 — UTC 날짜로 자르면 빠진다
    await add(session, child, "계란말이", at=datetime(2026, 10, 10, 15, 30, tzinfo=UTC))

    assert await history.recent_menu_keys(child_id=child.id, since=SINCE) == {"계란말이"}
