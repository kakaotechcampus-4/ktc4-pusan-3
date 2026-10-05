"""API용 최소 저장 함수의 아이 범위와 상태 변화."""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import null, select, text
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import (
    archive_child,
    create_child,
    find_accessible_child,
    list_children_for_parent,
)
from app.domains.correction.models import CorrectionTargetKind, CorrectionVerdict
from app.domains.correction.repository import append_correction, list_corrections
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ConfidenceSource, ObservationActivity
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.memory.profile.repository import list_affinities, rename_affinity
from app.domains.safety.models import (
    HealthSafety,
    SafetyCategory,
    SafetyKind,
    SafetySeverity,
    SafetyStatus,
)
from app.domains.safety.repository import create_safety, list_active_safety, retract_safety
from app.domains.schedule.models import Event, EventCategory, EventCreatedBy, EventType
from app.domains.schedule.repository import (
    create_event,
    create_event_item,
    create_reminder,
    delete_event,
    find_event,
    list_event_items,
    list_events,
    put_diary,
    set_event_item_prepared,
    update_event,
)
from app.domains.suggestion.models import SuggestionAgent, SuggestionFeedback, SuggestionKind
from app.domains.suggestion.repository import (
    create_suggestion,
    list_suggestions_using_observation,
    set_feedback,
)


@pytest.fixture
async def family(session):
    owner, stranger = Parent(), Parent()
    session.add_all([owner, stranger])
    await session.flush()
    child = await create_child(
        session,
        owner_parent_id=owner.id,
        nickname="첫째",
        birth_date=date(2023, 1, 1),
        relation=ParentChildRelation.MOTHER,
    )
    other = await create_child(
        session,
        owner_parent_id=stranger.id,
        nickname="다른 아이",
        birth_date=date(2024, 1, 1),
        relation=ParentChildRelation.FATHER,
    )
    return owner, stranger, child, other


