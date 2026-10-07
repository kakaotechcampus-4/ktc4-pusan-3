"""채택 · 일정 초안 · 일정 제출 API 통합 테스트 — #236.

PR #196 멘토 합의: approved = "보호자가 채택했다", 일정은 연결 테이블로.
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvent,
    SuggestionKind,
    SuggestionStatus,
)
from tests.integration.api.conftest import Bearer, issue_bearer, link_child


async def _make_suggestion(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    agent: SuggestionAgent = SuggestionAgent.ACTIVITY,
    status: SuggestionStatus = SuggestionStatus.DRAFT,
    expires_at: datetime | None = None,
) -> Suggestion:
    """테스트용 제안 직접 INSERT."""
    row = Suggestion(
        child_id=child_id,
        agent=agent,
        kind=SuggestionKind.PERSONALIZED,
        content="공원 산책",
        reason="야외 활동 선호",
        status=status,
        expires_at=expires_at or (datetime.now(UTC) + timedelta(hours=24)),
    )
    session.add(row)
    await session.flush()
    return row


# ── 채택 API ──────────────────────────────────────────────────────────────


async def test_approve_draft_to_approved(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """draft → approved 정상 전이."""
    s = await _make_suggestion(session, child_id=cid)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["suggestions"]) == 1
    assert data["suggestions"][0]["status"] == "approved"


async def test_approve_not_found(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
    """없는 suggestion_id → 404."""
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(uuid.uuid4())]},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_approve_already_approved_is_idempotent(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """이미 approved인 제안을 다시 보내면 200으로 그대로 돌려준다 (멱등)."""
    s = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["suggestions"][0]["status"] == "approved"


async def test_approve_expired(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """만료된 제안 → 409 suggestion_expired."""
    s = await _make_suggestion(
        session, child_id=cid, expires_at=datetime.now(UTC) - timedelta(hours=1)
    )
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "suggestion_expired"


async def test_approve_empty_ids(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
    """빈 suggestion_ids → 400."""
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": []},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_approve_other_childs_suggestion(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """남의 아이의 제안 → 404."""
    # 남의 아이 — 다른 보호자의 아이로 만든다. 같은 보호자에게 하나 더 이으면
    # "보호자당 아이 1명" 유니크 제약(#214)에 막힌다
    _, other_parent_id = await issue_bearer(session, token="test-token-other")
    other_cid = await link_child(session, parent_id=other_parent_id)
    s = await _make_suggestion(session, child_id=other_cid)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 404


# ── 일정 초안 API ─────────────────────────────────────────────────────────


async def test_event_drafts_from_approved(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """approved 제안 → 일정 초안 생성."""
    s = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["drafts"]) == 1
    draft = data["drafts"][0]
    assert draft["event"]["starts_at"] is None  # 제안에서 온 초안은 일자 없음
    assert draft["event"]["category"] == "activity"
    assert str(s.id) in draft["suggestion_ids"]


async def test_event_drafts_food_grouped(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """food 제안 N건 → 초안 1건 묶음."""
    s1 = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, status=SuggestionStatus.APPROVED
    )
    s2 = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, status=SuggestionStatus.APPROVED
    )
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s1.id), str(s2.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["drafts"]) == 1
    assert data["drafts"][0]["event"]["title"] == "저녁 식사"
    assert len(data["drafts"][0]["suggestion_ids"]) == 2


async def test_event_drafts_ids_are_unique_uuids(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """draft_id는 UUID 형식이고 호출마다 겹치지 않는다."""
    s1 = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    s2 = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, status=SuggestionStatus.APPROVED
    )
    headers, _ = bearer

    resp1 = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s1.id)]},
        headers=headers,
    )
    resp2 = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s2.id)]},
        headers=headers,
    )
    id1 = resp1.json()["drafts"][0]["draft_id"]
    id2 = resp2.json()["drafts"][0]["draft_id"]
    # UUID 형식 검증
    uuid.UUID(id1)
    uuid.UUID(id2)
    # 서로 다름
    assert id1 != id2


async def test_event_drafts_not_approved(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """draft인 제안 → 422 not_approved."""
    s = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.DRAFT)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "not_approved"


# ── 일정 제출 API ─────────────────────────────────────────────────────────


async def test_submit_event_with_suggestion(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """approved 제안 → 일정 제출 → SuggestionEvent 연결 생성."""
    idempotency.clear()
    s = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "공원 산책",
                "starts_at": "2026-10-10T15:00:00+09:00",
                "ends_at": None,
                "all_day": False,
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [],
            "suggestion_ids": [str(s.id)],
        },
        headers={**headers, "Idempotency-Key": "test-key-1"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["event"]["created_by"] == "agent"

    # 연결 테이블 확인
    link = await session.scalar(
        select(SuggestionEvent).where(SuggestionEvent.suggestion_id == s.id)
    )
    assert link is not None


async def test_submit_event_without_suggestion(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """제안 없이 직접 일정 생성 (caregiver)."""
    idempotency.clear()
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "병원 방문",
                "starts_at": "2026-10-12T10:00:00+09:00",
                "ends_at": None,
                "all_day": False,
                "event_type": "episodic",
                "category": "health",
            },
            "items": [],
        },
        headers={**headers, "Idempotency-Key": "test-key-2"},
    )
    assert resp.status_code == 201
    assert resp.json()["event"]["created_by"] == "caregiver"


async def test_submit_event_no_idempotency_key(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """Idempotency-Key 없음 → 400."""
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "테스트",
                "starts_at": "2026-10-10T15:00:00+09:00",
                "event_type": "episodic",
                "category": "etc",
            },
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "idempotency_key_required"


async def test_submit_event_idempotency_replay(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """같은 Idempotency-Key → 처음 응답 재생."""
    idempotency.clear()
    headers, _ = bearer
    body = {
        "event": {
            "title": "재생 테스트",
            "starts_at": "2026-10-10T15:00:00+09:00",
            "event_type": "episodic",
            "category": "etc",
        },
    }
    key = "replay-test-key"

    resp1 = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json=body,
        headers={**headers, "Idempotency-Key": key},
    )
    resp2 = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json=body,
        headers={**headers, "Idempotency-Key": key},
    )
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["event"]["id"] == resp2.json()["event"]["id"]


async def test_submit_event_already_linked(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """이미 연결된 제안 → 409 already_confirmed."""
    idempotency.clear()
    s = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer

    # 첫 번째 제출
    await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "첫 번째",
                "starts_at": "2026-10-10T15:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "suggestion_ids": [str(s.id)],
        },
        headers={**headers, "Idempotency-Key": "first"},
    )

    # 두 번째 제출 (같은 suggestion, 다른 key)
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "두 번째",
                "starts_at": "2026-10-11T15:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "suggestion_ids": [str(s.id)],
        },
        headers={**headers, "Idempotency-Key": "second"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "already_confirmed"


async def test_submit_event_not_approved_suggestion(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """draft인 제안으로 일정 제출 → 422."""
    idempotency.clear()
    s = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.DRAFT)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "불가",
                "starts_at": "2026-10-10T15:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "suggestion_ids": [str(s.id)],
        },
        headers={**headers, "Idempotency-Key": "draft-submit"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "not_approved"


# ── 중복 suggestion_ids 방어 ───────────────────────────────────────────────


async def test_approve_duplicate_ids(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """같은 id 두 번 → 중복 제거돼서 정상 처리."""
    s = await _make_suggestion(session, child_id=cid)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s.id), str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["suggestions"]) == 1


async def test_submit_event_duplicate_suggestion_ids(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """같은 suggestion_id 두 번 → 500이 아니라 정상 처리."""
    idempotency.clear()
    s = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "중복 테스트",
                "starts_at": "2026-10-10T15:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "suggestion_ids": [str(s.id), str(s.id)],
        },
        headers={**headers, "Idempotency-Key": "dup-test"},
    )
    assert resp.status_code == 201


# ── 만료 처리 (멘토 합의: draft에만) ──────────────────────────────────────


async def test_expire_only_affects_draft(session: AsyncSession, cid: uuid.UUID):
    """만료 처리는 draft만 — approved는 만료 대상이 아님. 만료된 draft는 안 보인다."""
    from app.domains.suggestion import repository as repo

    expired_time = datetime.now(UTC) - timedelta(hours=1)

    # 만료된 draft
    s_expired = await _make_suggestion(session, child_id=cid, expires_at=expired_time)
    # 만료됐지만 approved
    s_approved = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, expires_at=expired_time
    )
    # 살아있는 draft
    s_alive = await _make_suggestion(session, child_id=cid)

    drafts = await repo.list_suggestions(session, child_id=cid, status=SuggestionStatus.DRAFT)
    draft_ids = {s.id for s in drafts}
    assert s_alive.id in draft_ids  # 살아있는 draft는 보인다
    assert s_expired.id not in draft_ids  # 만료된 draft는 안 보인다

    all_approved = await repo.list_suggestions(
        session, child_id=cid, status=SuggestionStatus.APPROVED
    )
    assert s_approved.id in {s.id for s in all_approved}  # approved는 만료 무관


# ── 남의 아이 제안 방어 ───────────────────────────────────────────────────


async def test_event_drafts_other_childs_suggestion(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """남의 아이 제안으로 초안 요청 → 404."""
    _, other_parent_id = await issue_bearer(session, token="test-token-other")
    other_cid = await link_child(session, parent_id=other_parent_id)
    s = await _make_suggestion(session, child_id=other_cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_submit_event_other_childs_suggestion(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """남의 아이 제안으로 일정 제출 → 404."""
    idempotency.clear()
    _, other_parent_id = await issue_bearer(session, token="test-token-other")
    other_cid = await link_child(session, parent_id=other_parent_id)
    s = await _make_suggestion(session, child_id=other_cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "남의 제안",
                "starts_at": "2026-10-10T15:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "suggestion_ids": [str(s.id)],
        },
        headers={**headers, "Idempotency-Key": "other-child"},
    )
    assert resp.status_code == 404


# ── items 포함 제출 ───────────────────────────────────────────────────────


async def test_submit_event_with_items(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
    """준비물이 있는 일정 제출."""
    idempotency.clear()
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "소풍",
                "starts_at": "2026-10-15T09:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [
                {"item_id": None, "item_name": "도시락"},
                {"item_id": None, "item_name": "물통"},
            ],
        },
        headers={**headers, "Idempotency-Key": "items-test"},
    )
    assert resp.status_code == 201
    items = resp.json()["event"]["items"]
    assert len(items) == 2
    assert {i["item_name"] for i in items} == {"도시락", "물통"}
