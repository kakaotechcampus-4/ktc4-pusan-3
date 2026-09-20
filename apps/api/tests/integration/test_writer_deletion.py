"""Shared records outlive their writer, but not their child (Issue #47, PR A)."""

from datetime import date

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.consent.models import Consent, ConsentAction, ConsentScope
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
    RoutineCategory,
)
from app.domains.policy.models import PolicyVersion
from app.domains.safety.models import HealthSafety, SafetyKind
from app.domains.schedule.models import DiaryEntry, SharedPhoto

OBSERVATION_MODELS = {
    ObservationFood,
    ObservationEducation,
    ObservationActivity,
    ObservationHealth,
    ObservationRoutine,
}

RECORDS = [
    (ObservationFood, "source_writer", {}),
    (ObservationEducation, "source_writer", {"topic": "synthetic topic"}),
    (ObservationActivity, "source_writer", {"activity": "synthetic activity"}),
    (ObservationHealth, "source_writer", {"symptom": ["synthetic symptom"]}),
    (ObservationRoutine, "source_writer", {"routine_category": RoutineCategory.HABIT}),
    (HealthSafety, "created_by", {"kind": SafetyKind.ALLERGY, "label": "synthetic label"}),
    (
        SharedPhoto,
        "uploader_parent_id",
        {"date": date(2026, 9, 1), "image_url": "https://example.test/photo.jpg"},
    ),
]


@pytest.fixture
async def family(session):
    owner, writer = Parent(), Parent()
    session.add_all([owner, writer])
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, writer, child


def make_record(model, column, extra, child_id, writer_id):
    values = {"child_id": child_id, column: writer_id, **extra}
    if model in OBSERVATION_MODELS:
        values.update(
            raw_text="synthetic record",
            confidence_source=ConfidenceSource.PARENT_DIRECT,
            observed_range=Range(date(2026, 9, 1), date(2026, 9, 2)),
        )
        if model is not ObservationHealth:
            values["subject"] = "synthetic subject"
    return model(**values)


@pytest.mark.parametrize("model,column,extra", RECORDS, ids=lambda x: getattr(x, "__name__", None))
async def test_writer_delete_preserves_records_and_child_cascade(
    session, family, model, column, extra
):
    owner, writer, child = family
    record = make_record(model, column, extra, child.id, writer.id)
    other = make_record(model, column, extra, child.id, owner.id)
    session.add_all([record, other])
    await session.flush()
    record_id, other_id = record.id, other.id
    before = dict(
        (await session.execute(select(model.__table__).where(model.id == record_id)))
        .mappings()
        .one()
    )

    # A SQL DELETE exercises PostgreSQL's FK action without ORM relationship cleanup.
    await session.execute(delete(Parent).where(Parent.id == writer.id))
    after = dict(
        (await session.execute(select(model.__table__).where(model.id == record_id)))
        .mappings()
        .one()
    )
    assert after == {**before, column: None}
    assert (
        await session.scalar(select(getattr(model, column)).where(model.id == other_id)) == owner.id
    )
    assert await session.scalar(select(Child.id).where(Child.id == child.id)) == child.id
    assert await session.scalar(select(Parent.id).where(Parent.id == writer.id)) is None

    await session.execute(delete(Child).where(Child.id == child.id))
    assert (
        await session.scalars(select(model.id).where(model.id.in_([record_id, other_id])))
    ).all() == []


async def test_diary_deleted_with_author(session, family):
    """Diary는 개인 데이터라 RECORDS(공동 기록)와 반대다 — 작성자가 지워지면 같이 지워진다."""
    owner, writer, child = family
    entry = DiaryEntry(
        child_id=child.id, author_parent_id=writer.id, date=date(2026, 9, 1), content="synthetic"
    )
    other = DiaryEntry(
        child_id=child.id, author_parent_id=owner.id, date=date(2026, 9, 1), content="synthetic"
    )
    session.add_all([entry, other])
    await session.flush()
    entry_id, other_id = entry.id, other.id

    await session.execute(delete(Parent).where(Parent.id == writer.id))

    assert await session.scalar(select(DiaryEntry.id).where(DiaryEntry.id == entry_id)) is None
    assert await session.scalar(select(DiaryEntry.id).where(DiaryEntry.id == other_id)) == other_id


async def test_owner_delete_is_still_blocked(session, family):
    owner, _, child = family
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(Parent).where(Parent.id == owner.id))
    assert (
        await session.scalar(select(Child.owner_parent_id).where(Child.id == child.id)) == owner.id
    )


@pytest.mark.parametrize("target", ["parent", "child"])
async def test_consent_subject_delete_is_still_blocked(session, family, target):
    _, writer, child = family
    is_child = target == "child"
    scope = ConsentScope.CHILD_BASIC if is_child else ConsentScope.SERVICE_TERMS
    session.add(
        Consent(
            # 동의 대상이 삭제를 막는다 — 행위자(actor)는 SET NULL 이라 막지 않는다 (PR C).
            subject_parent_id=None if is_child else writer.id,
            child_id=child.id if is_child else None,
            actor_parent_id=writer.id,
            actor_ref=writer.id,
            scope=scope,
            action=ConsentAction.GRANTED,
            policy_version_id=await session.scalar(
                select(PolicyVersion.id).where(PolicyVersion.scope == scope)
            ),
        )
    )
    await session.flush()
    model, target_id = (Child, child.id) if target == "child" else (Parent, writer.id)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(model).where(model.id == target_id))
    assert await session.scalar(select(model.id).where(model.id == target_id)) == target_id
