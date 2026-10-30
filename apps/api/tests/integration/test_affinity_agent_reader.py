"""profile_affinity → 도메인 Agent 기억 포트 어댑터를 실제 DB 로."""

from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationStatus,
)
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


# 도메인마다 관찰 표와 사람이 읽는 칸
_OBSERVATION = {
    MemoryDomain.FOOD: (ObservationFood, {}),
    MemoryDomain.ACTIVITY: (ObservationActivity, {"activity": "놀이"}),
    MemoryDomain.EDUCATION: (ObservationEducation, {"topic": "공부"}),
}


async def _link(session, row: ProfileAffinity, status=ObservationStatus.ACTIVE, day=DAY) -> None:
    """기억에 관찰 한 건을 묶는다."""
    model, fields = _OBSERVATION[row.domain]
    session.add(
        model(
            child_id=row.child_id,
            raw_text="원문",
            subject=row.merge_key,
            confidence_source=ConfidenceSource.PARENT_DIRECT,
            observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
            affinity_id=row.id,
            status=status,
            **fields,
        )
    )
    await session.flush()


@pytest.fixture
async def child(session, kids):
    mine, other = kids

    async def add(child_id, domain, key, state, polarity=1, days=1):
        row = ProfileAffinity(
            child_id=child_id,
            merge_key=key,
            domain=domain,
            state=state,
            polarity=polarity,
            strength=0.9,
            last_observed_on=DAY,
        )
        session.add(row)
        await session.flush()
        for back in range(days):  # 하루에 한 건씩
            await _link(session, row, day=DAY - timedelta(days=back))
        return row

    await add(mine.id, MemoryDomain.ACTIVITY, "공룡", ProfileState.CONFIRMED, days=3)
    await add(mine.id, MemoryDomain.ACTIVITY, "블록", ProfileState.CANDIDATE, polarity=None)
    await add(mine.id, MemoryDomain.ACTIVITY, "퍼즐", ProfileState.ARCHIVED)
    await add(mine.id, MemoryDomain.EDUCATION, "숫자", ProfileState.CONFIRMED)
    await add(mine.id, MemoryDomain.FOOD, "딸기", ProfileState.CONFIRMED)
    await add(other.id, MemoryDomain.ACTIVITY, "기차", ProfileState.CONFIRMED)
    # 관찰을 전부 "잘못된 기록" 으로 고친 기억 — 묶인 active 관찰이 없다
    wrong = await add(mine.id, MemoryDomain.FOOD, "소주", ProfileState.CANDIDATE, days=0)
    await _link(session, wrong, status=ObservationStatus.INACTIVE)
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


async def test_묶인_active_관찰이_있는_날_수를_싣는다(session, child):
    rows = await reader(session).affinities(child_id=child.id, domains=("activity",))
    assert {r.merge_key: r.observed_days for r in rows} == {"공룡": 3, "블록": 1}


async def test_같은_날_관찰_여러_건은_하루로_센다(session, child):
    # 승격 O 와 같은 기준 — 입력 한 번에 관찰이 두 건 생겨도 티어 2 근거가 되지 않는다 (#633 리뷰)
    rows = await reader(session).affinities(child_id=child.id, domains=("activity",))
    block = next(r for r in rows if r.merge_key == "블록")
    row = await session.get(ProfileAffinity, block.id)
    await _link(session, row)  # 같은 DAY 에 한 건 더

    rows = await reader(session).affinities(child_id=child.id, domains=("activity",))
    assert next(r for r in rows if r.merge_key == "블록").observed_days == 1


async def test_묶인_active_관찰이_없는_기억은_싣지_않는다(session, child):
    # 기억 화면에서 사라진 기억이 추천 근거로 남지 않는다 (#626)
    found = await reader(session).affinities(child_id=child.id, domains=("food",))
    assert [r.merge_key for r in found] == ["딸기"]
