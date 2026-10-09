"""교정 시 Curator 보류 기록(hold) 정리 스펙.

보류 중인 관찰에 wrong/once_only 교정이 들어오면 hold 행을 삭제한다.
교정된 관찰은 다시 연결 대상이 되지 않으므로, hold 가 남아있을 이유가 없다.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationLinkHold,
    ObservationStatus,
)


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


def _food_with_embedding(child_id, *, observed_on=date(2026, 9, 26)):
    return ObservationFood(
        child_id=child_id,
        raw_text="test",
        subject="사과",
        polarity=1,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(observed_on, observed_on + timedelta(days=1)),
        embedding=[0.1] * 1536,
    )


class TestCorrectionClearsHold:
    """교정 시 hold 행이 삭제되는지."""

    async def test_wrong_교정시_hold_삭제(self, session, family):
        """보류 중(hold O, affinity_id=NULL) + wrong → hold 삭제."""
        owner, child = family
        obs = _food_with_embedding(child.id)
        session.add(obs)
        await session.flush()

        # hold 생성
        session.add(
            ObservationLinkHold(
                child_id=child.id,
                domain="food",
                observation_id=obs.id,
                uncertain_count=1,
                subject_hash="abc",
            )
        )
        await session.flush()

        from app.domains.memory.profile.service import handle_observation_correction

        await handle_observation_correction(
            session,
            domain="food",
            child_id=child.id,
            observation_id=obs.id,
            verdict="wrong",
            parent_id=owner.id,
            today=date(2026, 9, 26),
        )

        hold = await session.scalar(
            select(ObservationLinkHold).where(
                ObservationLinkHold.domain == "food", ObservationLinkHold.observation_id == obs.id
            )
        )
        assert hold is None

    async def test_once_only_교정시_hold_삭제(self, session, family):
        """보류 중 + once_only → hold 삭제."""
        owner, child = family
        obs = _food_with_embedding(child.id)
        session.add(obs)
        await session.flush()

        session.add(
            ObservationLinkHold(
                child_id=child.id,
                domain="food",
                observation_id=obs.id,
                uncertain_count=2,
                subject_hash="abc",
            )
        )
        await session.flush()

        from app.domains.memory.profile.service import handle_observation_correction

        await handle_observation_correction(
            session,
            domain="food",
            child_id=child.id,
            observation_id=obs.id,
            verdict="once_only",
            parent_id=owner.id,
            today=date(2026, 9, 26),
        )

        hold = await session.scalar(
            select(ObservationLinkHold).where(
                ObservationLinkHold.domain == "food", ObservationLinkHold.observation_id == obs.id
            )
        )
        assert hold is None

    async def test_hold_없는_관찰에_교정해도_에러_없음(self, session, family):
        """hold 가 없어도 교정은 정상 동작."""
        owner, child = family
        obs = _food_with_embedding(child.id)
        session.add(obs)
        await session.flush()

        from app.domains.memory.profile.service import handle_observation_correction

        # hold 없이 교정 — 에러 없어야 함
        await handle_observation_correction(
            session,
            domain="food",
            child_id=child.id,
            observation_id=obs.id,
            verdict="wrong",
            parent_id=owner.id,
            today=date(2026, 9, 26),
        )

        await session.refresh(obs)
        assert obs.status == ObservationStatus.INACTIVE
