"""Correction 의 대상·verdict 짝과 Observation stand_alone 상태 (Issue #73).

상태 전이와 검색·집계 반영은 후속 이슈다. 여기서는 스키마가 무엇을 받고 무엇을
막는지만 본다.

잘못된 값을 넣는 검증은 ORM 이 아니라 raw SQL 로 한다. SQLAlchemy 의 Enum 은
validate_strings 로 바인딩 시점에 먼저 걸러 버려서, ORM 으로 넣으면 DB 제약이
실제로 살아 있는지 확인하지 못한다.
"""

import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.correction.models import (
    OBSERVATION_KINDS,
    OBSERVATION_VERDICTS,
    PROFILE_VERDICTS,
    Correction,
    CorrectionTargetKind,
    CorrectionVerdict,
)
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationStatus,
)

OBSERVATIONS = [
    (ObservationFood, {"subject": "synthetic subject"}),
    (ObservationEducation, {"subject": "synthetic subject", "topic": "synthetic topic"}),
    (ObservationActivity, {"subject": "synthetic subject", "activity": "synthetic activity"}),
    (ObservationHealth, {"symptom": ["synthetic symptom"]}),
]

ALLOWED_PAIRS = [(kind, verdict) for kind in OBSERVATION_KINDS for verdict in OBSERVATION_VERDICTS]
ALLOWED_PAIRS += [(CorrectionTargetKind.PROFILE_AFFINITY, verdict) for verdict in PROFILE_VERDICTS]

# 관찰에는 프로필 전용 verdict 를, 프로필에는 관찰 전용 verdict 를 붙인 짝
REJECTED_PAIRS = [
    (CorrectionTargetKind.OBSERVATION_FOOD, CorrectionVerdict.OUTDATED),
    (CorrectionTargetKind.OBSERVATION_FOOD, CorrectionVerdict.NEED_MORE_OBSERVATION),
    (CorrectionTargetKind.OBSERVATION_HEALTH, CorrectionVerdict.OUTDATED),
    (CorrectionTargetKind.PROFILE_AFFINITY, CorrectionVerdict.ONCE_ONLY),
]


@pytest.fixture
async def family(session):
    owner, writer = Parent(), Parent()
    session.add_all([owner, writer])
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return writer, child


def make_observation(model, extra, child_id, *, status):
    return model(
        child_id=child_id,
        raw_text="synthetic record",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(date(2026, 9, 1), date(2026, 9, 2)),
        status=status,
        **extra,
    )


async def insert_raw_correction(session, *, child_id, target_kind, verdict):
    """제약 위반을 DB 가 막는지 보기 위해 ORM 검증을 건너뛴다."""
    await session.execute(
        sa.text(
            "INSERT INTO correction (child_id, target_kind, target_id, verdict)"
            " VALUES (:child_id, :target_kind, :target_id, :verdict)"
        ),
        {
            "child_id": child_id,
            "target_kind": target_kind,
            "target_id": uuid.uuid4(),
            "verdict": verdict,
        },
    )


@pytest.mark.parametrize("model,extra", OBSERVATIONS, ids=lambda x: getattr(x, "__name__", None))
async def test_observation_accepts_stand_alone(session, family, model, extra):
    _, child = family
    record = make_observation(model, extra, child.id, status=ObservationStatus.STAND_ALONE)
    session.add(record)
    await session.flush()
    stored = await session.scalar(select(model.status).where(model.id == record.id))
    assert stored == ObservationStatus.STAND_ALONE


def sql_literal(value):
    """daterange·text[] 는 asyncpg 가 raw SQL 의 바인딩 타입을 알 수 없어 리터럴로 적는다."""
    if isinstance(value, list):
        return "ARRAY[" + ", ".join(f"'{item}'" for item in value) + "]::text[]"
    return f"'{value}'"


@pytest.mark.parametrize("model,extra", OBSERVATIONS, ids=lambda x: getattr(x, "__name__", None))
async def test_observation_rejects_unknown_status(session, family, model, extra):
    _, child = family
    columns = ["child_id", "raw_text", "confidence_source", "observed_range", "status", *extra]
    literals = [
        ":child_id",
        "'synthetic record'",
        f"'{ConfidenceSource.PARENT_DIRECT.value}'",
        "'[2026-09-01,2026-09-02)'::daterange",
        "'archived'",  # 프로필 쪽 상태값이다. 관찰에는 없다
        *(sql_literal(value) for value in extra.values()),
    ]
    statement = sa.text(
        f"INSERT INTO {model.__tablename__} ({', '.join(columns)}) VALUES ({', '.join(literals)})"
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(statement, {"child_id": child.id})


@pytest.mark.parametrize("kind,verdict", ALLOWED_PAIRS, ids=lambda x: x.value)
async def test_correction_accepts_allowed_pairs(session, family, kind, verdict):
    writer, child = family
    record = Correction(
        child_id=child.id,
        target_kind=kind,
        target_id=uuid.uuid4(),
        verdict=verdict,
        created_by=writer.id,
    )
    session.add(record)
    await session.flush()
    assert await session.scalar(select(Correction.verdict).where(Correction.id == record.id)) == (
        verdict
    )


@pytest.mark.parametrize("kind,verdict", REJECTED_PAIRS, ids=lambda x: x.value)
async def test_correction_rejects_mismatched_pairs(session, family, kind, verdict):
    _, child = family
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await insert_raw_correction(
                session, child_id=child.id, target_kind=kind.value, verdict=verdict.value
            )


async def test_correction_rejects_confirm(session, family):
    """맞아요는 아무것도 바꾸지 않으므로 이력으로도 남기지 않는다."""
    _, child = family
    assert "confirm" not in {v.value for v in CorrectionVerdict}
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await insert_raw_correction(
                session,
                child_id=child.id,
                target_kind=CorrectionTargetKind.PROFILE_AFFINITY.value,
                verdict="confirm",
            )


async def test_correction_outlives_its_writer(session, family):
    writer, child = family
    record = Correction(
        child_id=child.id,
        target_kind=CorrectionTargetKind.OBSERVATION_FOOD,
        target_id=uuid.uuid4(),
        verdict=CorrectionVerdict.ONCE_ONLY,
        created_by=writer.id,
    )
    session.add(record)
    await session.flush()

    await session.execute(delete(Parent).where(Parent.id == writer.id))
    stored = (
        (await session.execute(select(Correction.__table__).where(Correction.id == record.id)))
        .mappings()
        .one()
    )
    assert stored["created_by"] is None
    assert stored["verdict"] == CorrectionVerdict.ONCE_ONLY


async def test_correction_is_removed_with_its_child(session, family):
    writer, child = family
    record = Correction(
        child_id=child.id,
        target_kind=CorrectionTargetKind.PROFILE_AFFINITY,
        target_id=uuid.uuid4(),
        verdict=CorrectionVerdict.OUTDATED,
        created_by=writer.id,
    )
    session.add(record)
    await session.flush()

    await session.execute(delete(Child).where(Child.id == child.id))
    assert await session.scalar(select(Correction.id).where(Correction.id == record.id)) is None