async def test_child_access_and_archive(session, family):
    owner, stranger, child, _ = family
    assert await find_accessible_child(session, child_id=child.id, parent_id=owner.id)
    assert await find_accessible_child(session, child_id=child.id, parent_id=stranger.id) is None
    assert [row.id for row in await list_children_for_parent(session, parent_id=owner.id)] == [
        child.id
    ]
    assert (
        await archive_child(
            session,
            child_id=child.id,
            owner_parent_id=stranger.id,
            archived_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        is None
    )
    assert await archive_child(
        session,
        child_id=child.id,
        owner_parent_id=owner.id,
        archived_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    assert await list_children_for_parent(session, parent_id=owner.id) == []


async def test_safety_retraction_keeps_history_but_hides_active(session, family):
    owner, stranger, child, other = family
    safety = await create_safety(
        session,
        child_id=child.id,
        parent_id=owner.id,
        kind=SafetyKind.ALLERGY,
        label="우유",
    )
    assert [row.id for row in await list_active_safety(session, child_id=child.id)] == [safety.id]
    assert (
        await retract_safety(
            session,
            child_id=other.id,
            safety_id=safety.id,
            parent_id=stranger.id,
        )
        is None
    )
    assert await retract_safety(
        session,
        child_id=child.id,
        safety_id=safety.id,
        parent_id=owner.id,
    )
    assert await list_active_safety(session, child_id=child.id) == []
    assert (await session.get(type(safety), safety.id)).status is SafetyStatus.RETRACTED


async def test_schedule_event_and_item_are_child_scoped(session, family):
    owner, stranger, child, other = family
    start = datetime(2026, 9, 23, 9, tzinfo=timezone.utc)
    event = await create_event(
        session,
        child_id=child.id,
        title="물놀이",
        event_type=EventType.EPISODIC,
        starts_at=start,
        ends_at=None,
        all_day=False,
        category=EventCategory.ACTIVITY,
        created_by=EventCreatedBy.CAREGIVER,
    )
    assert await find_event(session, child_id=other.id, event_id=event.id) is None
    assert [
        row.id
        for row in await list_events(
            session,
            child_id=child.id,
            ends_after=datetime(2026, 9, 23, tzinfo=timezone.utc),
            starts_before=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
    ] == [event.id]
    assert (
        await update_event(
            session,
            child_id=other.id,
            event_id=event.id,
            title="변경 시도",
            starts_at=start,
            ends_at=None,
            all_day=False,
            category=EventCategory.ACTIVITY,
        )
        is None
    )
    updated = await update_event(
        session,
        child_id=child.id,
        event_id=event.id,
        title="물놀이 준비",
        starts_at=start,
        ends_at=None,
        all_day=False,
        category=EventCategory.ACTIVITY,
    )
    assert updated.title == "물놀이 준비"
    assert (
        await create_reminder(
            session,
            child_id=child.id,
            event_id=event.id,
            parent_id=stranger.id,
            remind_at=start,
        )
        is None
    )
    assert (
        await create_reminder(
            session,
            child_id=child.id,
            event_id=event.id,
            parent_id=owner.id,
            remind_at=start,
        )
        is not None
    )
    item = await create_event_item(
        session, child_id=child.id, event_id=event.id, item_name="수영복"
    )
    assert item is not None
    assert (
        await set_event_item_prepared(
            session, child_id=other.id, item_id=item.item_id, is_prepared=True
        )
        is None
    )
    prepared = await set_event_item_prepared(
        session, child_id=child.id, item_id=item.item_id, is_prepared=True
    )
    assert prepared.is_prepared and prepared.prepared_at is not None
    assert [
        row.item_id for row in await list_event_items(session, child_id=child.id, event_id=event.id)
    ] == [item.item_id]
    assert not await delete_event(session, child_id=other.id, event_id=event.id)
    assert await delete_event(session, child_id=child.id, event_id=event.id)
    assert await session.scalar(select(Event.id).where(Event.id == event.id)) is None


async def test_diary_put_updates_only_authors_own_day(session, family):
    owner, stranger, child, _ = family
    first = await put_diary(
        session,
        child_id=child.id,
        parent_id=owner.id,
        day=date(2026, 9, 23),
        content="첫 기록",
    )
    revised = await put_diary(
        session,
        child_id=child.id,
        parent_id=owner.id,
        day=date(2026, 9, 23),
        content="고친 기록",
    )
    other = await put_diary(
        session,
        child_id=child.id,
        parent_id=stranger.id,
        day=date(2026, 9, 23),
        content="다른 작성자",
    )
    assert revised.id == first.id
    assert revised.content == "고친 기록"
    assert other.id != first.id


async def test_affinity_list_defaults_to_non_archived_and_rename_is_scoped(session, family):
    _, _, child, other = family
    active = ProfileAffinity(
        child_id=child.id,
        merge_key="레고",
        domain=MemoryDomain.ACTIVITY,
        last_observed_on=date(2026, 9, 1),
    )
    archived = ProfileAffinity(
        child_id=child.id,
        merge_key="옛 관심",
        domain=MemoryDomain.FOOD,
        state=ProfileState.ARCHIVED,
        last_observed_on=date(2026, 8, 1),
    )
    session.add_all([active, archived])
    await session.flush()
    assert [row.id for row in await list_affinities(session, child_id=child.id)] == [active.id]
    assert (
        await rename_affinity(session, child_id=other.id, affinity_id=active.id, merge_key="자동차")
        is None
    )
    renamed = await rename_affinity(
        session, child_id=child.id, affinity_id=active.id, merge_key="만들기 놀이"
    )
    assert renamed.merge_key == "만들기 놀이"
    assert renamed.state is ProfileState.CANDIDATE


async def test_suggestion_feedback_and_correction_history_are_scoped(session, family):
    owner, _, child, other = family
    observation = ObservationActivity(
        child_id=child.id,
        source_writer=owner.id,
        raw_text="블록을 쌓았다",
        subject="블록",
        activity="블록 놀이",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(date(2026, 9, 1), date(2026, 9, 2)),
    )
    session.add(observation)
    await session.flush()
    observation_id = observation.id
    suggestion = await create_suggestion(
        session,
        child_id=child.id,
        agent=SuggestionAgent.ACTIVITY,
        kind=SuggestionKind.PERSONALIZED,
        content="블록 놀이",
        reason="블록을 쌓는 걸 좋아해서요",
        citations=[
            {
                "source_kind": "observation_activity",
                "source_id": str(observation_id),
                "note": "블록을 쌓고 놀았다",
            }
        ],
        expires_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert [
        row.id
        for row in await list_suggestions_using_observation(
            session,
            child_id=child.id,
            kind="observation_activity",
            observation_id=observation_id,
        )
    ] == [suggestion.id]
    assert (
        await set_feedback(
            session,
            child_id=other.id,
            suggestion_id=suggestion.id,
            feedback=SuggestionFeedback.LIKED,
        )
        is None
    )
    assert (
        await set_feedback(
            session,
            child_id=child.id,
            suggestion_id=suggestion.id,
            feedback=SuggestionFeedback.LIKED,
        )
    ).feedback is SuggestionFeedback.LIKED

    correction = await append_correction(
        session,
        child_id=child.id,
        target_kind=CorrectionTargetKind.OBSERVATION_ACTIVITY,
        target_id=observation_id,
        verdict=CorrectionVerdict.WRONG,
        parent_id=owner.id,
    )
    with pytest.raises(ValueError, match="교정 대상"):
        await append_correction(
            session,
            child_id=other.id,
            target_kind=CorrectionTargetKind.OBSERVATION_ACTIVITY,
            target_id=observation_id,
            verdict=CorrectionVerdict.WRONG,
            parent_id=owner.id,
        )
    assert [
        row.id
        for row in await list_corrections(
            session,
            child_id=child.id,
            target_kind=CorrectionTargetKind.OBSERVATION_ACTIVITY,
            target_id=observation_id,
        )
    ] == [correction.id]
    assert (
        await list_corrections(
            session,
            child_id=other.id,
            target_kind=CorrectionTargetKind.OBSERVATION_ACTIVITY,
            target_id=observation_id,
        )
        == []
    )


async def test_safety_category_is_allergy_only(session, family):
    owner, _, child, _ = family
    mugwort = await create_safety(
        session,
        child_id=child.id,
        parent_id=owner.id,
        kind=SafetyKind.ALLERGY,
        label="쑥",
        category=[SafetyCategory.FOOD, SafetyCategory.ENVIRONMENT],
    )
    assert mugwort.category == ["food", "environment"]
    assert mugwort.status is SafetyStatus.ACTIVE

    with pytest.raises(ValueError, match="allergy"):
        await create_safety(
            session,
            child_id=child.id,
            parent_id=owner.id,
            kind=SafetyKind.CHRONIC_DISEASE,
            label="소아 당뇨",
            category=[SafetyCategory.FOOD],
        )


async def test_db_checks_safety_category_and_kind(session, family):
    """repository 를 거치지 않아도 DB CHECK 가 막는다."""
    _, _, child, _ = family
    with pytest.raises(IntegrityError, match="health_safety_category_allergy_only"):
        async with session.begin_nested():
            session.add(
                HealthSafety(
                    child_id=child.id,
                    kind=SafetyKind.CHRONIC_DISEASE,
                    label="천식",
                    category=["food"],
                )
            )
    with pytest.raises(IntegrityError, match="health_safety_category_values"):
        async with session.begin_nested():
            session.add(
                HealthSafety(
                    child_id=child.id, kind=SafetyKind.ALLERGY, label="우유", category=["dairy"]
                )
            )
    with pytest.raises(IntegrityError, match='"safety_kind"'):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO health_safety (child_id, kind, label) "
                    "VALUES (:child_id, 'dietary_restriction', '합성 라벨')"
                ),
                {"child_id": child.id},
            )


async def test_safety_category_null_passes_checks(session, family):
    """CHECK 두 개가 NULL 을 통과시킨다.

    category 는 server_default 가 있어 None 을 넘기면 '{}' 로 들어간다. NULL 은 null() 로 넣는다.
    """
    _, _, child, _ = family
    rows = [
        HealthSafety(child_id=child.id, kind=SafetyKind.ALLERGY, label="우유", category=null()),
        HealthSafety(
            child_id=child.id, kind=SafetyKind.BEHAVIORAL, label="분리불안", category=null()
        ),
    ]
    async with session.begin_nested():
        session.add_all(rows)
    stored = await session.scalars(
        select(HealthSafety.category.is_(None)).where(HealthSafety.id.in_([row.id for row in rows]))
    )
    assert list(stored) == [True, True]


async def test_none_status_is_not_active(session, family):
    _, _, child, _ = family
    session.add(
        HealthSafety(
            child_id=child.id, kind=SafetyKind.ALLERGY, label="난류", status=SafetyStatus.NONE
        )
    )
    await session.flush()
    assert await list_active_safety(session, child_id=child.id) == []


async def test_safety_management_is_text(session, family):
    owner, _, child, _ = family
    diabetes = await create_safety(
        session,
        child_id=child.id,
        parent_id=owner.id,
        kind=SafetyKind.CHRONIC_DISEASE,
        label="소아 당뇨",
        management="식전 인슐린",
    )
    assert diabetes.management == "식전 인슐린"
    milk = await create_safety(
        session, child_id=child.id, parent_id=owner.id, kind=SafetyKind.ALLERGY, label="우유"
    )
    assert milk.management is None
    assert milk.category == []


async def test_allergy_severity_is_class(session, family):
    """알레르기는 검사 Class 0~6, 나머지 kind 는 mild~anaphylaxis 를 쓴다."""
    owner, _, child, _ = family
    peanut = await create_safety(
        session,
        child_id=child.id,
        parent_id=owner.id,
        kind=SafetyKind.ALLERGY,
        label="땅콩",
        severity=SafetySeverity.CLASS_3,
    )
    assert peanut.severity is SafetySeverity.CLASS_3

    with pytest.raises(ValueError, match="severity"):
        await create_safety(
            session,
            child_id=child.id,
            parent_id=owner.id,
            kind=SafetyKind.ALLERGY,
            label="우유",
            severity=SafetySeverity.SEVERE,
        )
    with pytest.raises(ValueError, match="severity"):
        await create_safety(
            session,
            child_id=child.id,
            parent_id=owner.id,
            kind=SafetyKind.CHRONIC_DISEASE,
            label="천식",
            severity=SafetySeverity.CLASS_2,
        )


async def test_db_checks_severity_by_kind(session, family):
    """repository 를 거치지 않아도 DB CHECK 가 kind 와 맞지 않는 severity 를 막는다."""
    _, _, child, _ = family
    with pytest.raises(IntegrityError, match="health_safety_severity_by_kind"):
        async with session.begin_nested():
            session.add(
                HealthSafety(
                    child_id=child.id,
                    kind=SafetyKind.ALLERGY,
                    label="우유",
                    severity=SafetySeverity.MILD,
                )
            )
    with pytest.raises(IntegrityError, match="health_safety_severity_by_kind"):
        async with session.begin_nested():
            session.add(
                HealthSafety(
                    child_id=child.id,
                    kind=SafetyKind.CHRONIC_DISEASE,
                    label="천식",
                    severity=SafetySeverity.CLASS_0,
                )
            )


async def test_suggestion_keeps_allergens_and_items(session, family):
    _, _, child, _ = family
    suggestion = await create_suggestion(
        session,
        child_id=child.id,
        agent=SuggestionAgent.FOOD,
        kind=SuggestionKind.GENERAL,
        content="된장찌개",
        reason="또래 기준 추천이에요",
        citations=[],
        expires_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
        allergens=["대두"],
        items=["된장", "두부"],
    )
    assert suggestion.allergens == ["대두"]
    assert suggestion.items == ["된장", "두부"]


async def test_suggestion_lists_default_to_empty(session, family):
    """기존 호출(두 칸을 안 넘김)은 그대로 동작한다."""
    _, _, child, _ = family
    suggestion = await create_suggestion(
        session,
        child_id=child.id,
        agent=SuggestionAgent.ACTIVITY,
        kind=SuggestionKind.GENERAL,
        content="블록 놀이",
        reason=None,
        citations=[],
        expires_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
    )
    await session.refresh(suggestion)
    assert suggestion.allergens == []
    assert suggestion.items == []
