"""기록 · 기억 고치기 API — #259.

상태 변경 · 재계산 규칙은 `profile/service.py` 의 몫이다. 여기는 API 가 더하는 것만 본다:
아이 접근 확인, 대상별 허용 값, 건강 기록 거부, 고친 뒤 응답과 이력.
"""

import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import today_kst
from app.domains.memory.observation.models import ConfidenceSource, ObservationFood
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
from tests.integration.api.conftest import Bearer, issue_bearer, link_child


async def _food_with_affinity(
    session: AsyncSession, cid: uuid.UUID
) -> tuple[ObservationFood, ProfileAffinity]:
    day = today_kst()
    affinity = ProfileAffinity(
        child_id=cid, merge_key="계란 반찬", domain=MemoryDomain.FOOD, last_observed_on=day
    )
    session.add(affinity)
    await session.flush()
    food = ObservationFood(
        child_id=cid,
        raw_text="원문",
        subject="계란말이",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
        affinity_id=affinity.id,
    )
    session.add(food)
    await session.flush()
    return food, affinity


def _body(cid, kind, target_id, verdict) -> dict:
    return {
        "target_ref": {"kind": kind, "id": str(target_id)},
        "verdict": verdict,
        "child_id": str(cid),
    }


async def test_other_childs_target_is_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    food, _ = await _food_with_affinity(session, other_cid)

    resp = await db_client.post(
        "/api/v1/corrections",
        json=_body(other_cid, "observation_food", food.id, "wrong"),
        headers=headers,
    )

    assert resp.status_code == 403


async def test_verdict_must_fit_target_and_health_is_refused(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    food, affinity = await _food_with_affinity(session, cid)

    async def post(kind, target_id, verdict):
        body = _body(cid, kind, target_id, verdict)
        return (await db_client.post("/api/v1/corrections", json=body, headers=headers)).status_code

    assert await post("observation_food", food.id, "outdated") == 400  # 기억에만 쓰는 값
    assert await post("profile_affinity", affinity.id, "once_only") == 400  # 기록에만 쓰는 값
    assert await post("observation_health", food.id, "wrong") == 400  # 첫 배포 범위 밖
    assert await post("observation_food", uuid.uuid4(), "wrong") == 404


async def test_observation_correction_updates_state_history_and_affinity(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    food, affinity = await _food_with_affinity(session, cid)
    body = _body(cid, "observation_food", food.id, "once_only")

    resp = await db_client.post("/api/v1/corrections", json=body, headers=headers)

    data = resp.json()
    assert resp.status_code == 200
    assert data["target"]["status"] == "stand_alone"
    assert data["correction"]["verdict"] == "once_only"
    assert data["cascade"]["affinities_recomputed"] == [
        {"kind": "profile_affinity", "id": str(affinity.id)}
    ]
    detail = await db_client.get(
        f"/api/v1/children/{cid}/observations/observation_food/{food.id}", headers=headers
    )
    assert [c["verdict"] for c in detail.json()["corrections"]] == ["once_only"]
    # 이미 고친 기록은 다시 고치지 않는다 — 같은 버튼을 두 번 눌러도 이력이 두 줄이 되지 않는다
    again = await db_client.post("/api/v1/corrections", json=body, headers=headers)
    assert again.status_code == 400


async def test_profile_correction_returns_the_affinity(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    _, affinity = await _food_with_affinity(session, cid)

    resp = await db_client.post(
        "/api/v1/corrections",
        json=_body(cid, "profile_affinity", affinity.id, "need_more_observation"),
        headers=headers,
    )

    data = resp.json()
    assert resp.status_code == 200
    assert data["target"]["id"] == str(affinity.id)
    assert data["target"]["observation_count"] == 1  # 기억 고치기는 기록을 바꾸지 않는다
    assert data["cascade"]["affinities_recomputed"] == [
        {"kind": "profile_affinity", "id": str(affinity.id)}
    ]
