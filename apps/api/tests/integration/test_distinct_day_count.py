"""같은 날 관찰은 O=1 로 세는 스펙 — §2 "한 번의 관찰을 성향으로 확정하지 않는다".

입력 한 번에 Memory Agent 가 관찰 여러 건을 만들 수 있다.
같은 날 같은 Profile 에 붙은 관찰은 1일로 세야 바로 승격되지 않는다.
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


def _food(child_id, profile_id, *, observed_on, signals=()):
    return ObservationFood(
        child_id=child_id,
        raw_text="test",
        subject="사과",
        polarity=1,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        status=ObservationStatus.ACTIVE,
        observed_range=Range(observed_on, observed_on + timedelta(days=1)),
        affinity_id=profile_id,
        strong_signals=list(signals),
    )


def _profile(child_id, *, last_observed_on):
    return ProfileAffinity(
        child_id=child_id,
        merge_key="사과::p1",
        domain=MemoryDomain.FOOD,
        state=ProfileState.CANDIDATE,
        polarity=1,
        strength=STRENGTH_DEFAULT,
        last_observed_on=last_observed_on,
    )


class TestDistinctDayCount:
    """count_active_in_window 는 같은 날 관찰을 1일로 센다."""

    async def test_같은날_3건은_O_1(self, session, family):
        """§2 위반 방지: 입력 한 번에 관찰 3건 → 바로 승격 안 됨."""
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        for _ in range(3):
            session.add(_food(child.id, profile.id, observed_on=today))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CANDIDATE  # O=1, 3 미달

    async def test_다른날_3건은_O_3_confirmed(self, session, family):
        """정상 승격은 유지."""
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        for i in range(3):
            session.add(_food(child.id, profile.id, observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CONFIRMED  # O=3

    async def test_같은날_2건_다른날_1건은_O_2(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        # 같은 날 2건
        session.add(_food(child.id, profile.id, observed_on=today))
        session.add(_food(child.id, profile.id, observed_on=today))
        # 다른 날 1건
        session.add(_food(child.id, profile.id, observed_on=today - timedelta(days=1)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CANDIDATE  # O=2, 3 미달

    async def test_같은날_G_다른날_1건은_O_2_confirmed(self, session, family):
        """G + 2일이면 승격."""
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        session.add(_food(child.id, profile.id, observed_on=today, signals=("self_initiated",)))
        session.add(_food(child.id, profile.id, observed_on=today - timedelta(days=1)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CONFIRMED  # O=2, G=true, 2 >= 2

    async def test_같은날_G_2건은_O_1_candidate(self, session, family):
        """G 있어도 같은 날이면 1일 — 승격 미달."""
        _, child = family
        today = date(2026, 9, 26)
        profile = _profile(child.id, last_observed_on=today)
        session.add(profile)
        await session.flush()

        session.add(_food(child.id, profile.id, observed_on=today, signals=("self_initiated",)))
        session.add(_food(child.id, profile.id, observed_on=today, signals=("repeated",)))
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)
        assert result.state == ProfileState.CANDIDATE  # O=1, G=true, 1 < 2
