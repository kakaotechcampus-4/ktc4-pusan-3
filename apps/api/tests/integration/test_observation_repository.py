"""Observation repository의 DB 동작 계약."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationStatus,
    RoutineCategory,
)
from app.domains.memory.observation.repository import (
    ObservationDomain,
    count_active_observations,
    create_observation,
    delete_observation,
    find_observation,
    page_observations,
    query_observations,
    update_observation,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
from app.domains.suggestion.models import Suggestion, SuggestionAgent


@pytest.fixture
async def family(session):
    owner = Parent()
    writer = Parent()
    session.add_all([owner, writer])
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="첫째", birth_date=date(2023, 1, 1))
    other_child = Child(
        owner_parent_id=owner.id,
        nickname="둘째",
        birth_date=date(2024, 1, 1),
    )
    session.add_all([child, other_child])
    await session.flush()
    return writer, child, other_child


def observed(day: date, *, days: int = 1) -> Range[date]:
    return Range(day, day + timedelta(days=days), bounds="[)")


def required_fields(domain: ObservationDomain, label: str) -> dict:
    common = {"confidence_source": ConfidenceSource.PARENT_DIRECT}
    match domain:
        case ObservationDomain.FOOD:
            return {**common, "subject": label}
        case ObservationDomain.HEALTH:
            return {**common, "symptom": [label]}
        case ObservationDomain.EDUCATION:
            return {**common, "subject": label, "topic": label}
        case ObservationDomain.ACTIVITY:
            return {**common, "subject": label, "activity": label}
        case ObservationDomain.ROUTINE:
            return {
                **common,
                "subject": label,
                "routine_category": RoutineCategory.HABIT,
            }
    raise AssertionError(f"테스트 필드가 없는 domain: {domain}")


async def add_observation(session, writer, child, domain, day, label):
    return await create_observation(
        session,
        domain=domain,
        child_id=child.id,
        source_writer=writer.id,
        raw_text=f"{label} 원문",
        observed_range=observed(day),
        fields=required_fields(domain, label),
    )


async def test_create_and_query_use_domain_child_overlap_and_literal_text(session, family):
    writer, child, other_child = family
    older = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "사과 100%"
    )
    await add_observation(session, writer, child, ObservationDomain.FOOD, date(2026, 9, 5), "배")
    await add_observation(
        session,
        writer,
        other_child,
        ObservationDomain.FOOD,
        date(2026, 9, 1),
        "사과 100%",
    )
    await add_observation(
        session,
        writer,
        child,
        ObservationDomain.HEALTH,
        date(2026, 9, 1),
        "사과 100%",
    )

    rows = await query_observations(
        session,
        domain="food",
        child_id=child.id,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 2),
        raw_text_query="100%",
    )

    assert [row.id for row in rows] == [older.id]
    assert rows[0].observed_on == date(2026, 9, 1)
    assert rows[0].fields["status"] is ObservationStatus.ACTIVE


async def test_find_update_and_delete_never_cross_child_scope(session, family):
    writer, child, other_child = family
    record = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "사과"
    )

    assert (
        await find_observation(
            session,
            domain="food",
            child_id=other_child.id,
            observation_id=record.id,
        )
        is None
    )
    assert (
        await update_observation(
            session,
            domain="food",
            child_id=other_child.id,
            observation_id=record.id,
            fields={"subject": "배"},
        )
        is None
    )
    assert not await delete_observation(
        session,
        domain="food",
        child_id=other_child.id,
        observation_id=record.id,
    )

    updated = await update_observation(
        session,
        domain="food",
        child_id=child.id,
        observation_id=record.id,
        fields={"subject": "배", "reaction": "좋아함"},
        clear=frozenset({"amount"}),
    )
    assert updated is not None
    assert updated.fields["subject"] == "배"
    assert updated.fields["embedding"] is None
    assert updated.fields["reaction"] == "좋아함"
    assert updated.fields["amount"] is None

    assert await delete_observation(
        session,
        domain="food",
        child_id=child.id,
        observation_id=record.id,
    )
    assert (
        await find_observation(
            session,
            domain="food",
            child_id=child.id,
            observation_id=record.id,
        )
        is None
    )


async def test_count_active_observations_merges_five_tables_and_uses_overlap(session, family):
    writer, child, other_child = family
    period_start = date(2026, 9, 1)
    period_end = date(2026, 9, 8)

    for domain in ObservationDomain:
        await add_observation(session, writer, child, domain, period_start, domain.value)

    old = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 8, 1), "오래된 기록"
    )
    overlapping = await create_observation(
        session,
        domain=ObservationDomain.ACTIVITY,
        child_id=child.id,
        source_writer=writer.id,
        raw_text="기간이 걸친 기록",
        observed_range=observed(date(2026, 8, 31), days=2),
        fields=required_fields(ObservationDomain.ACTIVITY, "기간 활동"),
    )
    inactive = await add_observation(
        session, writer, child, ObservationDomain.ROUTINE, period_start, "비활성 기록"
    )
    await update_observation(
        session,
        domain=inactive.domain,
        child_id=child.id,
        observation_id=inactive.id,
        fields={"status": ObservationStatus.STAND_ALONE},
    )
    await add_observation(
        session, writer, other_child, ObservationDomain.HEALTH, period_start, "다른 아이"
    )

    counts = await count_active_observations(
        session,
        child_id=child.id,
        period_start=period_start,
        period_end=period_end,
    )

    assert old.observed_on < period_start
    assert overlapping.observed_on < period_start
    assert counts.total_count == 7
    assert counts.period_count == 6


async def test_repository_rejects_unknown_fields_and_invalid_ranges(session, family):
    writer, child, _ = family
    with pytest.raises(ValueError, match="지원하지 않는 관찰 필드"):
        await create_observation(
            session,
            domain="food",
            child_id=child.id,
            source_writer=writer.id,
            raw_text="원문",
            observed_range=observed(date(2026, 9, 1)),
            fields={**required_fields(ObservationDomain.FOOD, "사과"), "child_id": child.id},
        )

    with pytest.raises(ValueError, match="date_from"):
        await query_observations(
            session,
            domain="food",
            child_id=child.id,
            date_from=date(2026, 9, 2),
            date_to=date(2026, 9, 1),
        )

    with pytest.raises(ValueError, match="집계 기간"):
        await count_active_observations(
            session,
            child_id=child.id,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 1),
        )

    for invalid_range in (Range(empty=True), Range(date(2026, 9, 1), None)):
        with pytest.raises(ValueError, match="observed_range"):
            await create_observation(
                session,
                domain="food",
                child_id=child.id,
                source_writer=writer.id,
                raw_text="원문",
                observed_range=invalid_range,
                fields=required_fields(ObservationDomain.FOOD, "사과"),
            )


async def test_api_page_merges_domains_with_stable_cursor_and_status(session, family):
    writer, child, other_child = family
    food = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 2), "사과"
    )
    health = await add_observation(
        session, writer, child, ObservationDomain.HEALTH, date(2026, 9, 3), "콧물"
    )
    routine = await add_observation(
        session, writer, child, ObservationDomain.ROUTINE, date(2026, 9, 4), "양치"
    )
    await update_observation(
        session,
        domain="routine",
        child_id=child.id,
        observation_id=routine.id,
        fields={"status": ObservationStatus.STAND_ALONE},
    )
    await add_observation(
        session, writer, other_child, ObservationDomain.ACTIVITY, date(2026, 9, 3), "다른 아이"
    )

    first = await page_observations(session, child_id=child.id, limit=1)
    assert first.total == 2
    assert [row.id for row in first.items] == [health.id]
    assert first.next_cursor is not None
    second = await page_observations(session, child_id=child.id, cursor=first.next_cursor, limit=1)
    assert second.total == 2
    assert [row.id for row in second.items] == [food.id]
    assert second.next_cursor is None

    stand_alone = await page_observations(
        session, child_id=child.id, status=ObservationStatus.STAND_ALONE
    )
    assert [row.id for row in stand_alone.items] == [routine.id]
    day = await page_observations(
        session, child_id=child.id, date_from=date(2026, 9, 3), date_to=date(2026, 9, 3)
    )
    assert [row.id for row in day.items] == [health.id]


async def test_api_page_filters_affinity_and_unused_suggestion_evidence(session, family):
    writer, child, _ = family
    affinity = ProfileAffinity(
        child_id=child.id,
        merge_key="사과",
        domain=MemoryDomain.FOOD,
        last_observed_on=date(2026, 9, 1),
    )
    session.add(affinity)
    await session.flush()
    used = await create_observation(
        session,
        domain="food",
        child_id=child.id,
        source_writer=writer.id,
        raw_text="사과를 먹었다",
        observed_range=observed(date(2026, 9, 1)),
        fields={**required_fields(ObservationDomain.FOOD, "사과"), "affinity_id": affinity.id},
    )
    unused = await create_observation(
        session,
        domain="food",
        child_id=child.id,
        source_writer=writer.id,
        raw_text="사과를 또 먹었다",
        observed_range=observed(date(2026, 9, 2)),
        fields={**required_fields(ObservationDomain.FOOD, "사과"), "affinity_id": affinity.id},
    )
    session.add(
        Suggestion(
            child_id=child.id,
            agent=SuggestionAgent.FOOD,
            content="사과 간식",
            source_refs=[{"kind": "observation_food", "id": str(used.id)}],
            expires_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
    )
    await session.flush()

    page = await page_observations(
        session,
        child_id=child.id,
        domain="food",
        affinity_id=affinity.id,
        unused_in_suggestions=True,
    )
    assert page.total == 1
    assert [row.id for row in page.items] == [unused.id]


async def test_api_page_cursor_does_not_skip_same_day_records(session, family):
    writer, child, _ = family
    await add_observation(session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "사과")
    await add_observation(
        session, writer, child, ObservationDomain.HEALTH, date(2026, 9, 1), "콧물"
    )
    await add_observation(
        session, writer, child, ObservationDomain.ROUTINE, date(2026, 9, 1), "양치"
    )

    seen = []
    cursor = None
    for _ in range(3):
        page = await page_observations(session, child_id=child.id, cursor=cursor, limit=1)
        assert page.total == 3
        seen.extend(row.id for row in page.items)
        cursor = page.next_cursor
    assert len(set(seen)) == 3
    assert cursor is None
