"""DB CuratorStore — Protocol 메서드가 DB 에서 올바르게 동작하는지 검증한다.

인메모리 구현(inmemory.py)과 동일한 동작을 보장한다.
구현 대상: app/domains/memory/curator/db_store.py (Phase A)
"""

from datetime import date

import pytest
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationActivity,
    ObservationFood,
    ObservationLinkHold,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


def _food(child_id, *, subject="사과", polarity=1, observed_on=date(2026, 9, 26), **overrides):
    return ObservationFood(
        child_id=child_id,
        raw_text="test",
        subject=subject,
        polarity=polarity,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(observed_on, observed_on),
        **overrides,
    )


def _activity(child_id, *, subject="축구", polarity=1, observed_on=date(2026, 9, 26), **overrides):
    return ObservationActivity(
        child_id=child_id,
        raw_text="test",
        subject=subject,
        polarity=polarity,
        activity="축구",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(observed_on, observed_on),
        **overrides,
    )


@pytest.fixture
async def store(session):
    from app.domains.memory.curator.db_store import DbCuratorStore

    return DbCuratorStore(session)


# ---------------------------------------------------------------------------
# list_unembedded — active + embedding 없음
# ---------------------------------------------------------------------------


class TestListUnembedded:
    async def test_active이고_벡터_없는_관찰만_반환(self, session, family, store):
        _, child = family
        # 벡터 없는 active
        obs1 = _food(child.id, subject="사과")
        # 벡터 있는 active → 제외
        obs2 = _food(child.id, subject="배", embedding=[0.1] * 1536)
        # inactive → 제외
        obs3 = _food(child.id, subject="감", status=ObservationStatus.INACTIVE)
        session.add_all([obs1, obs2, obs3])
        await session.flush()

        result = await store.list_unembedded(child_id=child.id)

        ids = [item.id for item in result]
        assert str(obs1.id) in ids
        assert str(obs2.id) not in ids
        assert str(obs3.id) not in ids

    async def test_여러_도메인을_합쳐_created_at_오름차순(self, session, family, store):
        _, child = family
        food = _food(child.id, subject="사과")
        session.add(food)
        await session.flush()
        activity = _activity(child.id, subject="축구")
        session.add(activity)
        await session.flush()

        result = await store.list_unembedded(child_id=child.id)

        assert len(result) == 2
        # 먼저 저장된 food 가 앞
        assert result[0].domain == "food"
        assert result[1].domain == "activity"


# ---------------------------------------------------------------------------
# save_embeddings
# ---------------------------------------------------------------------------


class TestSaveEmbeddings:
    async def test_벡터_저장_후_조회_가능(self, session, family, store):
        _, child = family
        obs = _food(child.id)
        session.add(obs)
        await session.flush()

        vec = [0.5] * 1536
        await store.save_embeddings(vectors={("food", str(obs.id)): vec})

        await session.refresh(obs)
        assert obs.embedding is not None
        assert len(obs.embedding) == 1536


# ---------------------------------------------------------------------------
# list_unlinked — active + embedding 있음 + affinity_id 없음
# ---------------------------------------------------------------------------


class TestListUnlinked:
    async def test_벡터_있고_연결_안_된_관찰만_반환(self, session, family, store):
        _, child = family
        vec = [0.1] * 1536
        # 벡터 있고 affinity_id 없음
        obs1 = _food(child.id, subject="사과", embedding=vec)
        # 벡터 있고 affinity_id 있음 → 제외
        profile = ProfileAffinity(
            child_id=child.id, merge_key="배", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=0.5,
            last_observed_on=date(2026, 9, 26),
        )
        session.add(profile)
        await session.flush()
        obs2 = _food(child.id, subject="배", embedding=vec, affinity_id=profile.id)
        session.add_all([obs1, obs2])
        await session.flush()

        result = await store.list_unlinked(child_id=child.id)

        ids = [item.id for item in result]
        assert str(obs1.id) in ids
        assert str(obs2.id) not in ids


# ---------------------------------------------------------------------------
# list_profiles — archived 포함, created_at+id 오름차순
# ---------------------------------------------------------------------------


