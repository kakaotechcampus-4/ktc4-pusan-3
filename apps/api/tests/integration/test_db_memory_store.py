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
from app.domains.schedule.models import Event, EventCategory, EventCreatedBy, EventItem, EventType


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

    async def test_event_row_시각이_KST로_변환된다(
        self, session: AsyncSession, family: tuple
    ) -> None:
        """_event_row 의 starts_at 이 +09:00 으로 나오는지."""
        owner, child_a, _ = family
        utc_ts = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
        event = Event(
            child_id=child_a.id,
            title="시간대 변환 테스트",
            event_type=EventType.EPISODIC,
            starts_at=utc_ts,
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()

        store = DbMemoryStore(session, child_id=child_a.id)
        rows = await store.query_events(
            child_id=child_a.id, date_from=date(2026, 10, 7), date_to=date(2026, 10, 7)
        )
        assert len(rows) == 1
        # starts_at 이 KST (+09:00) 로 변환돼야 한다
        assert rows[0].starts_at.utcoffset() == timedelta(hours=9)
        assert rows[0].starts_at.hour == 0  # UTC 15시 = KST 0시


class TestEventChildIdIsolation:
    """일정·준비물의 child_id 필터 검증."""

    async def test_다른_아이의_일정을_get하면_None(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, child_b = family
        event = Event(
            child_id=child_a.id,
            title="아이A 수영장",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()

        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.get_event(event_id=str(event.id))
        assert result is None

    async def test_다른_아이의_일정을_delete하면_False(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, child_b = family
        event = Event(
            child_id=child_a.id,
            title="아이A 병원",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.HEALTH,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()

        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.delete_event(event_id=str(event.id))
        assert result is False


class TestEventItemChildIdIsolation:
    """준비물(event_item)의 child_id 격리 검증."""

    async def test_다른_아이의_준비물을_get하면_None(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, child_b = family
        event = Event(
            child_id=child_a.id,
            title="아이A 소풍",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()
        item = EventItem(event_id=event.id, item_name="수영복", is_prepared=False)
        session.add(item)
        await session.flush()

        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.get_event_item(item_id=str(item.item_id))
        assert result is None

    async def test_다른_아이의_준비물_목록은_빈_리스트(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, child_b = family
        event = Event(
            child_id=child_a.id,
            title="아이A 캠프",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()
        item = EventItem(event_id=event.id, item_name="물통", is_prepared=False)
        session.add(item)
        await session.flush()

        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.list_event_items(event_id=str(event.id))
        assert result == []


class TestEventItemChildIdIsolationUpdateDelete:
    """준비물 update/delete의 child_id 격리 검증."""

    async def test_다른_아이의_준비물을_update하면_None(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, child_b = family
        event = Event(
            child_id=child_a.id,
            title="아이A 운동회",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()
        item = EventItem(event_id=event.id, item_name="체육복", is_prepared=False)
        session.add(item)
        await session.flush()

        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.update_event_item(
            item_id=str(item.item_id), fields={"is_prepared": True}
        )
        assert result is None

    async def test_다른_아이의_준비물을_delete하면_False(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, child_b = family
        event = Event(
            child_id=child_a.id,
            title="아이A 소풍",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()
        item = EventItem(event_id=event.id, item_name="도시락", is_prepared=False)
        session.add(item)
        await session.flush()

        store_b = DbMemoryStore(session, child_id=child_b.id)
        result = await store_b.delete_event_item(item_id=str(item.item_id))
        assert result is False


class TestWroteFlag:
    """create / update / delete 후 wrote 플래그 검증."""

    async def test_create_후_wrote가_True(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        assert store.wrote is False
        await store.create_observation(
            domain="food",
            child_id=child_a.id,
            source_writer=owner.id,
            raw_text="딸기 잘 먹었어",
            observed_on=date(2026, 10, 1),
            observed_range=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 2)),
            fields={"subject": "딸기", "polarity": 1, "confidence_source": "parent_direct"},
        )
        assert store.wrote is True

    async def test_delete_event_후_wrote가_True(
        self, session: AsyncSession, family: tuple
    ) -> None:
        _, child_a, _ = family
        event = Event(
            child_id=child_a.id,
            title="삭제 테스트",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()

        store = DbMemoryStore(session, child_id=child_a.id)
        assert store.wrote is False
        await store.delete_event(event_id=str(event.id))
        assert store.wrote is True

    async def test_update_event_item_후_wrote가_True(
        self, session: AsyncSession, family: tuple
    ) -> None:
        _, child_a, _ = family
        event = Event(
            child_id=child_a.id,
            title="준비물 테스트",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()
        item = EventItem(event_id=event.id, item_name="수건", is_prepared=False)
        session.add(item)
        await session.flush()

        store = DbMemoryStore(session, child_id=child_a.id)
        assert store.wrote is False
        await store.update_event_item(
            item_id=str(item.item_id), fields={"is_prepared": True}
        )
        assert store.wrote is True

    async def test_delete_event_item_후_wrote가_True(
        self, session: AsyncSession, family: tuple
    ) -> None:
        _, child_a, _ = family
        event = Event(
            child_id=child_a.id,
            title="준비물 삭제 테스트",
            event_type=EventType.EPISODIC,
            starts_at=datetime(2026, 10, 7, 10, 0, tzinfo=_KST),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.CAREGIVER,
        )
        session.add(event)
        await session.flush()
        item = EventItem(event_id=event.id, item_name="모자", is_prepared=False)
        session.add(item)
        await session.flush()

        store = DbMemoryStore(session, child_id=child_a.id)
        assert store.wrote is False
        await store.delete_event_item(item_id=str(item.item_id))
        assert store.wrote is True

    async def test_update_후_wrote가_True(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        row = await store.create_observation(
            domain="food",
            child_id=child_a.id,
            source_writer=owner.id,
            raw_text="사과 잘 먹었어",
            observed_on=date(2026, 10, 1),
            observed_range=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 2)),
            fields={"subject": "사과", "polarity": 1, "confidence_source": "parent_direct"},
        )
        # create 에서 wrote=True 가 되므로 새 store 로 테스트
        store2 = DbMemoryStore(session, child_id=child_a.id)
        assert store2.wrote is False
        await store2.update_observation(
            domain="food", observation_id=row.id, fields={"subject": "배"}
        )
        assert store2.wrote is True

    async def test_delete_후_wrote가_True(
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
        store.wrote = False  # 리셋
        await store.delete_observation(domain="food", observation_id=row.id)
        assert store.wrote is True


class TestWroteFlagStaysFalse:
    """대상이 없거나 실패하면 wrote가 False를 유지하는지."""

    async def test_update_대상_없으면_wrote_False(
        self, session: AsyncSession, family: tuple
    ) -> None:
        _, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        result = await store.update_observation(
            domain="food",
            observation_id="00000000-0000-0000-0000-000000000099",
            fields={"subject": "없는 관찰"},
        )
        assert result is None
        assert store.wrote is False

    async def test_delete_대상_없으면_wrote_False(
        self, session: AsyncSession, family: tuple
    ) -> None:
        _, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        result = await store.delete_observation(
            domain="food",
            observation_id="00000000-0000-0000-0000-000000000099",
        )
        assert result is False
        assert store.wrote is False


class TestInvalidUuidUpdateDefence:
    async def test_잘못된_id로_update하면_None(
        self, session: AsyncSession, family: tuple
    ) -> None:
        _, child_a, _ = family
        store = DbMemoryStore(session, child_id=child_a.id)
        result = await store.update_observation(
            domain="food", observation_id="bad", fields={"subject": "x"}
        )
        assert result is None
