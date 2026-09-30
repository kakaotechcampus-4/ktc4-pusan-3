"""DB CuratorStore — Protocol 메서드가 DB 에서 올바르게 동작하는지 검증한다.

인메모리 구현(inmemory.py)과 동일한 동작을 보장한다.
구현 대상: app/domains/memory/curator/db_store.py (Phase A)

PR #178 (observation soft delete) 반영:
  deleted 관찰은 list_unembedded · list_unlinked 에서 제외돼야 한다.
  관찰이 soft delete 되면 보류 기록(hold)도 정리돼야 한다.
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

    async def test_deleted_관찰은_제외(self, session, family, store):
        """PR #178: soft delete 된 관찰은 임베딩 대상이 아니다."""
        _, child = family
        active = _food(child.id, subject="사과")
        deleted = _food(child.id, subject="배", status=ObservationStatus.DELETED)
        session.add_all([active, deleted])
        await session.flush()

        result = await store.list_unembedded(child_id=child.id)

        ids = [item.id for item in result]
        assert str(active.id) in ids
        assert str(deleted.id) not in ids

    async def test_stand_alone_관찰은_제외(self, session, family, store):
        """stand_alone 은 검색에는 남지만 Curator 집계 대상이 아니다."""
        _, child = family
        active = _food(child.id, subject="사과")
        stand_alone = _food(child.id, subject="배", status=ObservationStatus.STAND_ALONE)
        session.add_all([active, stand_alone])
        await session.flush()

        result = await store.list_unembedded(child_id=child.id)

        ids = [item.id for item in result]
        assert str(active.id) in ids
        assert str(stand_alone.id) not in ids

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
    async def test_deleted_관찰은_벡터_있어도_제외(self, session, family, store):
        """PR #178: soft delete 된 관찰은 연결 대상이 아니다."""
        _, child = family
        vec = [0.1] * 1536
        active = _food(child.id, subject="사과", embedding=vec)
        deleted = _food(child.id, subject="배", embedding=vec, status=ObservationStatus.DELETED)
        session.add_all([active, deleted])
        await session.flush()

        result = await store.list_unlinked(child_id=child.id)

        ids = [item.id for item in result]
        assert str(active.id) in ids
        assert str(deleted.id) not in ids

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

    async def test_clear_hold_기록_없어도_에러_안_남(self, session, family, store):
        _, child = family
        obs = _food(child.id, embedding=[0.1] * 1536)
        session.add(obs)
        await session.flush()

        # 보류 기록이 없어도 예외 없이 통과
        await store.clear_hold(domain="food", observation_id=str(obs.id))


# ---------------------------------------------------------------------------
# soft delete + hold 정리 (PR #178 대응)
# ---------------------------------------------------------------------------


class TestSoftDeleteHoldCleanup:
    """PR #178: observation 이 soft delete 되면 보류 기록도 정리돼야 한다.

    FK CASCADE 가 soft delete 에서는 발동하지 않으므로
    삭제 시점에 코드가 hold 행을 지워야 한다.
    """

    async def test_관찰_삭제_시_보류_기록도_사라진다(self, session, family, store):
        """soft delete 후 해당 관찰의 hold 행이 남아 있으면 안 된다."""
        from sqlalchemy import select

        _, child = family
        obs = _food(child.id, embedding=[0.1] * 1536)
        session.add(obs)
        await session.flush()

        # 보류 기록 생성
        await store.record_uncertain(domain="food", observation_id=str(obs.id), subject_hash="abc")

        # soft delete
        from app.domains.memory.observation.repository import ObservationDomain, delete_observation

        await delete_observation(session, domain=ObservationDomain.FOOD,
                                 child_id=child.id, observation_id=obs.id)

        # hold 행이 사라져야 한다
        hold = await session.scalar(
            select(ObservationLinkHold).where(ObservationLinkHold.domain == "food",
                ObservationLinkHold.observation_id == obs.id)
        )
        assert hold is None

    @pytest.mark.xfail(
        not hasattr(ObservationStatus, "DELETED"),
        reason="PR #178 머지 후 ObservationStatus.DELETED 가 생기면 통과",
    )
    async def test_삭제된_관찰은_다음_Curator_실행에서_무시(self, session, family, store):
        """deleted 관찰은 list_unembedded / list_unlinked 모두에서 빠져야 한다."""
        _, child = family
        obs = _food(child.id, subject="사과")
        session.add(obs)
        await session.flush()

        # soft delete
        obs.status = ObservationStatus.DELETED
        await session.flush()

        assert await store.list_unembedded(child_id=child.id) == []
        assert await store.list_unlinked(child_id=child.id) == []


