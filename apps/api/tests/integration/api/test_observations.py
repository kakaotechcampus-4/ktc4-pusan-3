"""관찰 목록 · 상세 API — #259.

저장 함수(필터 · 커서 · 삭제 표시)는 `tests/integration/test_observation_repository.py` 가 건다.
여기는 API 가 더하는 것만 본다: 분류 → 표 매핑, 건강 제외, 응답 조립, 커서 문자열.
"""

import base64
import json
import uuid
from datetime import UTC, date, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import today_kst
from app.domains.correction.models import CorrectionTargetKind, CorrectionVerdict
from app.domains.correction.repository import append_correction
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
    RoutineCategory,
)
from app.domains.memory.observation.repository import create_observation
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
    SuggestionKind,
)
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

_FIELDS = {
    "food": {"subject": "사과", "action": "먹음"},
    "education": {"subject": "그림책", "topic": "그림책"},
    "activity": {"subject": "블록", "activity": "블록 쌓기"},
    "routine": {"subject": "양치", "routine_category": RoutineCategory.SELF_CARE},
    "health": {"symptom": ["기침"]},
}


async def _observe(
    session: AsyncSession, child_id: uuid.UUID, writer: uuid.UUID, domain: str, days_ago: int = 0
):
    day = today_kst() - timedelta(days=days_ago)
    return await create_observation(
        session,
        domain=domain,
        child_id=child_id,
        source_writer=writer,
        raw_text=f"{domain} 원문",
        observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, **_FIELDS[domain]},
    )


async def test_other_childs_observations_are_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    record = await _observe(session, other_cid, stranger, "food")

    listed = await db_client.get(f"/api/v1/children/{other_cid}/observations", headers=headers)
    detail = await db_client.get(
        f"/api/v1/children/{other_cid}/observations/observation_food/{record.id}", headers=headers
    )
    assert (listed.status_code, detail.status_code) == (403, 403)


async def test_list_maps_agent_to_tables_and_hides_health(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """growth 는 education · routine 두 표, health 는 빈 목록, 전체에도 건강은 없다."""
    headers, parent_id = bearer
    for domain in _FIELDS:
        await _observe(session, cid, parent_id, domain)
    url = f"/api/v1/children/{cid}/observations"

    growth = (await db_client.get(url, params={"domain": "growth"}, headers=headers)).json()
    health = (await db_client.get(url, params={"domain": "health"}, headers=headers)).json()
    everything = (await db_client.get(url, headers=headers)).json()

    assert {i["kind"] for i in growth["items"]} == {
        "observation_education",
        "observation_routine",
    }
    assert health == {"items": [], "next_cursor": None, "total": 0}
    assert everything["total"] == 4
    assert "observation_health" not in {i["kind"] for i in everything["items"]}


async def test_list_keeps_corrected_observations_but_not_deleted_ones(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """고친 기록(이번만 · 잘못된 기록)은 목록에 남고 status 로 구분된다. 지운 것만 빠진다."""
    headers, parent_id = bearer
    kept = {}
    for status in ObservationStatus:
        record = await _observe(session, cid, parent_id, "food")
        await session.execute(
            update(ObservationFood).where(ObservationFood.id == record.id).values(status=status)
        )
        kept[str(record.id)] = status.value

    listed = (await db_client.get(f"/api/v1/children/{cid}/observations", headers=headers)).json()

    assert {i["id"]: i["status"] for i in listed["items"]} == {
        id_: status for id_, status in kept.items() if status != "deleted"
    }
    assert listed["total"] == 3


async def test_cursor_pages_through_without_gaps(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    ids = {str((await _observe(session, cid, parent_id, "food", days_ago=d)).id) for d in range(3)}
    url = f"/api/v1/children/{cid}/observations"

    seen: list[str] = []
    params: dict[str, str | int] = {"limit": 2}
    while True:
        page = (await db_client.get(url, params=params, headers=headers)).json()
        seen += [i["id"] for i in page["items"]]
        if page["next_cursor"] is None:
            break
        params["cursor"] = page["next_cursor"]

    assert sorted(seen) == sorted(ids) and len(seen) == 3


async def test_detail_carries_affinity_writer_usage_and_corrections(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    parent = await session.get(Parent, parent_id)
    parent.nickname = "엄마"
    affinity = ProfileAffinity(
        child_id=cid, merge_key="사과", domain=MemoryDomain.FOOD, last_observed_on=date.today()
    )
    session.add(affinity)
    await session.flush()
    record = await _observe(session, cid, parent_id, "food", days_ago=1)
    row = await session.get(ObservationFood, record.id)
    row.affinity_id = affinity.id
    suggestion = Suggestion(
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        kind=SuggestionKind.PERSONALIZED,
        content="사과 조각을 곁들여 보세요",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(suggestion)
    await session.flush()
    session.add(
        SuggestionEvidence(
            suggestion_id=suggestion.id,
            source_kind="observation_food",
            source_id=record.id,
            note="사과",
        )
    )
    await append_correction(
        session,
        child_id=cid,
        target_kind=CorrectionTargetKind.OBSERVATION_FOOD,
        target_id=record.id,
        verdict=CorrectionVerdict.ONCE_ONLY,
        parent_id=parent_id,
    )
    await session.flush()

    body = (
        await db_client.get(
            f"/api/v1/children/{cid}/observations/observation_food/{record.id}", headers=headers
        )
    ).json()

    observation = body["observation"]
    assert observation["observed_label"] == "어제"
    assert observation["affinity"]["merge_key"] == "사과"
    assert observation["source_writer"]["nickname"] == "엄마"
    assert observation["domain_fields"] == {"action": "먹음", "amount": None, "reaction": None}
    assert [u["suggestion_id"] for u in body["used_in"]] == [str(suggestion.id)]
    assert [c["verdict"] for c in body["corrections"]] == ["once_only"]


async def test_health_detail_is_not_found(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    record = await _observe(session, cid, parent_id, "health")
    resp = await db_client.get(
        f"/api/v1/children/{cid}/observations/observation_health/{record.id}", headers=headers
    )
    assert resp.status_code == 404


async def test_malformed_cursor_is_bad_request(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """형이 틀린 칸(숫자 id)도 500 이 아니라 400 이다."""
    headers, _ = bearer
    wrong_type = base64.urlsafe_b64encode(json.dumps(["2026-10-01", "food", 1]).encode()).decode()
    resp = await db_client.get(
        f"/api/v1/children/{cid}/observations", params={"cursor": wrong_type}, headers=headers
    )
    assert resp.status_code == 400
