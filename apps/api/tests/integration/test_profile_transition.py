"""프로필 상태 전이 서비스 — DB 통합 테스트.

session fixture 로 트랜잭션 격리. 재계산 멱등성과 correction→재계산 체인을 검증한다.
"""

from datetime import date, timedelta

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


def _profile(child_id, *, merge_key="사과::p1", last_observed_on, state="candidate"):
    return ProfileAffinity(
        child_id=child_id,
        merge_key=merge_key,
        domain=MemoryDomain.FOOD,
        state=ProfileState(state),
        polarity=1,
        strength=STRENGTH_DEFAULT,
        last_observed_on=last_observed_on,
    )


def _food_obs(child_id, affinity_id, *, observed_on, status=ObservationStatus.ACTIVE, signals=()):
    return ObservationFood(
        child_id=child_id,
        raw_text="test",
        subject="사과",
        polarity=1,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        status=status,
        observed_range=Range(observed_on, observed_on + timedelta(days=1)),
        affinity_id=affinity_id,
        strong_signals=list(signals),
    )


# ---------------------------------------------------------------------------
# recompute_profile
# ---------------------------------------------------------------------------


class TestRecomputeProfile:
    async def test_active_관찰_3건이면_confirmed_승격(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        for i in range(3):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)

        assert result.state == ProfileState.CONFIRMED
        assert result.strength == pytest.approx(STRENGTH_DEFAULT * 1.10)

    async def test_재계산_두_번_돌려도_결과_동일(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()
        for i in range(3):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        r1 = await recompute_profile(session, profile_id=profile.id, today=today)
        state1, str1 = r1.state, r1.strength

        r2 = await recompute_profile(session, profile_id=profile.id, today=today)
        assert r2.state == state1
        assert r2.strength == str1

    async def test_21일_초과면_archived(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        old = today - timedelta(days=22)
        profile = _profile(child.id, last_observed_on=old)
        session.add(profile)
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.ARCHIVED

    async def test_관찰_2건과_strong_signal이면_confirmed(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        session.add(
            _food_obs(
                child.id,
                profile.id,
                observed_on=today,
                signals=("self_initiated",),
            )
        )
        session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=1)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CONFIRMED

    async def test_윈도우_밖_관찰은_O에_안_잡힌다(self, session, family):
        """7일 윈도우 밖 관찰이 있어도 O 에 포함되지 않는다"""
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        # 윈도우 안 2건
        for i in range(2):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        # 윈도우 밖 1건 (8일 전)
        session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=8)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CANDIDATE  # O=2, G=False

    async def test_stand_alone_관찰은_O에_안_잡힌다(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        for i in range(2):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        session.add(
            _food_obs(
                child.id,
                profile.id,
                observed_on=today - timedelta(days=2),
                status=ObservationStatus.STAND_ALONE,
            )
        )
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CANDIDATE  # O=2 (stand_alone 제외)

    async def test_inactive_관찰은_O에_안_잡힌다(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        for i in range(2):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        session.add(
            _food_obs(
                child.id,
                profile.id,
                observed_on=today - timedelta(days=2),
                status=ObservationStatus.INACTIVE,
            )
        )
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CANDIDATE

    async def test_archived에서_새_관찰_3건이면_confirmed_부활(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today, state="archived")
        profile.strength = 0.4
        session.add(profile)
        await session.flush()

        for i in range(3):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CONFIRMED
        assert result.strength == pytest.approx(0.4 * 1.10)


# ---------------------------------------------------------------------------
# correction → 재계산 체인
# ---------------------------------------------------------------------------


class TestObservationCorrection:
    async def test_wrong이면_inactive_변경_후_profile_재계산(self, session, family):
        owner, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        observations = []
        for i in range(3):
            obs = _food_obs(child.id, profile.id, observed_on=today - timedelta(days=i))
            session.add(obs)
            observations.append(obs)
        await session.flush()

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

        await session.refresh(observations[0])
        assert observations[0].status == ObservationStatus.INACTIVE

        await session.refresh(profile)
        assert profile.state == ProfileState.CANDIDATE  # O=2

    async def test_once_only면_stand_alone_변경_후_profile_재계산(self, session, family):
        owner, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        observations = []
        for i in range(3):
            obs = _food_obs(child.id, profile.id, observed_on=today - timedelta(days=i))
            session.add(obs)
            observations.append(obs)
        await session.flush()

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

        await session.refresh(observations[0])
        assert observations[0].status == ObservationStatus.STAND_ALONE

        await session.refresh(profile)
        assert profile.state == ProfileState.CANDIDATE  # O=2


class TestProfileCorrection:
    async def test_wrong이면_strength_감소_후_재계산(self, session, family):
        owner, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()
        for i in range(3):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        await session.flush()

        # 먼저 승격
        from app.domains.memory.profile.service import (
            handle_profile_correction,
            recompute_profile,
        )

        await recompute_profile(session, profile_id=profile.id, today=today)
        await session.refresh(profile)
        assert profile.state == ProfileState.CONFIRMED
        str_before = profile.strength

        # wrong correction → W=1, O=3 → 3 < 3+1=4 → candidate
        await handle_profile_correction(
            session,
            child_id=child.id,
            profile_id=profile.id,
            verdict="wrong",
            parent_id=owner.id,
            today=today,
        )
        await session.refresh(profile)
        assert profile.state == ProfileState.CANDIDATE
        # strength: str_before * 0.93 (correction) * 0.90 (강등)
        assert profile.strength == pytest.approx(str_before * 0.93 * 0.90)

    async def test_outdated면_strength만_감소(self, session, family):
        owner, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()
        for i in range(4):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.domains.memory.profile.service import (
            handle_profile_correction,
            recompute_profile,
        )

        await recompute_profile(session, profile_id=profile.id, today=today)
        await session.refresh(profile)
        assert profile.state == ProfileState.CONFIRMED  # O=4
        str_before = profile.strength

        # outdated → W 는 outdated 안 셈 → O=4 >= 3+0 → confirmed 유지
        await handle_profile_correction(
            session,
            child_id=child.id,
            profile_id=profile.id,
            verdict="outdated",
            parent_id=owner.id,
            today=today,
        )
        await session.refresh(profile)
        assert profile.state == ProfileState.CONFIRMED  # 유지
        assert profile.strength == pytest.approx(str_before * 0.95)

    async def test_need_more_observation이면_strength만_감소(self, session, family):
        owner, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()
        for i in range(4):
            session.add(_food_obs(child.id, profile.id, observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.domains.memory.profile.service import (
            handle_profile_correction,
            recompute_profile,
        )

        await recompute_profile(session, profile_id=profile.id, today=today)
        str_before = profile.strength

        await handle_profile_correction(
            session,
            child_id=child.id,
            profile_id=profile.id,
            verdict="need_more_observation",
            parent_id=owner.id,
            today=today,
        )
        await session.refresh(profile)
        assert profile.state == ProfileState.CONFIRMED
        assert profile.strength == pytest.approx(str_before * 0.97)


# ---------------------------------------------------------------------------
# 긍정/부정 profile 분리
# ---------------------------------------------------------------------------


class TestPolaritySeparation:
    async def test_같은_subject라도_polarity별_profile_분리(self, session, family):
        """merge_key 에 polarity 포함 → 별개 profile"""
        _, child = family
        today = date(2026, 9, 26)

        pos = _profile(child.id, merge_key="사과::p1", last_observed_on=today)
        neg = ProfileAffinity(
            child_id=child.id,
            merge_key="사과::p-1",
            domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE,
            polarity=-1,
            strength=STRENGTH_DEFAULT,
            last_observed_on=today,
        )
        session.add_all([pos, neg])
        await session.flush()

        # 긍정 3건
        for i in range(3):
            session.add(_food_obs(child.id, pos.id, observed_on=today - timedelta(days=i)))
        # 부정 1건
        session.add(
            ObservationFood(
                child_id=child.id,
                raw_text="test",
                subject="사과",
                polarity=-1,
                confidence_source=ConfidenceSource.PARENT_DIRECT,
                status=ObservationStatus.ACTIVE,
                observed_range=Range(today, today + timedelta(days=1)),
                affinity_id=neg.id,
            )
        )
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        pos_r = await recompute_profile(session, profile_id=pos.id, today=today)
        neg_r = await recompute_profile(session, profile_id=neg.id, today=today)

        assert pos_r.state == ProfileState.CONFIRMED
        assert neg_r.state == ProfileState.CANDIDATE


# ---------------------------------------------------------------------------
# 조회 필터
# ---------------------------------------------------------------------------


class TestListConfirmedProfiles:
    async def test_confirmed만_반환(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        confirmed = _profile(child.id, merge_key="사과::p1", last_observed_on=today)
        confirmed.state = ProfileState.CONFIRMED
        candidate = _profile(child.id, merge_key="배::p1", last_observed_on=today)
        session.add_all([confirmed, candidate])
        await session.flush()

        from app.domains.memory.profile.repository import list_confirmed_profiles

        results = await list_confirmed_profiles(session, child_id=child.id)
        ids = {p.id for p in results}
        assert confirmed.id in ids
        assert candidate.id not in ids

    async def test_strength_내림차순(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        for i, s in enumerate([0.3, 0.8, 0.5]):
            p = _profile(child.id, merge_key=f"item{i}::p1", last_observed_on=today)
            p.state = ProfileState.CONFIRMED
            p.strength = s
            session.add(p)
        await session.flush()

        from app.domains.memory.profile.repository import list_confirmed_profiles

        results = await list_confirmed_profiles(
            session, child_id=child.id, domain=MemoryDomain.FOOD
        )
        strengths = [p.strength for p in results]
        assert strengths == sorted(strengths, reverse=True)

    async def test_domain당_10개_제한(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        for i in range(15):
            p = _profile(child.id, merge_key=f"item{i}::p1", last_observed_on=today)
            p.state = ProfileState.CONFIRMED
            p.strength = 0.5 + i * 0.01
            session.add(p)
        await session.flush()

        from app.domains.memory.profile.repository import list_confirmed_profiles

        results = await list_confirmed_profiles(
            session, child_id=child.id, domain=MemoryDomain.FOOD
        )
        assert len(results) <= 10
