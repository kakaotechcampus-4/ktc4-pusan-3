"""관찰을 고치거나 지우면 Curator 집계에 다시 들어간다 (#257).

Memory Agent 가 고친 관찰은 `updated_at` 이 갱신된다. 그중 Curator 집계에 영향을 주는 칸이 바뀌면
DbMemoryStore 가 같은 트랜잭션에서 정리한다.

    subject · polarity     벡터와 Profile 연결을 비운다 → 다음 Curator 가 다시 임베딩 · 연결한다
    + observed_range · strong_signals · 삭제
                           원래 Profile 의 last_observed_on 과 상태를 다시 계산한다

Correction(handle_observation_correction)이 관찰 상태를 바꾼 뒤 Profile 을 바로 다시 계산하는 것과
같은 길이다.
"""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.datetime_rules import DateRange
from app.agents.curator.embedding.judge import JudgeAnswer
from app.agents.curator.embedding.linker import link_observations
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.curator.db_store import DbCuratorStore
from app.domains.memory.observation.models import ObservationFood, ObservationLinkHold
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.memory.store.db_store import DbMemoryStore
from app.rules.profile import STRENGTH_DEFAULT

TODAY = date(2026, 10, 10)
EARLY = date(2026, 10, 1)
LATE = date(2026, 10, 5)
VECTOR = [0.1] * 1536


@pytest.fixture
async def family(session: AsyncSession) -> tuple[Parent, Child]:
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="재집계", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


@pytest.fixture
async def profile(session: AsyncSession, family: tuple[Parent, Child]) -> ProfileAffinity:
    _, child = family
    row = ProfileAffinity(
        child_id=child.id,
        domain=MemoryDomain.FOOD,
        polarity=1,
        merge_key="사과",
        embedding=VECTOR,
        state=ProfileState.CANDIDATE,
        strength=STRENGTH_DEFAULT,
        last_observed_on=LATE,
    )
    session.add(row)
    await session.flush()
    return row


@pytest.fixture
def store(session: AsyncSession, family: tuple[Parent, Child]) -> DbMemoryStore:
    _, child = family
    return DbMemoryStore(session, child_id=child.id, today=TODAY)


async def _linked(
    session: AsyncSession,
    store: DbMemoryStore,
    family: tuple[Parent, Child],
    profile: ProfileAffinity,
    day: date,
) -> str:
    """Curator 가 임베딩 · 연결까지 끝낸 food 관찰 하나."""
    owner, child = family
    row = await store.create_observation(
        domain="food",
        child_id=child.id,
        source_writer=owner.id,
        raw_text="원문",
        observed_on=day,
        observed_range=DateRange(start=day, end=day + timedelta(days=1)),
        fields={"subject": "사과", "polarity": 1, "confidence_source": "parent_direct"},
    )
    await session.execute(
        update(ObservationFood)
        .where(ObservationFood.id == UUID(row.id))
        .values(embedding=VECTOR, affinity_id=profile.id)
    )
    return row.id


async def _food(session: AsyncSession, observation_id: str) -> ObservationFood:
    row = await session.get(ObservationFood, UUID(observation_id), populate_existing=True)
    assert row is not None
    return row


async def _last_observed_on(session: AsyncSession, profile: ProfileAffinity) -> date:
    await session.refresh(profile)
    return profile.last_observed_on


@pytest.mark.parametrize(
    "change", [{"subject": "바나나"}, {"polarity": -1}], ids=["subject", "polarity"]
)
async def test_subject_polarity_를_고치면_벡터와_연결을_비워_Curator_가_다시_집는다(
    session: AsyncSession,
    family: tuple[Parent, Child],
    profile: ProfileAffinity,
    store: DbMemoryStore,
    change: dict[str, Any],
) -> None:
    _, child = family
    await _linked(session, store, family, profile, EARLY)
    edited = await _linked(session, store, family, profile, LATE)
    session.add(
        ObservationLinkHold(
            child_id=child.id,
            domain="food",
            observation_id=UUID(edited),
            uncertain_count=1,
            subject_hash="old",
        )
    )
    await session.flush()

    await store.update_observation(domain="food", observation_id=edited, fields=change)

    row = await _food(session, edited)
    assert row.embedding is None and row.affinity_id is None
    pending = await DbCuratorStore(session).list_unembedded(child_id=child.id)
    assert [item.id for item in pending] == [edited]
    holds = await session.scalar(
        select(func.count())
        .select_from(ObservationLinkHold)
        .where(ObservationLinkHold.observation_id == UUID(edited))
    )
    assert holds == 0  # 옛 subject 로 쌓인 보류 기록도 지운다
    # 빠진 관찰 몫을 원래 Profile 에서 다시 계산한다
    assert await _last_observed_on(session, profile) == EARLY


