"""기억 목록 API — #259.

목록 조회 자체(필터 · archived 제외)는 `list_affinities` 의 몫이다. 여기는 API 가 더하는 것만
본다: 분류 → 기억 표 매핑, 묶인 기록 수, 아이 접근 확인.
"""

import uuid
from datetime import timedelta

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
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
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
    assert health == {"affinities": [], "safety": []}


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
