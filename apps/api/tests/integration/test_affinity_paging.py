"""기억 목록 페이징 — `page_affinities` 의 DB 동작 계약 (#584).

`list_affinities` 는 그대로다 — 홈 하이라이트가 페이징 없이 전부 받아 쓰고, 묶인 기록이 없는
기억도 돌려준다. 여기는 총 건수와 함께 쪽으로 나누는 새 함수만 건다.

🚨 묶인 active 기록이 0개인 기억은 목록 · total 어디에도 세지 않는다 (루트 CLAUDE.md §2).
   예전에는 라우터가 쿼리 뒤에서 걸러서, 쪽으로 나누면 쪽마다 개수가 달라지고 total 이 컸다.
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.memory.profile.repository import page_affinities


@pytest.fixture
async def children(session):
    """아이 2명 — 아이 간 격리를 본다."""
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="첫째", birth_date=date(2023, 1, 1))
    other = Child(owner_parent_id=owner.id, nickname="둘째", birth_date=date(2024, 1, 1))
    session.add_all([child, other])
    await session.flush()
    return child, other


async def add_affinity(
    session,
    child: Child,
    key: str,
    last_observed_on: date,
    *,
    domain: MemoryDomain = MemoryDomain.FOOD,
    state: ProfileState = ProfileState.CONFIRMED,
    linked: ObservationStatus | None = ObservationStatus.ACTIVE,
) -> ProfileAffinity:
    """기억 1건. linked 는 묶어 둘 관찰의 상태 — None 이면 묶인 관찰이 없다."""
    affinity = ProfileAffinity(
        child_id=child.id,
        merge_key=key,
        domain=domain,
        state=state,
        last_observed_on=last_observed_on,
    )
    session.add(affinity)
    await session.flush()
    if linked is not None:
        session.add(
            ObservationFood(
                child_id=child.id,
                raw_text="원문",
                subject=key,
                confidence_source=ConfidenceSource.PARENT_DIRECT,
                observed_range=Range(
                    last_observed_on, last_observed_on + timedelta(days=1), bounds="[)"
                ),
                affinity_id=affinity.id,
                status=linked,
            )
        )
        await session.flush()
    return affinity


def ids(page) -> list[uuid.UUID]:
    return [row.id for row in page.items]


async def test_page_orders_by_last_observed_on_desc_and_total_ignores_the_page(session, children):
    child, _ = children
    created = [
        await add_affinity(session, child, f"음식{day}", date(2026, 9, day)) for day in range(1, 6)
    ]
    newest_first = [row.id for row in reversed(created)]

    pages = [
        await page_affinities(session, child_id=child.id, limit=2, offset=o) for o in (0, 2, 4)
    ]

    assert [ids(page) for page in pages] == [
        newest_first[0:2],
        newest_first[2:4],
        newest_first[4:5],
    ]
    assert {page.total for page in pages} == {5}


async def test_page_past_the_end_is_empty_with_the_same_total(session, children):
    child, _ = children
    await add_affinity(session, child, "사과", date(2026, 9, 1))

    page = await page_affinities(session, child_id=child.id, limit=10, offset=10)

    assert (page.items, page.total) == ([], 1)


async def test_page_excludes_affinities_without_active_observations_from_items_and_total(
    session, children
):
    """묶인 기록이 없거나 모두 교정된(stand_alone · inactive) 기억은 어느 쪽에도 안 나온다."""
    child, _ = children
    shown = await add_affinity(session, child, "계란 반찬", date(2026, 9, 5))
    await add_affinity(session, child, "묶인 기록 없음", date(2026, 9, 4), linked=None)
    await add_affinity(
        session, child, "이번만", date(2026, 9, 3), linked=ObservationStatus.STAND_ALONE
    )
    await add_affinity(
        session, child, "잘못됨", date(2026, 9, 2), linked=ObservationStatus.INACTIVE
    )

    first = await page_affinities(session, child_id=child.id, limit=1, offset=0)
    second = await page_affinities(session, child_id=child.id, limit=1, offset=1)

    assert ids(first) == [shown.id]
    assert second.items == []
    assert first.total == second.total == 1


async def test_page_hides_archived_by_default_and_filters_state_and_domain(session, children):
    child, _ = children
    confirmed = await add_affinity(session, child, "사과", date(2026, 9, 3))
    candidate = await add_affinity(
        session, child, "배", date(2026, 9, 2), state=ProfileState.CANDIDATE
    )
    await add_affinity(session, child, "옛 관심", date(2026, 8, 1), state=ProfileState.ARCHIVED)
    activity = await add_affinity(
        session, child, "블록", date(2026, 9, 1), domain=MemoryDomain.ACTIVITY
    )

    default = await page_affinities(session, child_id=child.id)
    only_candidate = await page_affinities(session, child_id=child.id, state=ProfileState.CANDIDATE)
    only_activity = await page_affinities(session, child_id=child.id, domain=MemoryDomain.ACTIVITY)

    assert ids(default) == [confirmed.id, candidate.id, activity.id]
    assert ids(only_candidate) == [candidate.id]
    assert ids(only_activity) == [activity.id]


async def test_page_filters_last_observed_on_inclusively(session, children):
    child, _ = children
    before = await add_affinity(session, child, "전", date(2026, 9, 9))
    start = await add_affinity(session, child, "시작일", date(2026, 9, 10))
    end = await add_affinity(session, child, "마지막일", date(2026, 9, 20))
    after = await add_affinity(session, child, "후", date(2026, 9, 21))

    page = await page_affinities(
        session, child_id=child.id, date_from=date(2026, 9, 10), date_to=date(2026, 9, 20)
    )

    assert ids(page) == [end.id, start.id]
    assert page.total == 2
    assert {before.id, after.id}.isdisjoint(ids(page))


@pytest.mark.parametrize(("stored", "q"), [("계란 반찬", "계란반찬"), ("계란반찬", "계란 반찬")])
async def test_page_q_ignores_whitespace_on_both_sides(session, children, stored, q):
    child, _ = children
    hit = await add_affinity(session, child, stored, date(2026, 9, 1))
    await add_affinity(session, child, "사과", date(2026, 9, 2))

    page = await page_affinities(session, child_id=child.id, q=q)

    assert ids(page) == [hit.id]
    assert page.total == 1


@pytest.mark.parametrize(("q", "expected"), [("%", "100% 주스"), ("_", "a_b"), ("\\", "c:\\x")])
async def test_page_q_treats_like_wildcards_as_plain_characters(session, children, q, expected):
    child, _ = children
    for index, key in enumerate(("100% 주스", "a_b", "ab", "c:\\x", "사과")):
        await add_affinity(session, child, key, date(2026, 9, 1 + index))

    page = await page_affinities(session, child_id=child.id, q=q)

    assert [row.merge_key for row in page.items] == [expected]
    assert page.total == 1


async def test_page_never_returns_another_childs_affinities(session, children):
    child, other = children
    mine = await add_affinity(session, child, "사과", date(2026, 9, 1))
    await add_affinity(session, other, "사과", date(2026, 9, 2))

    page = await page_affinities(session, child_id=child.id, q="사과")

    assert ids(page) == [mine.id]
    assert page.total == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"date_from": date(2026, 9, 2), "date_to": date(2026, 9, 1)},
    ],
)
async def test_page_rejects_invalid_arguments(session, children, kwargs):
    child, _ = children
    with pytest.raises(ValueError):
        await page_affinities(session, child_id=child.id, **kwargs)
