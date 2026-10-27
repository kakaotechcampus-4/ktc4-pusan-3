"""기억 목록 API — #259.

목록 조회 자체(필터 · archived 제외)는 `list_affinities` 의 몫이다. 여기는 API 가 더하는 것만
본다: 분류 → 기억 표 매핑, 묶인 기록 수, 아이 접근 확인, 쪽(page · size) · 날짜 · 검색.
"""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import today_kst
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationEducation,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.main import app
from tests.integration.api.conftest import Bearer, issue_bearer, link_child


def _affinity(child_id: uuid.UUID, domain: MemoryDomain, key: str) -> ProfileAffinity:
    return ProfileAffinity(
        child_id=child_id, merge_key=key, domain=domain, last_observed_on=today_kst()
    )


def _food(child_id, affinity_id, status=ObservationStatus.ACTIVE) -> ObservationFood:
    day = today_kst()
    return ObservationFood(
        child_id=child_id,
        raw_text="원문",
        subject="계란말이",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
        affinity_id=affinity_id,
        status=status,
    )


async def test_other_childs_affinities_are_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    resp = await db_client.get(f"/api/v1/children/{other_cid}/affinities", headers=headers)
    assert resp.status_code == 403


async def test_growth_reads_education_and_health_is_empty(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    food = _affinity(cid, MemoryDomain.FOOD, "계란 반찬")
    book = _affinity(cid, MemoryDomain.EDUCATION, "그림책")
    session.add_all([food, book])
    await session.flush()
    day = today_kst()
    session.add_all(
        [
            _food(cid, food.id),
            ObservationEducation(
                child_id=cid,
                raw_text="원문",
                subject="그림책",
                topic="그림책",
                confidence_source=ConfidenceSource.PARENT_DIRECT,
                observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
                affinity_id=book.id,
            ),
        ]
    )
    await session.flush()
    url = f"/api/v1/children/{cid}/affinities"

    growth = (await db_client.get(url, params={"domain": "growth"}, headers=headers)).json()
    health = (await db_client.get(url, params={"domain": "health"}, headers=headers)).json()

    assert [(a["merge_key"], a["domain"]) for a in growth["affinities"]] == [("그림책", "growth")]
    assert health == {
        "affinities": [],
        "safety": [],
        "total": 0,
        "page": 1,
        "size": 20,
        "total_pages": 0,
    }


async def test_count_and_refs_use_only_active_linked_observations(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """교정으로 집계에서 빠진 관찰은 세지 않는다 — Curator 와 같은 기준이어야 숫자가 맞는다.

    묶인 기록이 모두 교정된 기억(당근)은 근거가 없어 목록에 나오지 않는다.
    """
    headers, _ = bearer
    affinity = _affinity(cid, MemoryDomain.FOOD, "계란 반찬")
    corrected = _affinity(cid, MemoryDomain.FOOD, "당근")
    session.add_all([affinity, corrected])
    await session.flush()
    active = [_food(cid, affinity.id), _food(cid, affinity.id)]
    session.add_all(
        [
            *active,
            _food(cid, affinity.id, ObservationStatus.STAND_ALONE),
            _food(cid, affinity.id, ObservationStatus.INACTIVE),
            _food(cid, None),
            _food(cid, corrected.id, ObservationStatus.INACTIVE),
        ]
    )
    await session.flush()

    [card] = (await db_client.get(f"/api/v1/children/{cid}/affinities", headers=headers)).json()[
        "affinities"
    ]

    assert card["observation_count"] == 2
    assert {r["id"] for r in card["source_refs"]} == {str(o.id) for o in active}
    assert card["state_reason"] == "지금까지 2번 기록됐고, 마지막은 오늘이에요"


async def _linked(
    session: AsyncSession, cid: uuid.UUID, key: str, days_ago: int = 0, *, linked: bool = True
) -> ProfileAffinity:
    """기억 1건 + (linked 면) 묶인 active 기록 1건. 마지막 기록은 days_ago 일 전이다."""
    day = today_kst() - timedelta(days=days_ago)
    affinity = ProfileAffinity(
        child_id=cid, merge_key=key, domain=MemoryDomain.FOOD, last_observed_on=day
    )
    session.add(affinity)
    await session.flush()
    if linked:
        session.add(
            ObservationFood(
                child_id=cid,
                raw_text="원문",
                subject=key,
                confidence_source=ConfidenceSource.PARENT_DIRECT,
                observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
                affinity_id=affinity.id,
            )
        )
        await session.flush()
    return affinity


async def test_pages_by_affinities_only_and_safety_stays_an_empty_array(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """total · page · total_pages 는 affinities 기준이다. safety 는 쪽으로 나누지 않고 늘 [] 다."""
    headers, _ = bearer
    created = [str((await _linked(session, cid, f"음식{d}", days_ago=d)).id) for d in range(5)]
    url = f"/api/v1/children/{cid}/affinities"

    pages = [
        (await db_client.get(url, params={"page": p, "size": 2}, headers=headers)).json()
        for p in (1, 2, 3, 4)
    ]

    assert [[a["id"] for a in page["affinities"]] for page in pages] == [
        created[0:2],
        created[2:4],
        created[4:5],
        [],
    ]
    assert {(p["total"], p["size"], p["total_pages"]) for p in pages} == {(5, 2, 3)}
    assert [p["page"] for p in pages] == [1, 2, 3, 4]
    assert all(p["safety"] == [] for p in pages)
    assert set(pages[0]) == {"affinities", "safety", "total", "page", "size", "total_pages"}


async def test_affinities_without_active_observations_are_not_counted_on_any_page(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """쪽마다 개수가 들쭉날쭉하거나 total 이 부풀면 안 된다 — 걸러내는 일이 쿼리 안에서 끝난다."""
    headers, _ = bearer
    shown = [str((await _linked(session, cid, f"보임{d}", days_ago=d)).id) for d in range(3)]
    await _linked(session, cid, "묶인 기록 없음", days_ago=0, linked=False)
    url = f"/api/v1/children/{cid}/affinities"

    first = (await db_client.get(url, params={"size": 2}, headers=headers)).json()
    second = (await db_client.get(url, params={"size": 2, "page": 2}, headers=headers)).json()

    assert [a["id"] for a in first["affinities"] + second["affinities"]] == shown
    assert (first["total"], first["total_pages"]) == (3, 2)
    assert len(first["affinities"]) == 2 and len(second["affinities"]) == 1


async def test_date_range_filters_last_observed_on_inclusively(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    for d in (0, 2, 5, 8):
        await _linked(session, cid, f"음식{d}", days_ago=d)
    today = today_kst()

    body = (
        await db_client.get(
            f"/api/v1/children/{cid}/affinities",
            params={
                "date_from": (today - timedelta(days=5)).isoformat(),
                "date_to": (today - timedelta(days=2)).isoformat(),
            },
            headers=headers,
        )
    ).json()

    assert sorted(a["merge_key"] for a in body["affinities"]) == ["음식2", "음식5"]
    assert body["total"] == 2


@pytest.mark.parametrize(("stored", "q"), [("계란 반찬", "계란반찬"), ("계란반찬", "계란 반찬")])
async def test_q_ignores_whitespace_so_both_spellings_find_the_affinity(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    stored: str,
    q: str,
):
    headers, _ = bearer
    hit = await _linked(session, cid, stored)
    await _linked(session, cid, "사과")

    body = (
        await db_client.get(f"/api/v1/children/{cid}/affinities", params={"q": q}, headers=headers)
    ).json()

    assert [a["id"] for a in body["affinities"]] == [str(hit.id)]
    assert body["total"] == 1


async def test_blank_q_means_no_search_and_wildcards_are_plain_characters(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    await _linked(session, cid, "100% 주스")
    await _linked(session, cid, "사과")
    url = f"/api/v1/children/{cid}/affinities"

    blank = (await db_client.get(url, params={"q": "   "}, headers=headers)).json()
    percent = (await db_client.get(url, params={"q": "%"}, headers=headers)).json()

    assert blank["total"] == 2
    assert [a["merge_key"] for a in percent["affinities"]] == ["100% 주스"]


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"size": 0},
        {"size": 101},
        {"q": "가" * 101},
        {"date_from": "2026-13-01"},
        {"date_to": "어제"},
        {"date_from": "2026-10-31", "date_to": "2026-10-01"},
    ],
)
async def test_invalid_query_is_bad_request_not_unprocessable(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID, params: dict[str, str | int]
):
    headers, _ = bearer

    resp = await db_client.get(f"/api/v1/children/{cid}/affinities", params=params, headers=headers)

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_failed"


def test_swagger_says_total_counts_affinities_only():
    """total 이 어느 배열 기준인지 Swagger 에 적혀 있다 (같은 응답에 safety 가 있어서)."""
    props = app.openapi()["components"]["schemas"]["AffinitiesResponse"]["properties"]

    for name in ("total", "page", "size", "total_pages"):
        assert props[name].get("description"), f"{name} 에 설명이 없다"
    assert "affinities" in props["total"]["description"]
    assert "safety" in props["total"]["description"]


@pytest.mark.parametrize(
    ("created", "params", "expected_total_pages"),
    [
        (0, {}, 0),
        (4, {"size": 2}, 2),  # 딱 나누어떨어진다
        (4, {"size": 4}, 1),
        (4, {"size": 100}, 1),
        (5, {"size": 2}, 3),
    ],
)
async def test_total_pages_is_ceil_of_total_over_size_and_zero_when_empty(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    created: int,
    params: dict[str, int],
    expected_total_pages: int,
):
    headers, _ = bearer
    for d in range(created):
        await _linked(session, cid, f"음식{d}", days_ago=d)

    body = (
        await db_client.get(f"/api/v1/children/{cid}/affinities", params=params, headers=headers)
    ).json()

    assert (body["total"], body["total_pages"]) == (created, expected_total_pages)


async def test_state_and_domain_filters_apply_to_total_not_only_to_the_items(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    for d in range(3):
        await _linked(session, cid, f"확정{d}", days_ago=d)
    candidate = await _linked(session, cid, "후보", days_ago=5)
    candidate.state = ProfileState.CANDIDATE
    await session.flush()
    url = f"/api/v1/children/{cid}/affinities"

    only_candidate = (
        await db_client.get(url, params={"state": "candidate", "size": 1}, headers=headers)
    ).json()
    only_food = (
        await db_client.get(url, params={"domain": "food", "size": 1}, headers=headers)
    ).json()

    assert (only_candidate["total"], only_candidate["total_pages"]) == (1, 1)
    assert (only_food["total"], only_food["total_pages"]) == (4, 4)


async def test_q_allows_exactly_100_characters(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer

    resp = await db_client.get(
        f"/api/v1/children/{cid}/affinities", params={"q": "가" * 100}, headers=headers
    )

    assert resp.status_code == 200


async def test_health_domain_echoes_page_and_size_and_still_validates(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    url = f"/api/v1/children/{cid}/affinities"

    empty = (
        await db_client.get(url, params={"domain": "health", "page": 3, "size": 5}, headers=headers)
    ).json()
    invalid = await db_client.get(
        url,
        params={"domain": "health", "date_from": "2026-10-31", "date_to": "2026-10-01"},
        headers=headers,
    )

    assert empty == {
        "affinities": [],
        "safety": [],
        "total": 0,
        "page": 3,
        "size": 5,
        "total_pages": 0,
    }
    assert invalid.status_code == 400


async def test_other_childs_list_is_forbidden_whatever_the_query(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)

    resp = await db_client.get(
        f"/api/v1/children/{other_cid}/affinities",
        params={"page": 1, "size": 5, "q": "사과", "date_from": "2026-01-01"},
        headers=headers,
    )

    assert resp.status_code == 403