class TestListProfiles:
    async def test_같은_도메인_polarity만_반환_archived_포함(self, session, family, store):
        _, child = family
        p1 = ProfileAffinity(
            child_id=child.id, merge_key="사과", domain=MemoryDomain.FOOD,
            state=ProfileState.CONFIRMED, polarity=1, strength=0.5,
            last_observed_on=date(2026, 9, 26),
        )
        p2 = ProfileAffinity(
            child_id=child.id, merge_key="당근", domain=MemoryDomain.FOOD,
            state=ProfileState.ARCHIVED, polarity=1, strength=0.3,
            last_observed_on=date(2026, 9, 1),
        )
        # 다른 polarity → 제외
        p3 = ProfileAffinity(
            child_id=child.id, merge_key="피망", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=-1, strength=0.5,
            last_observed_on=date(2026, 9, 26),
        )
        session.add_all([p1, p2, p3])
        await session.flush()

        result = await store.list_profiles(child_id=child.id, domain="food", polarity=1)

        ids = [item.id for item in result]
        assert str(p1.id) in ids
        assert str(p2.id) in ids  # archived 포함
        assert str(p3.id) not in ids  # 다른 polarity


# ---------------------------------------------------------------------------
# link — affinity_id 채우기
# ---------------------------------------------------------------------------


class TestLink:
    async def test_관찰에_affinity_id_설정(self, session, family, store):
        _, child = family
        profile = ProfileAffinity(
            child_id=child.id, merge_key="사과", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=0.5,
            last_observed_on=date(2026, 9, 26),
        )
        session.add(profile)
        obs = _food(child.id, embedding=[0.1] * 1536)
        session.add(obs)
        await session.flush()

        await store.link(domain="food", observation_id=str(obs.id), affinity_id=str(profile.id))

        await session.refresh(obs)
        assert obs.affinity_id == profile.id


# ---------------------------------------------------------------------------
# create_profile — 새 candidate
# ---------------------------------------------------------------------------


class TestCreateProfile:
    async def test_candidate_상태로_생성(self, session, family, store):
        _, child = family

        result = await store.create_profile(
            child_id=child.id,
            domain="food",
            polarity=1,
            merge_key="사과",
            embedding=[0.1] * 1536,
            last_observed_on=date(2026, 9, 26),
        )

        assert result.state == "candidate"
        assert result.merge_key == "사과"
        # DB 에도 실제 저장됨
        row = await session.get(ProfileAffinity, result.id)
        assert row is not None


# ---------------------------------------------------------------------------
# record_uncertain / clear_hold
# ---------------------------------------------------------------------------


class TestHoldRecords:
    async def test_uncertain_횟수가_누적된다(self, session, family, store):
        _, child = family
        obs = _food(child.id, embedding=[0.1] * 1536)
        session.add(obs)
        await session.flush()

        count1 = await store.record_uncertain(
            domain="food", observation_id=str(obs.id), subject_hash="abc",
        )
        count2 = await store.record_uncertain(
            domain="food", observation_id=str(obs.id), subject_hash="abc",
        )

        assert count1 == 1
        assert count2 == 2

    async def test_subject_hash_바뀌면_1부터_다시(self, session, family, store):
        _, child = family
        obs = _food(child.id, embedding=[0.1] * 1536)
        session.add(obs)
        await session.flush()

        await store.record_uncertain(domain="food", observation_id=str(obs.id), subject_hash="aaa")
        await store.record_uncertain(domain="food", observation_id=str(obs.id), subject_hash="aaa")
        count = await store.record_uncertain(
            domain="food", observation_id=str(obs.id), subject_hash="bbb",
        )

        assert count == 1  # 리셋

    async def test_clear_hold_후_기록_없음(self, session, family, store):
        _, child = family
        obs = _food(child.id, embedding=[0.1] * 1536)
        session.add(obs)
        await session.flush()

        await store.record_uncertain(domain="food", observation_id=str(obs.id), subject_hash="abc")
        await store.clear_hold(domain="food", observation_id=str(obs.id))

        # 다시 기록하면 1부터
        count = await store.record_uncertain(
            domain="food", observation_id=str(obs.id), subject_hash="abc",
        )
        assert count == 1
