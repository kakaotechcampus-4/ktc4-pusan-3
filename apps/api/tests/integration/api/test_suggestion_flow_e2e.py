"""추천 → 채택 → 초안 → 일정 등록 → 목록 → 평가 → 일정 수정 E2E 플로우 테스트.

각 API를 순서대로 호출해서 앞 단계 결과가 다음 단계 입력으로 이어지는지 검증한다.
리뷰 반영 사항(draft 제외, feedback 제한, 검증 등)도 같은 플로우 안에서 확인한다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
    SuggestionKind,
    SuggestionStatus,
)
from tests.integration.api.conftest import Bearer


@pytest.fixture(autouse=True)
def _clear_idempotency():
    yield
    idempotency.clear()


async def test_full_suggestion_to_event_flow(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """추천 생성 → 채택 → 초안 → 일정 등록 → 목록 조회 → 평가 → 일정 수정.

    전체 플로우를 한 테스트에서 순서대로 호출한다.
    """
    headers, _ = bearer

    # ── 0. 추천 + 근거 생성 (Agent stub이라 DB 직접) ─────────────────────
    s1 = Suggestion(
        child_id=cid,
        agent=SuggestionAgent.ACTIVITY,
        kind=SuggestionKind.PERSONALIZED,
        content="공원 산책",
        reason="야외 활동 선호",
        status=SuggestionStatus.DRAFT,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    s2 = Suggestion(
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        kind=SuggestionKind.PERSONALIZED,
        content="계란말이",
        reason="계란 반찬 선호",
        status=SuggestionStatus.DRAFT,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    session.add_all([s1, s2])
    await session.flush()
    session.add(
        SuggestionEvidence(
            suggestion_id=s1.id,
            source_kind="observation_activity",
            source_id=uuid.uuid4(),
            note="공원에서 뛰어놀았다",
        )
    )
    await session.flush()

    # ── 1. 채택 (draft → approved) ──────────────────────────────────────
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s1.id), str(s2.id)]},
        headers=headers,
    )
    assert resp.status_code == 200
    approved = resp.json()["suggestions"]
    assert len(approved) == 2
    assert all(s["status"] == "approved" for s in approved)

    # ── 2. 일정 초안 (DB 저장 없음) ─────────────────────────────────────
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s1.id), str(s2.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    drafts = resp.json()["drafts"]
    # food는 하나로 묶이고, activity는 별도 → 초안 2개
    assert len(drafts) == 2
    # 시간은 비어있음 (보호자가 채울 것)
    for d in drafts:
        assert d["event"]["starts_at"] is None

    # ── 3. 일정 등록 (승인 게이트 ㉠) ───────────────────────────────────
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "공원 산책",
                "starts_at": "2026-10-15T10:00:00+09:00",
                "ends_at": None,
                "all_day": False,
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [{"item_id": None, "item_name": "모자"}],
            "suggestion_ids": [str(s1.id)],
        },
        headers={**headers, "Idempotency-Key": "flow-event-1"},
    )
    assert resp.status_code == 201
    event = resp.json()["event"]
    event_id = event["id"]
    assert event["title"] == "공원 산책"
    assert event["created_by"] == "agent"  # suggestion에서 왔으니 agent
    assert len(event["items"]) == 1
    item_id = event["items"][0]["item_id"]
    assert event["items"][0]["is_prepared"] is False

    # ── 4. 같은 추천 재등록 → 409 ──────────────────────────────────────
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "중복",
                "starts_at": "2026-10-15T14:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [],
            "suggestion_ids": [str(s1.id)],
        },
        headers={**headers, "Idempotency-Key": "flow-event-dup"},
    )
    assert resp.status_code == 409

    # ── 5. 목록 조회 — approved만, draft 제외, evidence 포함 ────────────
    resp = await db_client.get(
        f"/api/v1/children/{cid}/suggestions",
        headers=headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    # draft 2개를 approved로 바꿨으니 2개 보여야 함
    assert len(items) == 2
    # 모든 항목이 draft가 아님
    assert all(i["status"] != "draft" for i in items)
    # s1의 evidence 확인
    s1_item = next(i for i in items if i["id"] == str(s1.id))
    assert len(s1_item["evidence"]) == 1
    assert s1_item["evidence"][0]["note"] == "공원에서 뛰어놀았다"
    assert len(s1_item["source_refs"]) == 1

    # ── 6. 평가 ─────────────────────────────────────────────────────────
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/suggestions/{s1.id}/feedback",
        json={"feedback": "child_liked"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["suggestion"]["feedback"] == "child_liked"

    # ── 7. 일정 수정 — 준비물 추가, 기존 유지 ──────────────────────────
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/events/{event_id}",
        json={
            "event": {
                "title": "공원 산책 (수정)",
                "starts_at": "2026-10-15T11:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [
                {"item_id": item_id, "item_name": "모자"},
                {"item_id": None, "item_name": "물통"},
            ],
        },
        headers={**headers, "Idempotency-Key": "flow-update-1"},
    )
    assert resp.status_code == 200
    updated = resp.json()["event"]
    assert updated["title"] == "공원 산책 (수정)"
    assert len(updated["items"]) == 2
    item_names = {i["item_name"] for i in updated["items"]}
    assert item_names == {"모자", "물통"}


async def test_draft_excluded_from_default_list(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """필터 없이 목록을 부르면 draft는 나오지 않는다."""
    headers, _ = bearer
    session.add(
        Suggestion(
            child_id=cid,
            agent=SuggestionAgent.ACTIVITY,
            kind=SuggestionKind.PERSONALIZED,
            content="draft 추천",
            status=SuggestionStatus.DRAFT,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    session.add(
        Suggestion(
            child_id=cid,
            agent=SuggestionAgent.ACTIVITY,
            kind=SuggestionKind.PERSONALIZED,
            content="approved 추천",
            status=SuggestionStatus.APPROVED,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    await session.flush()

    resp = await db_client.get(f"/api/v1/children/{cid}/suggestions", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["status"] != "draft" for i in items)
    assert any(i["content"] == "approved 추천" for i in items)


async def test_feedback_rejected_on_draft(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """draft 상태 추천에 feedback을 보내면 404 (approved/rejected만 허용)."""
    headers, _ = bearer
    s = Suggestion(
        child_id=cid,
        agent=SuggestionAgent.ACTIVITY,
        kind=SuggestionKind.PERSONALIZED,
        content="draft 추천",
        status=SuggestionStatus.DRAFT,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    session.add(s)
    await session.flush()

    resp = await db_client.patch(
        f"/api/v1/children/{cid}/suggestions/{s.id}/feedback",
        json={"feedback": "child_liked"},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_invalid_cursor_returns_400(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
    """잘못된 cursor → 500이 아니라 400."""
    headers, _ = bearer
    resp = await db_client.get(
        f"/api/v1/children/{cid}/suggestions?cursor=not-valid-base64",
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_failed"


async def test_invalid_status_returns_400(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
    """잘못된 status 값 → 500이 아니라 400."""
    headers, _ = bearer
    resp = await db_client.get(
        f"/api/v1/children/{cid}/suggestions?status=invalid",
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_failed"


async def _make_event_with_items(session, *, child_id):
    """테스트용 일정 + 준비물 2개."""
    from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType
    from app.domains.schedule.repository import create_event, create_event_item

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
    i1 = await create_event_item(
        session, child_id=child_id, event_id=event.id, item_name="수건"
    )
    i2 = await create_event_item(
        session, child_id=child_id, event_id=event.id, item_name="모자"
    )
    await session.flush()
    return event, [i1, i2]


async def test_update_event_other_events_item_id_returns_400(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """다른 일정의 item_id가 오면 400."""
    headers, _ = bearer
    event1, _ = await _make_event_with_items(session, child_id=cid)
    event2, items2 = await _make_event_with_items(session, child_id=cid)

    # event1을 수정하면서 event2의 item_id를 보냄
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/events/{event1.id}",
        json={
            "event": {
                "title": "물놀이",
                "starts_at": "2026-10-15T10:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [
                {"item_id": str(items2[0].item_id), "item_name": "남의 수건"},
            ],
        },
        headers={**headers, "Idempotency-Key": "other-item-1"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_failed"


async def test_update_event_deleted_item_id_is_ignored(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """이미 삭제된 item_id는 무시하고 나머지를 정상 저장한다."""
    headers, _ = bearer
    event, items = await _make_event_with_items(session, child_id=cid)
    deleted_id = str(uuid.uuid4())  # DB에 없는 item_id

    resp = await db_client.patch(
        f"/api/v1/children/{cid}/events/{event.id}",
        json={
            "event": {
                "title": "물놀이",
                "starts_at": "2026-10-15T10:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [
                {"item_id": str(items[0].item_id), "item_name": "수건"},
                {"item_id": deleted_id, "item_name": "없는 준비물"},
                {"item_id": None, "item_name": "썬크림"},
            ],
        },
        headers={**headers, "Idempotency-Key": "deleted-item-1"},
    )
    assert resp.status_code == 200
    result_items = resp.json()["event"]["items"]
    result_names = {i["item_name"] for i in result_items}
    # 삭제된 id는 무시, 수건(유지) + 썬크림(신규)만 남아야 함
    assert result_names == {"수건", "썬크림"}


async def test_update_event_without_idempotency_key_returns_400(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """일정 수정에 Idempotency-Key가 없으면 400."""
    from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType
    from app.domains.schedule.repository import create_event

    headers, _ = bearer
    event = await create_event(
        session,
        child_id=cid,
        title="테스트",
        event_type=EventType.EPISODIC,
        starts_at=datetime.now(UTC),
        ends_at=None,
        all_day=False,
        category=EventCategory.ACTIVITY,
        created_by=EventCreatedBy.CAREGIVER,
    )
    await session.flush()

    resp = await db_client.patch(
        f"/api/v1/children/{cid}/events/{event.id}",
        json={
            "event": {
                "title": "수정",
                "starts_at": "2026-10-15T10:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "idempotency_key_required"
