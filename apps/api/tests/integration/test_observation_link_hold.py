"""observation_link_hold — 새 구조 스펙.

구조 변경: FK 칸 3개(food_id, activity_id, education_id) + CHECK
       → child_id(FK CASCADE) + domain + observation_id, UNIQUE(domain, observation_id)

DB 가 보장하는 것:
  - 아이가 삭제되면 보류 기록도 삭제된다 (child_id FK CASCADE)
  - 같은 관찰에 보류 기록은 하나다 (UNIQUE(domain, observation_id))

코드가 보장하는 것:
  - 관찰이 soft delete 되면 보류 기록도 삭제된다 (store 의 delete_observation —
    tests/integration/test_observation_delete_paths.py 가 본다, #736)

세는 규칙은 연결 단계의 단위 테스트(tests/unit/agents/curator/)가 본다.
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationLinkHold,
)


@pytest.fixture
async def family(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return parent, child


@pytest.fixture
async def observations(session, family):
    _, child = family
    common = {
        "child_id": child.id,
        "raw_text": "synthetic record",
        "subject": "synthetic subject",
        "confidence_source": ConfidenceSource.PARENT_DIRECT,
        "observed_range": Range(date(2026, 9, 1), date(2026, 9, 2)),
    }
    food = ObservationFood(**common)
    activity = ObservationActivity(**common, activity="synthetic activity")
    # 서로 다른 도메인에서 UUID 가 같아도 식별과 삭제가 격리돼야 한다.
    shared_id = uuid4()
    food.id = activity.id = shared_id
    education = ObservationEducation(id=shared_id, **common, topic="synthetic topic")
    session.add_all([food, activity, education])
    await session.flush()
    return {"food": food, "activity": activity, "education": education}


# ---------------------------------------------------------------------------
# DB 보장 — child_id FK CASCADE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", ["food", "activity", "education"])
async def test_아이_삭제시_보류_기록도_삭제된다(session, family, domain):
    """child_id FK CASCADE 로 DB 가 보장한다."""
    _, child = family
    observation_id = uuid4()  # 관찰 FK 없이도 저장되고 child FK 만으로 삭제돼야 한다.

    session.add(
        ObservationLinkHold(
            child_id=child.id,
            domain=domain,
            observation_id=observation_id,
            uncertain_count=1,
            subject_hash="h",
        )
    )
    await session.flush()

    await session.execute(delete(Child).where(Child.id == child.id))
    await session.flush()

    assert (
        await session.scalar(
            select(ObservationLinkHold.id).where(ObservationLinkHold.child_id == child.id)
        )
        is None
    )


# ---------------------------------------------------------------------------
# DB 보장 — UNIQUE(domain, observation_id)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", ["food", "activity", "education"])
async def test_같은_관찰에_보류_기록은_하나다(session, family, observations, domain):
    """UNIQUE(domain, observation_id) 제약."""
    _, child = family
    food = observations[domain]

    session.add(
        ObservationLinkHold(
            child_id=child.id,
            domain=domain,
            observation_id=food.id,
            uncertain_count=1,
            subject_hash="h",
        )
    )
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                ObservationLinkHold(
                    child_id=child.id,
                    domain=domain,
                    observation_id=food.id,
                    uncertain_count=1,
                    subject_hash="h2",
                )
            )
            await session.flush()


# ---------------------------------------------------------------------------
# DB 보장 — domain 값 검증
# ---------------------------------------------------------------------------


async def test_domain은_food_activity_education만(session, family, observations):
    _, child = family
    food = observations["food"]

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                ObservationLinkHold(
                    child_id=child.id,
                    domain="invalid",
                    observation_id=food.id,
                    uncertain_count=1,
                    subject_hash="h",
                )
            )
            await session.flush()


async def test_존재하지_않는_아이의_hold는_저장할_수_없다(session):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                ObservationLinkHold(
                    child_id=uuid4(),
                    domain="food",
                    observation_id=uuid4(),
                    uncertain_count=1,
                    subject_hash="h",
                )
            )
            await session.flush()
