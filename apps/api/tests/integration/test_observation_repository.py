"""Observation repository — DB 동작 계약.

각 테스트 함수는 repository 함수 하나의 명세를 정의한다.
함수명이 곧 스펙이므로, 실패하면 "어떤 계약이 깨졌는지" 함수명만 보고 판단할 수 있어야 한다.

# 인덱스 의존
#   count_active, page: ix_observation_{domain}_child_status (child_id, status)
#   find/update/delete: PK (id) + child_id 필터
#   query date overlap: (child_id, status) 인덱스 후 observed_range post-filter
"""

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
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
    SuggestionKind,
)


@pytest.fixture
async def family(session):
    """보호자(writer) 1명 + 아이 2명. 아이 간 격리를 검증하는 기본 구조."""
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
    """[day, day+days) 반열린 관찰 범위."""
    return Range(day, day + timedelta(days=days), bounds="[)")


def required_fields(domain: ObservationDomain, label: str) -> dict:
    """도메인별 NOT NULL 컬럼의 최소 필드셋."""
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
    """테스트 헬퍼: 최소 필드로 관찰 1건 생성."""
    return await create_observation(
        session,
        domain=domain,
        child_id=child.id,
        source_writer=writer.id,
        raw_text=f"{label} 원문",
        observed_range=observed(day),
        fields=required_fields(domain, label),
    )


async def test_create_returns_record_with_observed_on_and_default_status(session, family):
    """create → ObservationRecord 반환, observed_on = observed_range.lower, status = active."""
    writer, child, _ = family
    record = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "사과"
    )
    assert record.observed_on == date(2026, 9, 1)
    assert record.domain is ObservationDomain.FOOD
    assert record.fields["status"] is ObservationStatus.ACTIVE
    assert record.fields["subject"] == "사과"


async def test_query_filters_by_domain_child_date_overlap_and_literal_text(session, family):
    """query는 (도메인, 아이, 날짜 overlap, raw_text 리터럴) 네 축으로 필터한다.

    - 다른 아이의 같은 도메인 기록은 제외
    - 같은 아이의 다른 도메인 기록은 제외
    - 날짜 범위 밖 기록은 제외
    - raw_text에 '%' 같은 특수문자가 있어도 리터럴 매칭
    """
    writer, child, other_child = family
    target = await add_observation(
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

    assert [row.id for row in rows] == [target.id]


async def test_query_date_to_is_inclusive(session, family):
    """date_to는 inclusive — InMemoryStore(inmemory.py:79)의 observed_on <= date_to 와 동일.

    date_to=9/2 → 9/2에 관찰된 기록도 포함되어야 한다.
    """
    writer, child, _ = family
    sep1 = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "9/1 기록"
    )
    sep2 = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 2), "9/2 기록"
    )
    await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 3), "9/3 기록"
    )

    rows = await query_observations(
        session,
        domain="food",
        child_id=child.id,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 2),
    )

    assert {row.id for row in rows} == {sep1.id, sep2.id}


async def test_find_update_delete_are_scoped_to_child_id(session, family):
    """find/update/delete 모두 child_id WHERE절로 다른 아이 접근을 차단한다.

    - 다른 아이의 child_id → find=None, update=None, delete=False
    - 같은 아이의 child_id → 정상 동작
    """
    writer, child, other_child = family
    record = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "사과"
    )

    # 다른 아이로 접근 → 전부 실패
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

    # 같은 아이로 접근 → 정상
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


async def test_count_active_merges_five_tables_excludes_inactive_and_other_child(session, family):
    """count_active는 5테이블 UNION ALL + status='active' + child_id 필터.

    - 5개 도메인 각 1건 = total 5, period 5
    - 기간 밖 old(8/1) = total +1, period 변동 없음
    - 기간 걸친 overlapping(8/31~9/1) = total +1, period +1
    - stand_alone(집계 제외) = total/period 변동 없음
    - 다른 아이 = 변동 없음
    → total_count=7, period_count=6
    인덱스: ix_observation_{domain}_child_status → Index Only Scan 가능.
    """
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


async def test_create_rejects_managed_field_in_fields_dict(session, family):
    """fields에 child_id 같은 managed 필드를 넣으면 ValueError."""
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


async def test_query_rejects_date_from_after_date_to(session, family):
    """date_from > date_to이면 ValueError."""
    _, child, _ = family
    with pytest.raises(ValueError, match="date_from"):
        await query_observations(
            session,
            domain="food",
            child_id=child.id,
            date_from=date(2026, 9, 2),
            date_to=date(2026, 9, 1),
        )


async def test_count_active_rejects_empty_period(session, family):
    """period_start == period_end(빈 기간)이면 ValueError."""
    _, child, _ = family
    with pytest.raises(ValueError, match="집계 기간"):
        await count_active_observations(
            session,
            child_id=child.id,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 1),
        )


async def test_create_rejects_empty_and_unbounded_observed_range(session, family):
    """observed_range가 empty이거나 upper=None이면 ValueError."""
    writer, child, _ = family
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


async def test_page_merges_five_domains_with_cursor_and_status_filter(session, family):
    """page는 5테이블 병합 후 status 필터 + 역순 커서 페이징을 제공한다.

    - active만 기본 조회 (stand_alone은 별도 status 파라미터로)
    - 다른 아이 기록 제외
    - 날짜 필터(date_from=date_to → 하루)
    - limit=1 → next_cursor → 다음 페이지
    """
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


async def test_page_filters_by_affinity_and_excludes_suggestion_used(session, family):
    """affinity_id 필터 + unused_in_suggestions=True로 제안에 안 쓰인 관찰만 조회."""
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
    suggestion = Suggestion(
        child_id=child.id,
        agent=SuggestionAgent.FOOD,
        kind=SuggestionKind.PERSONALIZED,
        content="사과 간식",
        expires_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    session.add(suggestion)
    await session.flush()
    session.add(
        SuggestionEvidence(
            suggestion_id=suggestion.id,
            source_kind="observation_food",
            source_id=used.id,
            source_updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            note="사과를 먹었다",
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


async def test_page_cursor_does_not_skip_same_day_different_domain_records(session, family):
    """같은 날짜·다른 도메인 3건을 limit=1씩 순회하면 3건 모두 수집된다.

    커서 정렬: (upper(observed_range) DESC, kind DESC, id DESC).
    같은 날짜에서 kind+id로 구분하므로 중복·누락이 없어야 한다.
    """
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
