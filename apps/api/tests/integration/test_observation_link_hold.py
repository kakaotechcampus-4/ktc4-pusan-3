"""observation_link_hold — DB 가 보장하는 두 가지만 본다.

    - 관찰이 지워지면 보류 기록도 지워진다 (FK CASCADE). FK 칸을 셋 둔 이유다
    - 한 행은 관찰 하나만 가리킨다 (CHECK)

세는 규칙은 연결 단계의 단위 테스트(tests/unit/agents/curator/)가 본다.
"""

from datetime import date

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationActivity,
    ObservationFood,
    ObservationLinkHold,
)


@pytest.fixture
async def observations(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    common = {
        "child_id": child.id,
        "raw_text": "synthetic record",
        "subject": "synthetic subject",
        "confidence_source": ConfidenceSource.PARENT_DIRECT,
        "observed_range": Range(date(2026, 9, 1), date(2026, 9, 2)),
    }
    food = ObservationFood(**common)
    activity = ObservationActivity(**common, activity="synthetic activity")
    session.add_all([food, activity])
    await session.flush()
    return food, activity


async def test_관찰이_지워지면_보류_기록도_지워진다(session, observations):
    food, _ = observations
    session.add(ObservationLinkHold(food_id=food.id, uncertain_count=1, subject_hash="h"))
    await session.flush()

    await session.execute(delete(ObservationFood).where(ObservationFood.id == food.id))

    assert await session.scalar(select(ObservationLinkHold.id)) is None


@pytest.mark.parametrize("targets", ["none", "two"])
async def test_보류_기록은_관찰_하나만_가리킨다(session, observations, targets):
    food, activity = observations
    ids = {} if targets == "none" else {"food_id": food.id, "activity_id": activity.id}

    with pytest.raises(IntegrityError, match="observation_link_hold_one_target"):
        async with session.begin_nested():
            session.add(ObservationLinkHold(**ids, uncertain_count=1, subject_hash="h"))
            await session.flush()
