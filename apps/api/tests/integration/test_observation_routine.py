"""observation_routine 테이블 (Issue #107).

ObservationCommon 이 주는 검증(FK 삭제 정책 · status 값 · correction 대상 짝)은
test_writer_deletion.py · test_correction_target.py 의 파라미터 목록에 이미 얹었다.
여기서는 routine 전용 컬럼(routine_category · context · assistance_level ·
completion_status · trigger)만 본다.
"""

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    AssistanceLevel,
    CompletionStatus,
    ConfidenceSource,
    ObservationRoutine,
    RoutineCategory,
)

INSERT_SQL = sa.text(
    "INSERT INTO observation_routine"
    " (child_id, raw_text, subject, confidence_source, observed_range,"
    " routine_category, assistance_level, completion_status)"
    " VALUES (:child_id, 'synthetic record', 'synthetic subject', :confidence_source,"
    " '[2026-09-01,2026-09-02)'::daterange, :routine_category, :assistance_level,"
    " :completion_status)"
)


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    record = Child(owner_parent_id=owner.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(record)
    await session.flush()
    return record


def make_routine(child_id, **overrides):
    values = dict(
        child_id=child_id,
        raw_text="synthetic record",
        subject="synthetic subject",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(date(2026, 9, 1), date(2026, 9, 2)),
        routine_category=RoutineCategory.HABIT,
    )
    values.update(overrides)
    return ObservationRoutine(**values)


async def test_create_with_only_required_fields(session, child):
    record = make_routine(child.id)
    session.add(record)
    await session.flush()

    stored = await session.get(ObservationRoutine, record.id)
    assert stored.routine_category == RoutineCategory.HABIT
    assert stored.context is None
    assert stored.assistance_level is None
    assert stored.completion_status is None
    assert stored.trigger is None


async def test_create_with_all_fields(session, child):
    record = make_routine(
        child.id,
        context="식사 중",
        assistance_level=AssistanceLevel.VERBAL_PROMPT,
        completion_status=CompletionStatus.PARTIAL,
        trigger="정리하라고 했을 때",
    )
    session.add(record)
    await session.flush()

    stored = await session.get(ObservationRoutine, record.id)
    assert stored.context == "식사 중"
    assert stored.assistance_level == AssistanceLevel.VERBAL_PROMPT
    assert stored.completion_status == CompletionStatus.PARTIAL
    assert stored.trigger == "정리하라고 했을 때"


async def test_routine_category_is_required(session, child):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                sa.text(
                    "INSERT INTO observation_routine"
                    " (child_id, raw_text, subject, confidence_source, observed_range)"
                    " VALUES (:child_id, 'synthetic record', 'synthetic subject',"
                    " :confidence_source, '[2026-09-01,2026-09-02)'::daterange)"
                ),
                {"child_id": child.id, "confidence_source": ConfidenceSource.PARENT_DIRECT.value},
            )


@pytest.mark.parametrize("column", ["routine_category", "assistance_level", "completion_status"])
async def test_rejects_unknown_enum_value(session, child, column):
    """잘못된 값을 raw SQL 로 넣는다 — ORM Enum 은 바인딩 시점에 먼저 걸러서 DB 제약이
    실제로 살아 있는지 확인하지 못한다 (test_correction_target.py 와 같은 이유).
    """
    params = {
        "child_id": child.id,
        "confidence_source": ConfidenceSource.PARENT_DIRECT.value,
        "routine_category": RoutineCategory.HABIT.value,
        "assistance_level": AssistanceLevel.INDEPENDENT.value,
        "completion_status": CompletionStatus.COMPLETED.value,
    }
    params[column] = "not_a_real_value"
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(INSERT_SQL, params)
