"""Growth 의 이미 낸 책 — 만료 전 Growth 추천의 items. 승인 · 거절한 책도 다시 내지 않는다."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest

from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.suggestion.agent_reader import DbIssuedBooks
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionKind,
    SuggestionStatus,
)

NOW = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)


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
def issued(session):
    @asynccontextmanager
    async def same():
        yield session

    return DbIssuedBooks(same)


async def add(
    session,
    child,
    items,
    *,
    expires_at,
    agent=SuggestionAgent.GROWTH,
    status=SuggestionStatus.DRAFT,
):
    session.add(
        Suggestion(
            child_id=child.id,
            agent=agent,
            kind=SuggestionKind.GENERAL,
            content=items[0],
            items=list(items),
            status=status,
            expires_at=expires_at,
        )
    )
    await session.flush()


async def test_만료_전_Growth_추천의_items_를_상태와_상관없이_돌려준다(session, kids, issued):
    child, other = kids
    later = NOW + timedelta(hours=3)
    await add(session, child, ["책1(가)"], expires_at=later)
    await add(session, child, ["책2(가)"], expires_at=later, status=SuggestionStatus.APPROVED)
    await add(session, child, ["책3(가)"], expires_at=later, status=SuggestionStatus.REJECTED)
    await add(session, child, ["책4(가)"], expires_at=NOW)  # 지금 만료 — 경계는 뺀다
    await add(session, child, ["책5(가)"], expires_at=later, agent=SuggestionAgent.FOOD)
    await add(session, other, ["책6(가)"], expires_at=later)

    assert await issued.labels(child_id=child.id, now=NOW) == {"책1(가)", "책2(가)", "책3(가)"}


async def test_교육_준비물도_같은_칸이라_섞여_온다(session, kids, issued):
    child, _ = kids
    await add(session, child, ["색종이", "풀"], expires_at=NOW + timedelta(hours=1))

    assert await issued.labels(child_id=child.id, now=NOW) == {"색종이", "풀"}
