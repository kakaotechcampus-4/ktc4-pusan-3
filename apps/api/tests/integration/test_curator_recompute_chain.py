"""Curator 연결 → Profile 상태 전이 체인 — 통합 스펙.

Curator 가 관찰을 Profile 에 연결한 뒤 recompute_profile 이 돌아
상태가 올바르게 전이되는지 검증한다.

구현 대상: Phase B (recompute 배선) + Phase C (실행 흐름)
Embedder 와 Judge 는 가짜를 쓴다 — 외부 API 의존 없이 DB 체인만 본다.

PR #178 반영: 연결된 관찰이 soft delete 되면 O 가 줄어 상태가 내려가야 한다.
"""

from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.rules.profile import STRENGTH_DEFAULT


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


def _food(child_id, *, subject="사과", polarity=1, observed_on=date(2026, 9, 26)):
    return ObservationFood(
        child_id=child_id,
        raw_text="test",
        subject=subject,
        polarity=polarity,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(observed_on, observed_on + timedelta(days=1)),
    )


class _FakeEmbedder:
    """모든 텍스트에 고정 벡터를 반환한다."""

    async def embed(self, texts):
        return [[0.1] * 1536 for _ in texts]


class _FakeJudge:
    """항상 none 을 답한다 — 이름이 같으면 코드가 먼저 잡으므로 판정기는 불리지 않는다."""

    async def judge(self, *, subject, domain, candidates):
        from app.agents.curator.embedding.judge import JudgeAnswer

        return JudgeAnswer(choice="none", model="fake")


# ---------------------------------------------------------------------------
# Phase B — 연결 후 recompute 체인
# ---------------------------------------------------------------------------


