"""시나리오 기반 통합 테스트 — 실제 부모 행동 패턴 시뮬레이션.

매 단계마다 state · strength · last_observed_on 을 추적한다.
승격 윈도우 14일, archived 윈도우 21일 기준.
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
    child = Child(owner_parent_id=owner.id, nickname="시나리오", birth_date=date(2023, 1, 1))
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
    async def embed(self, texts):
        return [[0.1] * 1536 for _ in texts]


class _FakeJudge:
    async def judge(self, *, subject, domain, candidates):
        from app.agents.curator.embedding.judge import JudgeAnswer

        return JudgeAnswer(choice="none", model="fake")


async def _curator_run(session, child_id, today):
    """Curator 임베딩+연결 → recompute 한 번."""
    from app.agents.curator.embedding.linker import link_observations
    from app.domains.memory.curator.db_store import DbCuratorStore
    from app.domains.memory.curator.recompute import recompute_after_linking

    store = DbCuratorStore(session)
    result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child_id)
    if result.affected_profile_ids:
        await recompute_after_linking(session, result=result, today=today)
    return result


def _assert_profile(profile, *, state, strength, last_observed_on, msg=""):
    """state · strength · last_observed_on 세 값을 한 번에 단언한다."""
    prefix = f"[{msg}] " if msg else ""
    assert profile.state == state, f"{prefix}state: {profile.state} != {state}"
    assert profile.strength == pytest.approx(strength, abs=1e-6), (
        f"{prefix}strength: {profile.strength} != {strength}"
    )
    assert profile.last_observed_on == last_observed_on, (
        f"{prefix}last_observed_on: {profile.last_observed_on} != {last_observed_on}"
    )


# ---------------------------------------------------------------------------
# Scenario 1 — 성향 확인: 관찰이 쌓여 confirmed
# ---------------------------------------------------------------------------


class TestScenario1Confirmation:
    async def test_관찰_3건으로_승격(self, session, family):
        _, child = family
        day1 = date(2026, 9, 20)

        # Day 1: 첫 관찰
        session.add(_food(child.id, subject="사과", observed_on=day1))
        await session.flush()
        r = await _curator_run(session, child.id, day1)
        pid = UUID(r.affected_profile_ids[0])
        p = await session.get(ProfileAffinity, pid)
        _assert_profile(
            p, state=ProfileState.CANDIDATE, strength=0.50, last_observed_on=day1, msg="Day1"
        )

        # Day 3: 두 번째 관찰
        day3 = day1 + timedelta(days=2)
        session.add(_food(child.id, subject="사과", observed_on=day3))
        await session.flush()
        await _curator_run(session, child.id, day3)
        await session.refresh(p)
        _assert_profile(
            p, state=ProfileState.CANDIDATE, strength=0.50, last_observed_on=day3, msg="Day3"
        )

        # Day 5: 세 번째 → confirmed
        day5 = day1 + timedelta(days=4)
        session.add(_food(child.id, subject="사과", observed_on=day5))
        await session.flush()
        await _curator_run(session, child.id, day5)
        await session.refresh(p)
        _assert_profile(
            p,
            state=ProfileState.CONFIRMED,
            strength=0.50 * 1.10,
            last_observed_on=day5,
            msg="Day5 승격",
        )


# ---------------------------------------------------------------------------
# Scenario 2 — wrong 교정으로 강등 → 관찰 추가로 복구
# ---------------------------------------------------------------------------


class TestScenario2CorrectionAndRecovery:
    async def test_wrong_강등_후_재승격(self, session, family):
        owner, child = family
        day1 = date(2026, 9, 20)

        # Day 1~5: 3건 → confirmed
        obs_list = []
        for i in range(3):
            obs = _food(child.id, subject="사과", observed_on=day1 + timedelta(days=i * 2))
            session.add(obs)
            obs_list.append(obs)
        await session.flush()
        day5 = day1 + timedelta(days=4)
        r = await _curator_run(session, child.id, day5)
        pid = UUID(r.affected_profile_ids[0])
        p = await session.get(ProfileAffinity, pid)
        s_after_promo = 0.50 * 1.10  # 0.55
        _assert_profile(
            p,
            state=ProfileState.CONFIRMED,
            strength=s_after_promo,
            last_observed_on=day5,
            msg="승격 후",
        )

        # Day 6: obs₁ wrong 교정 → O=2 → candidate
        day6 = day1 + timedelta(days=5)
        from app.domains.memory.profile.service import handle_observation_correction

        await handle_observation_correction(
            session,
            domain="food",
            child_id=child.id,
            observation_id=obs_list[0].id,
            verdict="wrong",
            parent_id=owner.id,
            today=day6,
        )
        await session.refresh(p)
        s_after_correction = s_after_promo * 0.93 * 0.90  # correction × 강등
        # correction 은 profile correction 이 아니라 observation correction 이므로
        # strength 감소는 없고 상태 전이 강등만
        # 아, handle_observation_correction 은 strength 를 안 건드린다.
        # recompute_profile 의 apply_transition 만 적용된다.
        s_after_correction = s_after_promo * 0.90  # 강등 -10%
        _assert_profile(
            p,
            state=ProfileState.CANDIDATE,
            strength=s_after_correction,
            last_observed_on=day5,
            msg="wrong 후 강등",
            # last_observed_on: obs₁(Day1)이 inactive 됐지만 Day3,Day5 관찰이 남아서 Day5 유지
        )

        # Day 7: 새 관찰 → O=3 → confirmed 재승격
        day7 = day1 + timedelta(days=6)
        session.add(_food(child.id, subject="사과", observed_on=day7))
        await session.flush()
        await _curator_run(session, child.id, day7)
        await session.refresh(p)
        s_after_repromo = s_after_correction * 1.10  # 재승격 +10%
        _assert_profile(
            p,
            state=ProfileState.CONFIRMED,
            strength=s_after_repromo,
            last_observed_on=day7,
            msg="재승격",
        )


# ---------------------------------------------------------------------------
# Scenario 3 — 관찰 삭제 후 last_observed_on 갱신
# ---------------------------------------------------------------------------


class TestScenario3DeleteAndLastObserved:
    async def test_최신_관찰_삭제시_last_observed_on_이전으로(self, session, family):
        _, child = family
        day1 = date(2026, 9, 20)
        day3 = day1 + timedelta(days=2)
        day5 = day1 + timedelta(days=4)

        obs_list = []
        for d in [day1, day3, day5]:
            obs = _food(child.id, subject="사과", observed_on=d)
            session.add(obs)
            obs_list.append(obs)
        await session.flush()

        r = await _curator_run(session, child.id, day5)
        pid = UUID(r.affected_profile_ids[0])
        p = await session.get(ProfileAffinity, pid)
        assert p.last_observed_on == day5

        # Day 5 관찰 삭제 (hard delete — PR #178 전이므로)
        from sqlalchemy import delete as sql_delete

        await session.execute(
            sql_delete(ObservationFood).where(ObservationFood.id == obs_list[2].id)
        )
        await session.flush()

        # recompute → last_observed_on 이 Day 3 으로 갱신
        from app.domains.memory.curator.recompute import recompute_after_linking
        from app.agents.curator.embedding.linker import LinkResult

        # 삭제 후 직접 recompute 를 부른다 (실제로는 delete_observation 경로에서 호출)
        from app.domains.memory.profile.repository import get_latest_active_observed_on
        from app.domains.memory.profile.service import recompute_profile

        latest = await get_latest_active_observed_on(session, affinity_id=pid, domain=p.domain)
        assert latest == day3
        p.last_observed_on = latest
        await session.flush()
        await recompute_profile(session, profile_id=pid, today=day5)

        await session.refresh(p)
        assert p.last_observed_on == day3, "삭제 후 Day3 으로 갱신"
        assert p.state == ProfileState.CANDIDATE, "O=2 → candidate"


# ---------------------------------------------------------------------------
# Scenario 4 — archived → 새 관찰로 부활
# ---------------------------------------------------------------------------


class TestScenario4ArchivedRevival:
    async def test_21일_초과_archived_후_부활(self, session, family):
        _, child = family
        day1 = date(2026, 9, 1)

        # Day 1~5: 3건 → confirmed
        for i in range(3):
            session.add(_food(child.id, subject="사과", observed_on=day1 + timedelta(days=i * 2)))
        await session.flush()
        day5 = day1 + timedelta(days=4)
        r = await _curator_run(session, child.id, day5)
        pid = UUID(r.affected_profile_ids[0])
        p = await session.get(ProfileAffinity, pid)
        s_confirmed = 0.50 * 1.10  # 0.55
        _assert_profile(
            p,
            state=ProfileState.CONFIRMED,
            strength=s_confirmed,
            last_observed_on=day5,
            msg="confirmed",
        )

        # Day 27 (22일 후): recompute → archived
        day27 = day5 + timedelta(days=22)
        from app.domains.memory.profile.service import recompute_profile

        await recompute_profile(session, profile_id=pid, today=day27)
        await session.refresh(p)
        s_archived = s_confirmed * 0.90  # 강등
        _assert_profile(
            p,
            state=ProfileState.ARCHIVED,
            strength=s_archived,
            last_observed_on=day5,
            msg="archived",
        )

        # Day 28~30: 새 관찰 3건 → confirmed 부활
        day28 = day27 + timedelta(days=1)
        for i in range(3):
            session.add(_food(child.id, subject="사과", observed_on=day28 + timedelta(days=i)))
        await session.flush()
        day30 = day28 + timedelta(days=2)
        await _curator_run(session, child.id, day30)
        await session.refresh(p)
        s_revived = s_archived * 1.10  # archived → confirmed 부활
        _assert_profile(
            p,
            state=ProfileState.CONFIRMED,
            strength=s_revived,
            last_observed_on=day30,
            msg="부활",
        )


# ---------------------------------------------------------------------------
# Scenario 5 — once_only + wrong 혼합 교정
# ---------------------------------------------------------------------------


class TestScenario5MixedCorrections:
    """observation correction 은 O 만 줄인다 (W 는 profile correction 에서만 증가).
    once_only → stand_alone (O 에서 빠짐, 검색에는 남음)
    wrong → inactive (O 에서 빠짐)
    둘 다 W 를 늘리지 않는다.
    """

    async def test_once_only와_wrong이_O를_줄여_강등(self, session, family):
        owner, child = family
        day1 = date(2026, 9, 20)

        # Day 1~4: 4건 → confirmed (O=4)
        obs_list = []
        for i in range(4):
            obs = _food(child.id, subject="사과", observed_on=day1 + timedelta(days=i))
            session.add(obs)
            obs_list.append(obs)
        await session.flush()
        day4 = day1 + timedelta(days=3)
        r = await _curator_run(session, child.id, day4)
        pid = UUID(r.affected_profile_ids[0])
        p = await session.get(ProfileAffinity, pid)
        s0 = 0.50 * 1.10  # 0.55
        _assert_profile(
            p,
            state=ProfileState.CONFIRMED,
            strength=s0,
            last_observed_on=day4,
            msg="초기 confirmed",
        )

        from app.domains.memory.profile.service import handle_observation_correction

        # Day 5: obs₁ once_only → O=3 (4-1), W=0 → 3 >= 3 → confirmed 유지
        day5 = day1 + timedelta(days=4)
        await handle_observation_correction(
            session,
            domain="food",
            child_id=child.id,
            observation_id=obs_list[0].id,
            verdict="once_only",
            parent_id=owner.id,
            today=day5,
        )
        await session.refresh(p)
        _assert_profile(
            p,
            state=ProfileState.CONFIRMED,
            strength=s0,
            last_observed_on=day4,
            msg="once_only 후 유지",
        )

        # once_only 된 관찰은 stand_alone — 검색에는 남지만 집계에서 빠짐
        await session.refresh(obs_list[0])
        assert obs_list[0].status == ObservationStatus.STAND_ALONE

        # Day 6: obs₂ wrong → O=2 (3-1), W=0 → 2 < 3 → candidate 강등
        day6 = day1 + timedelta(days=5)
        await handle_observation_correction(
            session,
            domain="food",
            child_id=child.id,
            observation_id=obs_list[1].id,
            verdict="wrong",
            parent_id=owner.id,
            today=day6,
        )
        await session.refresh(p)
        s_demoted = s0 * 0.90  # 강등 -10%
        _assert_profile(
            p,
            state=ProfileState.CANDIDATE,
            strength=s_demoted,
            last_observed_on=day4,
            msg="wrong 후 강등",
        )

        # wrong 된 관찰은 inactive
        await session.refresh(obs_list[1])
        assert obs_list[1].status == ObservationStatus.INACTIVE


# ---------------------------------------------------------------------------
# Scenario 6 — 두 성향 독립 전이
# ---------------------------------------------------------------------------


class TestScenario6IndependentProfiles:
    async def test_두_subject가_서로_영향_없이_전이(self, session, family):
        owner, child = family
        day1 = date(2026, 9, 20)

        # 사과: Day 1,3,5 → 3건
        for i in range(3):
            session.add(_food(child.id, subject="사과", observed_on=day1 + timedelta(days=i * 2)))
        # 배: Day 2,4 → 2건
        for i in range(2):
            session.add(_food(child.id, subject="배", observed_on=day1 + timedelta(days=1 + i * 2)))
        await session.flush()

        day5 = day1 + timedelta(days=4)
        r = await _curator_run(session, child.id, day5)

        profiles = {}
        for pid_str in r.affected_profile_ids:
            p = await session.get(ProfileAffinity, UUID(pid_str))
            profiles[p.merge_key] = p

        apple = profiles["사과"]
        pear = profiles["배"]
        _assert_profile(
            apple,
            state=ProfileState.CONFIRMED,
            strength=0.55,
            last_observed_on=day5,
            msg="사과 confirmed",
        )
        _assert_profile(
            pear,
            state=ProfileState.CANDIDATE,
            strength=0.50,
            last_observed_on=day1 + timedelta(days=3),
            msg="배 candidate",
        )

        # 배: Day 6 추가 → O=3 → confirmed
        day6 = day1 + timedelta(days=5)
        session.add(_food(child.id, subject="배", observed_on=day6))
        await session.flush()
        await _curator_run(session, child.id, day6)
        await session.refresh(pear)
        _assert_profile(
            pear,
            state=ProfileState.CONFIRMED,
            strength=0.55,
            last_observed_on=day6,
            msg="배 confirmed",
        )

        # 사과는 변화 없음
        await session.refresh(apple)
        _assert_profile(
            apple,
            state=ProfileState.CONFIRMED,
            strength=0.55,
            last_observed_on=day5,
            msg="사과 그대로",
        )