# ---------------------------------------------------------------------------
# 다른 아이의 데이터 격리
# ---------------------------------------------------------------------------


class TestChildIsolation:
    async def test_다른_아이_관찰은_조회되지_않는다(self, session, store):
        p = Parent()
        session.add(p)
        await session.flush()
        child_a = Child(owner_parent_id=p.id, nickname="A", birth_date=date(2023, 1, 1))
        child_b = Child(owner_parent_id=p.id, nickname="B", birth_date=date(2023, 6, 1))
        session.add_all([child_a, child_b])
        await session.flush()

        obs_a = _food(child_a.id, subject="사과")
        obs_b = _food(child_b.id, subject="배")
        session.add_all([obs_a, obs_b])
        await session.flush()

        result = await store.list_unembedded(child_id=child_a.id)
        ids = [item.id for item in result]
        assert str(obs_a.id) in ids
        assert str(obs_b.id) not in ids

    async def test_다른_아이_profile은_후보에_안_나온다(self, session, store):
        p = Parent()
        session.add(p)
        await session.flush()
        child_a = Child(owner_parent_id=p.id, nickname="A", birth_date=date(2023, 1, 1))
        child_b = Child(owner_parent_id=p.id, nickname="B", birth_date=date(2023, 6, 1))
        session.add_all([child_a, child_b])
        await session.flush()

        pa = ProfileAffinity(
            child_id=child_a.id, merge_key="사과", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=0.5,
            last_observed_on=date(2026, 9, 26),
        )
        pb = ProfileAffinity(
            child_id=child_b.id, merge_key="배", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=0.5,
            last_observed_on=date(2026, 9, 26),
        )
        session.add_all([pa, pb])
        await session.flush()

        result = await store.list_profiles(child_id=child_a.id, domain="food", polarity=1)
        ids = [item.id for item in result]
        assert str(pa.id) in ids
        assert str(pb.id) not in ids


async def test_hold_저장과_해제는_같은_UUID의_도메인을_구분한다(session, family, store):
    from uuid import uuid4

    from sqlalchemy import select

    from app.domains.memory.observation.models import ObservationEducation

    _, child = family
    observation_id = uuid4()
    session.add_all([
        _food(child.id, id=observation_id),
        _activity(child.id, id=observation_id),
        ObservationEducation(
            id=observation_id, child_id=child.id, raw_text="test", subject="수학",
            topic="수학", confidence_source=ConfidenceSource.PARENT_DIRECT,
            observed_range=Range(date(2026, 9, 26), date(2026, 9, 27)),
        ),
    ])
    await session.flush()

    for domain in ("food", "activity", "education"):
        assert await store.record_uncertain(
            domain=domain, observation_id=str(observation_id), subject_hash="same",
        ) == 1
    assert await store.record_uncertain(
        domain="food", observation_id=str(observation_id), subject_hash="same",
    ) == 2
    holds = (await session.scalars(select(ObservationLinkHold).where(
        ObservationLinkHold.observation_id == observation_id,
    ))).all()
    assert {h.domain: h.uncertain_count for h in holds} == {
        "food": 2, "activity": 1, "education": 1,
    }
    assert all(h.child_id == child.id for h in holds)

    await store.clear_hold(domain="food", observation_id=str(observation_id))
    remaining = (await session.scalars(select(ObservationLinkHold.domain).where(
        ObservationLinkHold.observation_id == observation_id,
    ))).all()
    assert set(remaining) == {"activity", "education"}
