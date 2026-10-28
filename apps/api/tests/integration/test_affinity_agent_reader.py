"""profile_affinity → 도메인 Agent 기억 포트 어댑터를 실제 DB 로."""

from contextlib import asynccontextmanager
from datetime import date

import pytest

from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.memory.profile.agent_reader import DbAffinityReader
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState

DAY = date(2026, 10, 1)


@pytest.fixture
async def kids(session):
    # 보호자 한 명에 아이 한 명만 연결된다(uq_parent_child_parent_id) — 아이마다 보호자를 둔다
    made = []
    for nickname in ("첫째", "다른 아이"):
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
async def child(session, kids):
    mine, other = kids

    def add(child_id, domain, key, state, polarity=1):
        session.add(
            ProfileAffinity(
                child_id=child_id,
                merge_key=key,
                domain=domain,
                state=state,
                polarity=polarity,
                strength=0.9,
                last_observed_on=DAY,
            )
        )

    add(mine.id, MemoryDomain.ACTIVITY, "공룡", ProfileState.CONFIRMED)
    add(mine.id, MemoryDomain.ACTIVITY, "블록", ProfileState.CANDIDATE, polarity=None)
    add(mine.id, MemoryDomain.ACTIVITY, "퍼즐", ProfileState.ARCHIVED)
    add(mine.id, MemoryDomain.EDUCATION, "숫자", ProfileState.CONFIRMED)
    add(mine.id, MemoryDomain.FOOD, "딸기", ProfileState.CONFIRMED)
    add(other.id, MemoryDomain.ACTIVITY, "기차", ProfileState.CONFIRMED)
    await session.flush()
    return mine


def reader(session) -> DbAffinityReader:
    @asynccontextmanager
    async def open_session():
        yield session  # 테스트 트랜잭션 안에서 읽는다 — 바깥 rollback 이 지운다

    return DbAffinityReader(open_session)


async def test_요청한_도메인의_archived_뺀_관심만_읽는다(session, child):
    found = await reader(session).affinities(child_id=child.id, domains=("activity",))
    assert sorted(r.merge_key for r in found) == ["공룡", "블록"]


async def test_도메인을_여럿_주면_합쳐_읽는다(session, child):
    found = await reader(session).affinities(child_id=child.id, domains=("education", "activity"))
    assert sorted(r.merge_key for r in found) == ["공룡", "블록", "숫자"]


async def test_AffinityRow_로_옮기고_극성_없는_candidate_도_싣는다(session, child):
    rows = await reader(session).affinities(child_id=child.id, domains=("activity",))
    found = {r.merge_key: r for r in rows}
    assert (found["공룡"].state, found["공룡"].polarity, found["공룡"].last_observed_on) == (
        "confirmed",
        1,
        DAY,
    )
    assert (found["블록"].state, found["블록"].polarity) == ("candidate", None)
