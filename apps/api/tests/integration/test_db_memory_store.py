"""DbMemoryStore tests.

child_a's observation id로 child_b의 store에서 get/update/delete하면 차단되는지 검증한다.
삭제 시 Promotable 모델의 embedding 비움을 검증한다.
잘못된 UUID 문자열이 ValueError 대신 None/False 를 반환하는지 검증한다.
이벤트 날짜 필터가 KST 자정 경계를 올바르게 다루는지 검증한다.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.datetime_rules import DateRange
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ObservationFood
from app.domains.memory.store.db_store import DbMemoryStore
from app.domains.schedule.models import Event, EventCategory, EventCreatedBy, EventType


@pytest.fixture
async def family(session: AsyncSession):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child_a = Child(owner_parent_id=owner.id, nickname="아이A", birth_date=date(2023, 1, 1))
    child_b = Child(owner_parent_id=owner.id, nickname="아이B", birth_date=date(2023, 6, 1))
    session.add_all([child_a, child_b])
    await session.flush()
    return owner, child_a, child_b


@pytest.fixture
async def obs_of_a(session: AsyncSession, family: tuple) -> str:
    """child_a의 food observation을 만들고 id를 반환한다."""
    owner, child_a, _ = family
    store_a = DbMemoryStore(session, child_id=child_a.id)
    row = await store_a.create_observation(
        domain="food",
        child_id=child_a.id,
        source_writer=owner.id,
        raw_text="사과 잘 먹었어",
        observed_on=date(2026, 10, 1),
        observed_range=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 2)),
        fields={"subject": "사과", "polarity": 1, "confidence_source": "parent_direct"},
    )
    return row.id


class TestChildIdIsolation:
    async def test_get_observation_of_other_child_returns_none(
        self, session: AsyncSession, family: tuple, obs_of_a: str
    ) -> None:
        _, _, child_b = family
        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.get_observation(domain="food", observation_id=obs_of_a)
        assert result is None

    async def test_update_observation_of_other_child_returns_none(
        self, session: AsyncSession, family: tuple, obs_of_a: str
    ) -> None:
        _, _, child_b = family
        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.update_observation(
            domain="food", observation_id=obs_of_a, fields={"subject": "바나나"}
        )
        assert result is None

    async def test_delete_observation_of_other_child_returns_false(
        self, session: AsyncSession, family: tuple, obs_of_a: str
    ) -> None:
        _, _, child_b = family
        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.delete_observation(domain="food", observation_id=obs_of_a)
        assert result is False

    async def test_own_child_get_works(
        self, session: AsyncSession, family: tuple, obs_of_a: str
    ) -> None:
        """Sanity check: child_a's store can still read its own observation."""
        _, child_a, _ = family
        store_a = DbMemoryStore(session, child_id=child_a.id)
        result = await store_a.get_observation(domain="food", observation_id=obs_of_a)
        assert result is not None
        assert result.id == obs_of_a


class TestDeleteEmbeddingCleanup:
    async def test_food_삭제_시_embedding도_비워진다(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        row = await store.create_observation(
            domain="food",
            child_id=child_a.id,
            source_writer=owner.id,
            raw_text="바나나 좋아해",
            observed_on=date(2026, 10, 1),
            observed_range=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 2)),
            fields={"subject": "바나나", "polarity": 1, "confidence_source": "parent_direct"},
        )
        # embedding 을 직접 세팅
        from uuid import UUID

        orm = await session.get(ObservationFood, UUID(row.id))
        orm.embedding = [0.1] * 1536
        await session.flush()

        deleted = await store.delete_observation(domain="food", observation_id=row.id)
        assert deleted is True

        await session.refresh(orm)
        assert orm.embedding is None

    async def test_health_삭제_시_embedding_컬럼_없어도_정상(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        row = await store.create_observation(
            domain="health",
            child_id=child_a.id,
            source_writer=owner.id,
            raw_text="열이 나요",
            observed_on=date(2026, 10, 1),
            observed_range=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 2)),
            fields={"symptom": ["fever"], "confidence_source": "parent_direct"},
        )
        deleted = await store.delete_observation(domain="health", observation_id=row.id)
        assert deleted is True


class TestInvalidUuidDefence:
    async def test_잘못된_id로_get하면_None(
        self, session: AsyncSession, family: tuple
    ) -> None:
        _, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        result = await store.get_observation(domain="food", observation_id="not-a-uuid")
        assert result is None

    async def test_잘못된_id로_delete하면_False(
        self, session: AsyncSession, family: tuple
    ) -> None:
        _, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        result = await store.delete_observation(domain="food", observation_id="xxx")
        assert result is False


_KST = timezone(timedelta(hours=9))


class TestEventQueryKST:
    async def test_KST_자정_직후_이벤트가_올바른_날짜로_조회된다(
        self, session: AsyncSession, family: tuple
    ) -> None:
        """UTC 15:00 (= KST 00:00 다음날) 이벤트가 KST 날짜 기준으로 정확히 잡히는지."""
        owner, child_a, _ = family
        # UTC 2026-10-06 15:00 = KST 2026-10-07 00:00
        utc_ts = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
        event = Event(
            child_id=child_a.id,
            title="KST 자정 테스트",
            event_type=EventType.EPISODIC,
            starts_at=utc_ts,
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()

        store = DbMemoryStore(session, child_id=child_a.id)

        # KST 기준 10/7 에 해당하므로 10/7 로 조회하면 잡혀야 한다
        rows_correct = await store.query_events(
            child_id=child_a.id, date_from=date(2026, 10, 7), date_to=date(2026, 10, 7)
        )
        assert len(rows_correct) == 1

        # KST 기준 10/6 에는 안 잡혀야 한다
        rows_wrong = await store.query_events(
            child_id=child_a.id, date_from=date(2026, 10, 6), date_to=date(2026, 10, 6)
        )
        assert len(rows_wrong) == 0
