"""Observation repository — DB 동작 계약.

각 테스트 함수는 repository 함수 하나의 명세를 정의한다.
함수명이 곧 스펙이므로, 실패하면 "어떤 계약이 깨졌는지" 함수명만 보고 판단할 수 있어야 한다.

# 인덱스 의존
#   count_active, page: ix_observation_{domain}_child_status (child_id, status)
#   find/update/delete: PK (id) + child_id 필터. delete 는 status=deleted 로 바꾸는 UPDATE
#   query date overlap: (child_id, status) 인덱스 후 observed_range post-filter
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationStatus,
    RoutineCategory,
)
from app.domains.memory.observation.repository import (
    _MODEL_BY_DOMAIN,
    ObservationDomain,
    count_active_observations,
    create_observation,
    delete_observation,
    find_observation,
    page_observations,
    query_observations,
    set_observation_status,
    update_observation,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity


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


@pytest.mark.parametrize("domain", list(ObservationDomain))
async def test_delete_keeps_row_as_deleted_and_hides_it_everywhere(session, family, domain):
    """delete는 soft delete. 행과 원문은 남고 status 만 deleted 로 바뀐다.

    - 지운 행은 find · query · update · 교정 · 목록(active, inactive) · 홈 집계 어디에도 안 걸린다
    - 두 번 지우면 False
    """
    writer, child, _ = family
    day = date(2026, 9, 1)
    record = await add_observation(session, writer, child, domain, day, "지울 기록")
    model = _MODEL_BY_DOMAIN[domain]

    assert await delete_observation(
        session, domain=domain, child_id=child.id, observation_id=record.id
    )

    stored = (
        await session.execute(select(model.status, model.raw_text).where(model.id == record.id))
    ).one()
    assert stored.status == ObservationStatus.DELETED
    assert stored.raw_text == "지울 기록 원문"

    assert (
        await find_observation(session, domain=domain, child_id=child.id, observation_id=record.id)
        is None
    )
    assert await query_observations(session, domain=domain, child_id=child.id) == []
    assert (
        await update_observation(
            session,
            domain=domain,
            child_id=child.id,
            observation_id=record.id,
            fields={"confidence_source": ConfidenceSource.PARENT_HEDGED},
        )
        is None
    )
    assert (
        await set_observation_status(
            session,
            domain=domain,
            child_id=child.id,
            observation_id=record.id,
            status=ObservationStatus.INACTIVE,
        )
        is None
    )
    for status in (ObservationStatus.ACTIVE, ObservationStatus.INACTIVE):
        page = await page_observations(session, child_id=child.id, status=status)
        assert page.total == 0
    counts = await count_active_observations(
        session, child_id=child.id, period_start=day, period_end=day + timedelta(days=1)
    )
    assert counts.total_count == 0

    assert not await delete_observation(
        session, domain=domain, child_id=child.id, observation_id=record.id
    )


async def test_page_rejects_deleted_status_filter(session, family):
    """deleted 는 목록 필터로 열지 않는다."""
    _, child, _ = family
    with pytest.raises(ValueError, match="deleted"):
        await page_observations(session, child_id=child.id, status=ObservationStatus.DELETED)


async def test_create_and_update_reject_status_in_fields(session, family):
    """status 는 교정과 삭제만 쓴다. fields 로는 어떤 값도 넣을 수 없다."""
    writer, child, _ = family
    with pytest.raises(ValueError, match="지원하지 않는 관찰 필드"):
        await create_observation(
            session,
            domain="food",
            child_id=child.id,
            source_writer=writer.id,
            raw_text="원문",
            observed_range=observed(date(2026, 9, 1)),
            fields={
                **required_fields(ObservationDomain.FOOD, "사과"),
                "status": ObservationStatus.STAND_ALONE,
            },
        )
    record = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "사과"
    )
    for status in ObservationStatus:
        with pytest.raises(ValueError, match="지원하지 않는 관찰 필드"):
            await update_observation(
                session,
                domain="food",
                child_id=child.id,
                observation_id=record.id,
                fields={"status": status},
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
    await set_observation_status(
        session,
        domain=inactive.domain,
        child_id=child.id,
        observation_id=inactive.id,
        status=ObservationStatus.STAND_ALONE,
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
    await set_observation_status(
        session,
        domain="routine",
        child_id=child.id,
        observation_id=routine.id,
        status=ObservationStatus.STAND_ALONE,
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


async def test_page_filters_by_affinity(session, family):
    """affinity_id 를 주면 그 기억에 묶인 관찰만 조회한다."""
    writer, child, _ = family
    affinity = ProfileAffinity(
        child_id=child.id,
        merge_key="사과",
        domain=MemoryDomain.FOOD,
        last_observed_on=date(2026, 9, 1),
    )
    session.add(affinity)
    await session.flush()
    await create_observation(
        session,
        domain="food",
        child_id=child.id,
        source_writer=writer.id,
        raw_text="사과를 먹었다",
        observed_range=observed(date(2026, 9, 1)),
        fields=required_fields(ObservationDomain.FOOD, "사과"),
    )
    linked = await create_observation(
        session,
        domain="food",
        child_id=child.id,
        source_writer=writer.id,
        raw_text="사과를 또 먹었다",
        observed_range=observed(date(2026, 9, 2)),
        fields={**required_fields(ObservationDomain.FOOD, "사과"), "affinity_id": affinity.id},
    )

    page = await page_observations(
        session, child_id=child.id, domains=["food"], affinity_id=affinity.id
    )
    assert page.total == 1
    assert [row.id for row in page.items] == [linked.id]


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


# ── #584: offset 페이징 · q 검색 ─────────────────────────────────────────────


async def test_page_offset_pages_in_newest_first_order_without_gaps(session, family):
    """offset 은 앞에서 건너뛸 건수다. 정렬은 cursor 와 같고 total 은 페이지와 무관하다."""
    writer, child, _ = family
    created = [
        await add_observation(
            session, writer, child, ObservationDomain.FOOD, date(2026, 9, day), f"음식{day}"
        )
        for day in range(1, 6)
    ]
    newest_first = [row.id for row in reversed(created)]

    pages = [
        await page_observations(session, child_id=child.id, limit=2, offset=offset)
        for offset in (0, 2, 4)
    ]

    assert [[row.id for row in page.items] for page in pages] == [
        newest_first[0:2],
        newest_first[2:4],
        newest_first[4:5],
    ]
    assert {page.total for page in pages} == {5}


async def test_page_offset_keeps_same_day_ties_in_kind_then_id_descending_order(session, family):
    """같은 날짜는 (kind DESC, id DESC) 로 순서가 고정된다 — 한 건씩 넘겨도 중복 · 누락이 없다."""
    writer, child, _ = family
    day = date(2026, 9, 1)
    food_a = await add_observation(session, writer, child, ObservationDomain.FOOD, day, "사과")
    food_b = await add_observation(session, writer, child, ObservationDomain.FOOD, day, "배")
    health = await add_observation(session, writer, child, ObservationDomain.HEALTH, day, "콧물")
    routine = await add_observation(session, writer, child, ObservationDomain.ROUTINE, day, "양치")
    expected = [
        routine.id,
        health.id,
        *sorted([food_a.id, food_b.id], reverse=True),
    ]

    seen = []
    for offset in range(4):
        page = await page_observations(session, child_id=child.id, limit=1, offset=offset)
        seen.extend(row.id for row in page.items)

    assert seen == expected


async def test_page_orders_by_observed_to_not_observed_from_and_overlaps_multi_day_ranges(
    session, family
):
    """기간이 긴 기록은 끝 날짜(observed_to) 기준으로 앞에 오고, 날짜 필터는 겹치면 포함한다."""
    writer, child, _ = family
    long_one = await create_observation(
        session,
        domain="food",
        child_id=child.id,
        source_writer=writer.id,
        raw_text="9/1~9/5",
        observed_range=observed(date(2026, 9, 1), days=5),
        fields=required_fields(ObservationDomain.FOOD, "긴 기록"),
    )
    short_one = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 3), "짧은 기록"
    )

    ordered = await page_observations(session, child_id=child.id)
    from_fifth = await page_observations(session, child_id=child.id, date_from=date(2026, 9, 5))
    until_first = await page_observations(session, child_id=child.id, date_to=date(2026, 9, 1))

    assert [row.id for row in ordered.items] == [long_one.id, short_one.id]
    assert [row.id for row in from_fifth.items] == [long_one.id]
    assert [row.id for row in until_first.items] == [long_one.id]


async def test_page_date_from_alone_and_date_to_alone_each_filter(session, family):
    writer, child, _ = family
    old = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "옛날"
    )
    new = await add_observation(
        session, writer, child, ObservationDomain.FOOD, date(2026, 9, 20), "최근"
    )

    after = await page_observations(session, child_id=child.id, date_from=date(2026, 9, 10))
    before = await page_observations(session, child_id=child.id, date_to=date(2026, 9, 10))

    assert [row.id for row in after.items] == [new.id]
    assert [row.id for row in before.items] == [old.id]


async def test_page_offset_past_the_end_is_an_empty_page_with_the_same_total(session, family):
    writer, child, _ = family
    await add_observation(session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "사과")

    page = await page_observations(session, child_id=child.id, limit=10, offset=10)

    assert page.items == []
    assert page.total == 1


async def test_page_offset_rejects_negative_and_cursor_together(session, family):
    """offset 은 0 이상이고, cursor 와 같이 줄 수 없다 — 어느 쪽을 따를지 모호해진다."""
    writer, child, _ = family
    await add_observation(session, writer, child, ObservationDomain.FOOD, date(2026, 9, 1), "사과")
    await add_observation(session, writer, child, ObservationDomain.FOOD, date(2026, 9, 2), "배")
    first = await page_observations(session, child_id=child.id, limit=1)

    with pytest.raises(ValueError):
        await page_observations(session, child_id=child.id, offset=-1)
    with pytest.raises(ValueError):
        await page_observations(session, child_id=child.id, cursor=first.next_cursor, offset=1)


async def test_page_defaults_keep_the_cursor_walk_unchanged(session, family):
    """q · offset 을 안 주는 호출(캘린더)은 이전과 같다 — cursor 로 끝까지 돌면 전부 나온다."""
    writer, child, _ = family
    for day in range(1, 5):
        await add_observation(
            session, writer, child, ObservationDomain.FOOD, date(2026, 9, day), f"음식{day}"
        )

    seen, cursor = [], None
    while True:
        page = await page_observations(session, child_id=child.id, cursor=cursor, limit=3)
        seen.extend(row.id for row in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert len(seen) == len(set(seen)) == 4


async def _observe_text(session, writer, child, day, *, raw_text, subject):
    return await create_observation(
        session,
        domain="food",
        child_id=child.id,
        source_writer=writer.id,
        raw_text=raw_text,
        observed_range=observed(day),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, "subject": subject},
    )


async def test_page_q_matches_raw_text_or_subject_ignoring_case(session, family):
    writer, child, _ = family
    by_text = await _observe_text(
        session, writer, child, date(2026, 9, 1), raw_text="오늘 점심에 브로콜리", subject="채소"
    )
    by_subject = await _observe_text(
        session, writer, child, date(2026, 9, 2), raw_text="오늘 간식", subject="Broccoli"
    )
    await _observe_text(session, writer, child, date(2026, 9, 3), raw_text="사과", subject="과일")

    korean = await page_observations(session, child_id=child.id, q="브로콜리")
    english = await page_observations(session, child_id=child.id, q="broc")
    nothing = await page_observations(session, child_id=child.id, q="없는말")

    assert [row.id for row in korean.items] == [by_text.id]
    assert [row.id for row in english.items] == [by_subject.id]
    assert (nothing.items, nothing.total) == ([], 0)


@pytest.mark.parametrize(
    ("q", "expected_text"),
    [
        ("%", "주스 100% 먹음"),
        ("_", "a_b 메모"),
        ("\\", "경로 c:\\x"),
    ],
)
async def test_page_q_treats_like_wildcards_as_plain_characters(session, family, q, expected_text):
    """% · _ · \\ 는 와일드카드가 아니라 글자 그대로다 — q=% 가 전체 매칭이 되면 안 된다."""
    writer, child, _ = family
    for index, text in enumerate(("주스 100% 먹음", "a_b 메모", "ab 메모", "경로 c:\\x", "사과")):
        await _observe_text(
            session, writer, child, date(2026, 9, 1 + index), raw_text=text, subject=f"과목{index}"
        )

    page = await page_observations(session, child_id=child.id, q=q)

    assert [row.raw_text for row in page.items] == [expected_text]
    assert page.total == 1


async def test_page_q_total_counts_only_matches_and_combines_with_other_filters(session, family):
    writer, child, other_child = family
    hit_old = await _observe_text(
        session, writer, child, date(2026, 8, 1), raw_text="브로콜리 8월", subject="가"
    )
    hit_new = await _observe_text(
        session, writer, child, date(2026, 9, 10), raw_text="브로콜리 9월", subject="나"
    )
    await _observe_text(session, writer, child, date(2026, 9, 11), raw_text="사과", subject="다")
    await _observe_text(
        session, writer, other_child, date(2026, 9, 10), raw_text="브로콜리 다른 아이", subject="라"
    )

    everything = await page_observations(session, child_id=child.id, q="브로콜리", limit=1)
    september = await page_observations(
        session,
        child_id=child.id,
        q="브로콜리",
        domains=["food"],
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
    )

    assert everything.total == 2
    assert [row.id for row in everything.items] == [hit_new.id]
    assert [row.id for row in september.items] == [hit_new.id]
    assert hit_old.id not in {row.id for row in september.items}


async def test_page_q_does_not_break_the_health_branch_that_has_no_subject(session, family):
    """observation_health 에는 subject 칸이 없다. q 를 줘도 안 깨지고 raw_text 로 맞는다."""
    writer, child, _ = family
    health = await add_observation(
        session, writer, child, ObservationDomain.HEALTH, date(2026, 9, 1), "콧물"
    )
    await add_observation(session, writer, child, ObservationDomain.FOOD, date(2026, 9, 2), "사과")

    page = await page_observations(session, child_id=child.id, q="콧물")

    assert [row.id for row in page.items] == [health.id]
    assert page.total == 1


async def test_page_q_does_not_leak_through_subject_only_matches(session, family):
    """raw_text OR subject 가 다른 조건을 우회하면 안 된다 — subject 로만 맞는 행으로 확인한다."""
    writer, child, other_child = family
    mine = await _observe_text(
        session, writer, child, date(2026, 9, 10), raw_text="무관", subject="브로콜리"
    )
    await _observe_text(
        session, writer, other_child, date(2026, 9, 10), raw_text="무관", subject="브로콜리"
    )
    corrected = await _observe_text(
        session, writer, child, date(2026, 9, 10), raw_text="무관", subject="브로콜리"
    )
    await set_observation_status(
        session,
        domain="food",
        child_id=child.id,
        observation_id=corrected.id,
        status=ObservationStatus.STAND_ALONE,
    )
    await _observe_text(
        session, writer, child, date(2026, 1, 1), raw_text="무관", subject="브로콜리"
    )

    page = await page_observations(
        session, child_id=child.id, q="브로콜리", date_from=date(2026, 9, 1)
    )

    assert [row.id for row in page.items] == [mine.id]
    assert page.total == 1


async def test_page_q_does_not_ignore_whitespace_for_observations(session, family):
    """공백 무시 검색은 기억만이다 — 기록은 문장이라 단어 경계를 넘어 걸리면 오탐이 된다."""
    writer, child, _ = family
    await _observe_text(
        session, writer, child, date(2026, 9, 1), raw_text="계란 반찬", subject="가"
    )
    await _observe_text(session, writer, child, date(2026, 9, 2), raw_text="a b", subject="나")

    glued = await page_observations(session, child_id=child.id, q="계란반찬")
    glued_ascii = await page_observations(session, child_id=child.id, q="ab")

    assert (glued.total, glued_ascii.total) == (0, 0)


@pytest.mark.parametrize(
    "domain",
    [
        ObservationDomain.FOOD,
        ObservationDomain.EDUCATION,
        ObservationDomain.ACTIVITY,
        ObservationDomain.ROUTINE,
    ],
)
async def test_page_q_reads_subject_from_every_visible_table(session, family, domain):
    """subject 칸이 표마다 다르게 올라가거나 NULL 로 채워지면 그 표만 검색에서 빠진다."""
    writer, child, _ = family
    hit = await create_observation(
        session,
        domain=domain,
        child_id=child.id,
        source_writer=writer.id,
        raw_text="무관한 원문",
        observed_range=observed(date(2026, 9, 1)),
        fields={
            **required_fields(domain, "찾는이름"),
        },
    )

    page = await page_observations(session, child_id=child.id, q="찾는이름")

    assert [row.id for row in page.items] == [hit.id]