async def test_날짜만_고치면_연결은_두고_원래_Profile_을_다시_계산한다(
    session: AsyncSession,
    family: tuple[Parent, Child],
    profile: ProfileAffinity,
    store: DbMemoryStore,
) -> None:
    await _linked(session, store, family, profile, EARLY)
    edited = await _linked(session, store, family, profile, LATE)
    moved = date(2026, 9, 20)

    await store.update_observation(
        domain="food",
        observation_id=edited,
        fields={"observed_range": DateRange(start=moved, end=moved + timedelta(days=1))},
        observed_on=moved,
    )

    row = await _food(session, edited)
    assert row.embedding is not None and row.affinity_id == profile.id
    assert await _last_observed_on(session, profile) == EARLY


async def test_집계와_상관없는_칸만_고치면_그대로_둔다(
    session: AsyncSession,
    family: tuple[Parent, Child],
    profile: ProfileAffinity,
    store: DbMemoryStore,
) -> None:
    edited = await _linked(session, store, family, profile, LATE)

    await store.update_observation(
        domain="food", observation_id=edited, fields={"amount": "반 그릇"}
    )

    row = await _food(session, edited)
    assert row.embedding is not None and row.affinity_id == profile.id


async def test_같은_값으로_고치면_다시_집지_않는다(
    session: AsyncSession,
    family: tuple[Parent, Child],
    profile: ProfileAffinity,
    store: DbMemoryStore,
) -> None:
    edited = await _linked(session, store, family, profile, LATE)

    await store.update_observation(domain="food", observation_id=edited, fields={"subject": "사과"})

    row = await _food(session, edited)
    assert row.embedding is not None and row.affinity_id == profile.id


async def test_지우면_원래_Profile_을_다시_계산한다(
    session: AsyncSession,
    family: tuple[Parent, Child],
    profile: ProfileAffinity,
    store: DbMemoryStore,
) -> None:
    await _linked(session, store, family, profile, EARLY)
    deleted = await _linked(session, store, family, profile, LATE)

    assert await store.delete_observation(domain="food", observation_id=deleted)

    assert await _last_observed_on(session, profile) == EARLY


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [VECTOR for _ in texts]


class _NoMatchJudge:
    async def judge(self, *, subject: str, domain: str, candidates: Any) -> JudgeAnswer:
        return JudgeAnswer(choice="none", model="fake")


async def test_고친_관찰은_다음_Curator_실행에서_새_Profile_로_연결된다(
    session: AsyncSession,
    family: tuple[Parent, Child],
    profile: ProfileAffinity,
    store: DbMemoryStore,
) -> None:
    _, child = family
    kept = await _linked(session, store, family, profile, EARLY)
    edited = await _linked(session, store, family, profile, LATE)

    await store.update_observation(
        domain="food", observation_id=edited, fields={"subject": "바나나"}
    )
    result = await link_observations(
        DbCuratorStore(session),
        _FakeEmbedder(),
        _NoMatchJudge(),
        child_id=child.id,
        birth_date=date(2023, 1, 1),
    )

    row = await _food(session, edited)
    assert row.affinity_id is not None and row.affinity_id != profile.id
    created = await session.get(ProfileAffinity, row.affinity_id)
    assert created is not None and created.merge_key == "바나나"
    assert result.created_profile_ids == (str(row.affinity_id),)
    assert (await _food(session, kept)).affinity_id == profile.id
