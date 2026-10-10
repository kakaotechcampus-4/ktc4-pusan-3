"""추천 목록 · 평가 · 추천 생성 stub · 일정 수정 통합 테스트 — #284."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType
from app.domains.schedule.repository import create_event, create_event_item
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
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
    with_evidence: bool = False,
) -> Suggestion:
    row = Suggestion(
        child_id=child_id,
        agent=agent,
        kind=SuggestionKind.PERSONALIZED,
        content="공원 산책",
        reason="야외 활동 선호",
        status=status,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    session.add(row)
    await session.flush()
    if with_evidence:
        session.add(
            SuggestionEvidence(
                suggestion_id=row.id,
                source_kind="profile_affinity",
                source_id=uuid.uuid4(),
                note="반복 3회",
            )
        )
        await session.flush()
    return row


# ── 추천 생성 stub ───────────────────────────────────────────────────────


async def test_create_suggestions_returns_501(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """POST /children/{cid}/suggestions는 agent 연결 전까지 501."""
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions",
        json={"agents": ["food"]},
        headers=headers,
    )
    assert resp.status_code == 501


async def test_answer_question_returns_501(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """POST /children/{cid}/answers는 agent 연결 전까지 501."""
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/answers",
        json={"question_id": "q1", "answer": "국수를 잘 먹어요"},
        headers=headers,
    )
    assert resp.status_code == 501


# ── 추천 목록 ────────────────────────────────────────────────────────────


async def test_list_suggestions_empty(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """제안이 없으면 빈 목록."""
    headers, _ = bearer
    resp = await db_client.get(
        f"/api/v1/children/{cid}/suggestions",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["next_cursor"] is None


async def test_list_suggestions_with_evidence(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """제안 목록에 evidence가 포함된다."""
    s = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, with_evidence=True
    )
    headers, _ = bearer
    resp = await db_client.get(
        f"/api/v1/children/{cid}/suggestions",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) >= 1
    item = next(i for i in data["items"] if i["id"] == str(s.id))
    assert len(item["evidence"]) == 1
    assert item["evidence"][0]["note"] == "반복 3회"
    assert len(item["source_refs"]) == 1


async def test_list_suggestions_status_filter(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """status 필터가 동작한다."""
    await _make_suggestion(session, child_id=cid, status=SuggestionStatus.DRAFT)
    await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer

    resp_draft = await db_client.get(
        f"/api/v1/children/{cid}/suggestions?status=draft",
        headers=headers,
    )
    resp_approved = await db_client.get(
        f"/api/v1/children/{cid}/suggestions?status=approved",
        headers=headers,
    )
    assert resp_draft.status_code == 200
    assert resp_approved.status_code == 200
    for item in resp_draft.json()["items"]:
        assert item["status"] == "draft"
    for item in resp_approved.json()["items"]:
        assert item["status"] == "approved"


async def test_list_suggestions_cursor_pagination(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """커서 페이징이 동작한다."""
    for _ in range(5):
        await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer

    resp1 = await db_client.get(
        f"/api/v1/children/{cid}/suggestions?limit=3",
        headers=headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1["items"]) == 3
    assert data1["next_cursor"] is not None

    resp2 = await db_client.get(
        f"/api/v1/children/{cid}/suggestions?limit=3&cursor={data1['next_cursor']}",
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["items"]) == 2
    assert data2["next_cursor"] is None

    # 중복 없음
    ids1 = {i["id"] for i in data1["items"]}
    ids2 = {i["id"] for i in data2["items"]}
    assert ids1.isdisjoint(ids2)


async def test_list_suggestions_other_child_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """남의 아이 추천은 403."""
    headers, _ = bearer
    other_cid = uuid.uuid4()
    resp = await db_client.get(
        f"/api/v1/children/{other_cid}/suggestions",
        headers=headers,
    )
    assert resp.status_code == 403


# ── 추천 평가 ────────────────────────────────────────────────────────────


async def test_feedback_sets_value(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """평가가 DB에 반영되고 갱신된 suggestion을 돌린다."""
    s = await _make_suggestion(session, child_id=cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/suggestions/{s.id}/feedback",
        json={"feedback": "child_liked"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["suggestion"]["feedback"] == "child_liked"


async def test_feedback_not_found(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """없는 suggestion → 404."""
    headers, _ = bearer
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/suggestions/{uuid.uuid4()}/feedback",
        json={"feedback": "not_acted"},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_feedback_other_child_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """남의 아이 추천 평가는 403."""
    # 다른 보호자의 아이에 제안 생성
    other_bearer = await issue_bearer(session, token="other-token")
    _, other_pid = other_bearer
    other_cid = await link_child(session, parent_id=other_pid)
    s = await _make_suggestion(session, child_id=other_cid, status=SuggestionStatus.APPROVED)

    headers, _ = bearer
    resp = await db_client.patch(
        f"/api/v1/children/{other_cid}/suggestions/{s.id}/feedback",
        json={"feedback": "child_liked"},
        headers=headers,
    )
    assert resp.status_code == 403


# ── 일정 수정 ────────────────────────────────────────────────────────────


async def _make_event(session, *, child_id):
    event = await create_event(
        session,
        child_id=child_id,
        title="물놀이",
        event_type=EventType.EPISODIC,
        starts_at=datetime.now(UTC),
        ends_at=None,
        all_day=False,
        category=EventCategory.ACTIVITY,
        created_by=EventCreatedBy.CAREGIVER,
    )
    i1 = await create_event_item(session, child_id=child_id, event_id=event.id, item_name="수건")
    i2 = await create_event_item(session, child_id=child_id, event_id=event.id, item_name="모자")
    await session.flush()
    return event, [i1, i2]


@pytest.fixture(autouse=True)
def _clear_idempotency():
    yield
    idempotency.clear()


async def test_update_event_basic(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """일정 수정이 DB에 반영된다."""
    event, items = await _make_event(session, child_id=cid)
    headers, _ = bearer
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/events/{event.id}",
        json={
            "event": {
                "title": "수영",
                "starts_at": "2026-10-10T10:00:00+09:00",
                "ends_at": None,
                "all_day": False,
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [
                {"item_id": str(items[0].item_id), "item_name": "수건"},
                {"item_id": None, "item_name": "썬크림"},
            ],
        },
        headers={**headers, "Idempotency-Key": "upd-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["event"]["title"] == "수영"
    assert len(data["event"]["items"]) == 2
    item_names = {i["item_name"] for i in data["event"]["items"]}
    assert item_names == {"수건", "썬크림"}


async def test_update_event_preserves_is_prepared(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """기존 item의 is_prepared가 보존된다."""
    from app.domains.schedule.repository import set_event_item_prepared

    event, items = await _make_event(session, child_id=cid)
    # 수건을 체크
    await set_event_item_prepared(
        session, child_id=cid, item_id=items[0].item_id, is_prepared=True
    )
    await session.flush()

    headers, _ = bearer
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/events/{event.id}",
        json={
            "event": {
                "title": "물놀이",
                "starts_at": "2026-10-10T10:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [
                {"item_id": str(items[0].item_id), "item_name": "수건"},
            ],
        },
        headers={**headers, "Idempotency-Key": "upd-2"},
    )
    assert resp.status_code == 200
    towel = resp.json()["event"]["items"][0]
    assert towel["item_name"] == "수건"
    assert towel["is_prepared"] is True


async def test_update_event_requires_idempotency_key(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """Idempotency-Key 없으면 400."""
    event, _ = await _make_event(session, child_id=cid)
    headers, _ = bearer
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/events/{event.id}",
        json={
            "event": {
                "title": "물놀이",
                "starts_at": "2026-10-10T10:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "idempotency_key_required"


async def test_update_event_not_found(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    """없는 일정 → 404."""
    headers, _ = bearer
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/events/{uuid.uuid4()}",
        json={
            "event": {
                "title": "없는 일정",
                "starts_at": "2026-10-10T10:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [],
        },
        headers={**headers, "Idempotency-Key": "upd-3"},
    )
    assert resp.status_code == 404