class TestLinkThenRecompute:
    """Curator 연결 → last_observed_on 갱신 → recompute_profile 체인."""

    async def test_같은_subject_3건_연결하면_confirmed_승격(self, session, family):
        """관찰 3건이 같은 이름으로 들어오면:
        1. Curator 가 첫 관찰로 candidate Profile 생성
        2. 나머지 2건을 같은 Profile 에 연결
        3. recompute 가 O=3 으로 confirmed 전이
        """
        _, child = family
        today = date(2026, 9, 26)
        observations = []
        for i in range(3):
            obs = _food(child.id, subject="사과", observed_on=today - timedelta(days=i))
            session.add(obs)
            observations.append(obs)
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        # Curator 결과: 1개 생성 + 2개 연결
        assert len(result.created_profile_ids) == 1
        profile_id = result.affected_profile_ids[0]

        # Phase B: recompute 배선 함수 호출
        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        profile = await session.get(ProfileAffinity, UUID(profile_id))
        assert profile is not None
        assert profile.state == ProfileState.CONFIRMED
        assert profile.strength == pytest.approx(STRENGTH_DEFAULT * 1.10)

    async def test_2건이면_candidate_유지(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        for i in range(2):
            session.add(_food(child.id, subject="배", observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        profile_id = result.affected_profile_ids[0]
        profile = await session.get(ProfileAffinity, UUID(profile_id))
        assert profile.state == ProfileState.CANDIDATE

    async def test_연결_후_last_observed_on_갱신(self, session, family):
        """연결 후 Profile 의 last_observed_on 이 관찰 중 가장 최근 날짜를 반영한다."""
        _, child = family
        today = date(2026, 9, 26)
        dates = [today - timedelta(days=2), today - timedelta(days=1), today]
        for d in dates:
            session.add(_food(child.id, subject="감", observed_on=d))
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        profile_id = result.affected_profile_ids[0]
        profile = await session.get(ProfileAffinity, UUID(profile_id))
        assert profile.last_observed_on == today


# ---------------------------------------------------------------------------
# Phase B — 기존 Profile 에 연결
# ---------------------------------------------------------------------------


class TestLinkToExistingProfile:
    async def test_기존_candidate에_관찰_추가로_confirmed_전이(self, session, family):
        """이미 candidate Profile 이 있고 관찰 1건이 연결된 상태에서,
        같은 이름의 관찰 2건이 추가되면 O=3 으로 confirmed."""
        _, child = family
        today = date(2026, 9, 26)

        # 기존 Profile + 관찰 1건 (이미 연결됨)
        profile = ProfileAffinity(
            child_id=child.id, merge_key="사과", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=STRENGTH_DEFAULT,
            last_observed_on=today - timedelta(days=5),
            embedding=[0.1] * 1536,
        )
        session.add(profile)
        await session.flush()
        existing = _food(child.id, subject="사과", observed_on=today - timedelta(days=5))
        existing.affinity_id = profile.id
        existing.embedding = [0.1] * 1536
        session.add(existing)
        await session.flush()

        # 새 관찰 2건 (아직 미연결)
        for i in range(2):
            session.add(_food(child.id, subject="사과", observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        # 새 Profile 은 안 만들어야 한다 — 이름이 같으므로 기존에 연결
        assert len(result.created_profile_ids) == 0

        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        await session.refresh(profile)
        assert profile.state == ProfileState.CONFIRMED
        assert profile.last_observed_on == today


# ---------------------------------------------------------------------------
# Phase B — recompute 멱등성
# ---------------------------------------------------------------------------


class TestRecomputeIdempotency:
    async def test_같은_결과로_두_번_돌려도_상태_동일(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        for i in range(3):
            session.add(_food(child.id, subject="사과", observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        profile_id = result.affected_profile_ids[0]
        profile = await session.get(ProfileAffinity, UUID(profile_id))
        state1, strength1 = profile.state, profile.strength

        # 두 번째
        await recompute_after_linking(session, result=result, today=today)
        await session.refresh(profile)

        assert profile.state == state1
        assert profile.strength == strength1


# ---------------------------------------------------------------------------
# PR #178 — 연결된 관찰 삭제 후 상태 변화
# ---------------------------------------------------------------------------


_HAS_DELETED = hasattr(ObservationStatus, "DELETED")


class TestDeleteLinkedObservation:
    """연결된 관찰이 soft delete 되면 O 가 줄어 Profile 상태가 내려가야 한다."""

    @pytest.mark.xfail(not _HAS_DELETED, reason="PR #178 머지 후 ObservationStatus.DELETED 가 생기면 통과")
    async def test_confirmed_에서_관찰_삭제로_candidate_강등(self, session, family):
        """O=3 confirmed → 관찰 1건 deleted → O=2 → candidate."""
        _, child = family
        today = date(2026, 9, 26)
        observations = []
        for i in range(3):
            obs = _food(child.id, subject="사과", observed_on=today - timedelta(days=i))
            session.add(obs)
            observations.append(obs)
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        profile_id = result.affected_profile_ids[0]
        profile = await session.get(ProfileAffinity, UUID(profile_id))
        assert profile.state == ProfileState.CONFIRMED

        # 관찰 1건 soft delete
        observations[0].status = ObservationStatus.DELETED
        await session.flush()

        # recompute 다시 — 이제 O=2
        from app.domains.memory.profile.service import recompute_profile

        await recompute_profile(session, profile_id=UUID(profile_id), today=today)

        await session.refresh(profile)
        assert profile.state == ProfileState.CANDIDATE

    @pytest.mark.xfail(not _HAS_DELETED, reason="PR #178 머지 후 ObservationStatus.DELETED 가 생기면 통과")
    async def test_모든_관찰_삭제해도_profile_자체는_남는다(self, session, family):
        """관찰이 모두 deleted 돼도 Profile 행은 남는다 (soft delete 이므로 FK SET NULL 안 됨).
        상태는 O=0 이므로 candidate, 21일 지나면 archived."""
        _, child = family
        today = date(2026, 9, 26)
        observations = []
        for i in range(3):
            obs = _food(child.id, subject="사과", observed_on=today - timedelta(days=i))
            session.add(obs)
            observations.append(obs)
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        profile_id = result.affected_profile_ids[0]

        # 전부 삭제
        for obs in observations:
            obs.status = ObservationStatus.DELETED
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        await recompute_profile(session, profile_id=UUID(profile_id), today=today)

        profile = await session.get(ProfileAffinity, UUID(profile_id))
        assert profile is not None  # 행은 남아있다
        assert profile.state == ProfileState.CANDIDATE  # O=0


# ---------------------------------------------------------------------------
# correction + recompute 체인
# ---------------------------------------------------------------------------


class TestCorrectionAfterLinking:
    """연결된 관찰에 correction 이 들어오면 상태가 재계산돼야 한다."""

    async def test_wrong_교정으로_O가_줄어_candidate_강등(self, session, family):
        """O=3 confirmed → 관찰 1건 wrong(inactive) → O=2 → candidate."""
        _, child = family
        today = date(2026, 9, 26)
        observations = []
        for i in range(3):
            obs = _food(child.id, subject="사과", observed_on=today - timedelta(days=i))
            session.add(obs)
            observations.append(obs)
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        profile_id = result.affected_profile_ids[0]
        profile = await session.get(ProfileAffinity, UUID(profile_id))
        assert profile.state == ProfileState.CONFIRMED

        # wrong correction
        owner, _ = family
        from app.domains.memory.profile.service import handle_observation_correction

        await handle_observation_correction(
            session,
            domain="food",
            child_id=child.id,
            observation_id=observations[0].id,
            verdict="wrong",
            parent_id=owner.id,
            today=today,
        )

        await session.refresh(profile)
        assert profile.state == ProfileState.CANDIDATE  # O=2

    async def test_once_only_교정은_집계에서_빠지고_검색은_남는다(self, session, family):
        """once_only → stand_alone: Curator 집계(O)에서 빠지지만 검색에는 남는다."""
        _, child = family
        today = date(2026, 9, 26)
        observations = []
        for i in range(3):
            obs = _food(child.id, subject="사과", observed_on=today - timedelta(days=i))
            session.add(obs)
            observations.append(obs)
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        from app.domains.memory.curator.recompute import recompute_after_linking

        await recompute_after_linking(session, result=result, today=today)

        # once_only correction
        owner, _ = family
        from app.domains.memory.profile.service import handle_observation_correction

        await handle_observation_correction(
            session,
            domain="food",
            child_id=child.id,
            observation_id=observations[0].id,
            verdict="once_only",
            parent_id=owner.id,
            today=today,
        )

        profile_id = result.affected_profile_ids[0]
        profile = await session.get(ProfileAffinity, UUID(profile_id))
        assert profile.state == ProfileState.CANDIDATE  # O=2

        # 관찰 자체는 stand_alone 으로 남아있다
        await session.refresh(observations[0])
        assert observations[0].status == ObservationStatus.STAND_ALONE
