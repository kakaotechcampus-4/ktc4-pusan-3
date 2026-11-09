"""채택 · 일정 초안 · 일정 제출 API 통합 테스트 — #236.

PR #196 멘토 합의: approved = "보호자가 채택했다", 일정은 연결 테이블로.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.domains.safety.models import SafetyKind
from app.domains.safety.repository import create_safety
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
    kind: SuggestionKind = SuggestionKind.PERSONALIZED,
    expires_at: datetime | None = None,
    items: Sequence[str] = (),
    allergens: Sequence[str] = (),
) -> Suggestion:
    """테스트용 제안 직접 INSERT."""
    row = Suggestion(
        child_id=child_id,
        agent=agent,
        kind=kind,
        content="공원 산책",
        reason="야외 활동 선호",
        status=status,
        items=list(items),
        allergens=list(allergens),
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
    # "보호자당 아이 1명" 유니크 제약(#198 · #214)에 막힌다
    _, other_parent_id = await issue_bearer(session, token="test-token-other-parent")
    other_cid = await link_child(session, parent_id=other_parent_id)
    s = await _make_suggestion(session, child_id=other_cid)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# ── 채택 직전 알레르기 재확인 ─────────────────────────────────────────────────


def _granted(value: bool):
    async def read(session, *, child_id):
        return value

    return read


async def _approve(db_client: AsyncClient, cid: uuid.UUID, headers, *rows: Suggestion):
    return await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(r.id) for r in rows]},
        headers=headers,
    )


async def test_채택_전에_등록된_알레르기에_걸리면_아무것도_채택하지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """목록을 받은 뒤 등록된 알레르기 — 걸린 추천만 detail 에 싣고, 같이 보낸 다른 추천도 남긴다."""
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    headers, parent_id = bearer
    safe = await _make_suggestion(session, child_id=cid, agent=SuggestionAgent.FOOD)
    walnut = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, allergens=["호두", "잣"]
    )
    await create_safety(
        session, child_id=cid, parent_id=parent_id, kind=SafetyKind.ALLERGY, label="호두"
    )

    resp = await _approve(db_client, cid, headers, safe, walnut)

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "allergen_blocked"
    assert error["detail"] == {"suggestion_ids": [str(walnut.id)]}
    for row in (safe, walnut):
        await session.refresh(row)
        assert row.status == SuggestionStatus.DRAFT


async def test_채택_재확인은_목록과_같은_이름_사전으로_맞춘다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """견과류로 등록한 알레르기에 호두가 든 추천이 걸린다 — 목록이 빼는 것과 같다."""
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    headers, parent_id = bearer
    row = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, allergens=["호두"]
    )
    await create_safety(
        session, child_id=cid, parent_id=parent_id, kind=SafetyKind.ALLERGY, label="견과류"
    )

    resp = await _approve(db_client, cid, headers, row)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "allergen_blocked"


async def test_알레르기_기록에_없는_항목은_채택을_막지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """답을 미룬 "모름"(행 없음)은 막지 않는다 (event-draft-ui-v1.md §3-8)."""
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    headers, _ = bearer
    row = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, allergens=["잣"]
    )

    resp = await _approve(db_client, cid, headers, row)

    assert resp.status_code == 200
    assert resp.json()["suggestions"][0]["status"] == "approved"


async def test_알레르기_항목이_없는_추천만_채택하면_건강정보를_읽지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    async def must_not_read(*args, **kwargs):
        raise AssertionError("건강정보를 읽었다")

    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", must_not_read)
    monkeypatch.setattr("app.api.v1.routers.suggestions.list_safety_labels", must_not_read)
    headers, _ = bearer
    row = await _make_suggestion(session, child_id=cid)

    resp = await _approve(db_client, cid, headers, row)

    assert resp.status_code == 200


async def test_건강정보_동의가_없으면_알레르기_항목이_든_추천을_채택하지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """목록도 동의 없이는 이 추천을 내보내지 않는다 — 동의 없이 건강정보를 읽어 판정하지 않는다."""
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(False))
    headers, _ = bearer
    row = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, allergens=["호두"]
    )

    resp = await _approve(db_client, cid, headers, row)

    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "consent_required"
    assert error["detail"]["scopes"] == ["child_health"]
    await session.refresh(row)
    assert row.status == SuggestionStatus.DRAFT


async def test_이미_채택한_추천은_새로_고른_추천과_같이_와도_알레르기를_다시_보지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """채택 뒤에 등록된 알레르기로 지난 채택을 뒤집지 않는다 — 새로 고른 추천만 다시 본다."""
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    headers, parent_id = bearer
    earlier = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        allergens=["호두"],
    )
    fresh = await _make_suggestion(session, child_id=cid, agent=SuggestionAgent.FOOD)
    await create_safety(
        session, child_id=cid, parent_id=parent_id, kind=SafetyKind.ALLERGY, label="호두"
    )

    resp = await _approve(db_client, cid, headers, earlier, fresh)

    assert resp.status_code == 200
    assert {s["status"] for s in resp.json()["suggestions"]} == {"approved"}
    await session.refresh(fresh)
    assert fresh.status == SuggestionStatus.APPROVED


async def test_만료된_추천이_섞이면_알레르기보다_만료를_먼저_알린다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    headers, parent_id = bearer
    walnut = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, allergens=["호두"]
    )
    expired = await _make_suggestion(
        session, child_id=cid, expires_at=datetime.now(UTC) - timedelta(hours=1)
    )
    await create_safety(
        session, child_id=cid, parent_id=parent_id, kind=SafetyKind.ALLERGY, label="호두"
    )

    resp = await _approve(db_client, cid, headers, walnut, expired)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "suggestion_expired"


async def test_알레르기를_못_읽으면_통과시키지_않고_아무것도_채택하지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """못 읽었다고 기본값으로 넘기지 않는다 (루트 §2) — 예외가 그대로 올라간다."""
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))

    async def broken(*args, **kwargs):
        raise RuntimeError("DB 를 읽지 못했다")

    monkeypatch.setattr("app.api.v1.routers.suggestions.list_safety_labels", broken)
    headers, _ = bearer
    row = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, allergens=["호두"]
    )

    with pytest.raises(RuntimeError):
        await _approve(db_client, cid, headers, row)

    await session.refresh(row)
    assert row.status == SuggestionStatus.DRAFT


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


# 🚨 채택 · 초안 · 제출은 kind 를 보지 않는다 — 05 의 일반 추천도 같은 길로 일정이 된다
#    (#512 · #532). 나중에 kind 검사가 생기면 여기가 깨져야 한다.


async def test_general_suggestion_approve_draft_submit(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """일반 추천 id 로 approve → event-drafts → events 까지 통과한다."""
    idempotency.clear()
    s = await _make_suggestion(session, child_id=cid, kind=SuggestionKind.GENERAL)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 200
    [approved] = resp.json()["suggestions"]
    assert approved["kind"] == "general"
    assert approved["status"] == "approved"
    # 채택 응답은 근거를 읽지 않는다 — 개인화도 빈 배열 (아래 테스트)
    assert approved["evidence"] == []
    assert approved["source_refs"] == []

    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    draft = resp.json()["drafts"][0]

    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        # 초안은 일자를 모른다 — 보호자가 승인 시트에서 채운다
        json={
            "event": {**draft["event"], "starts_at": "2026-10-10T15:00:00+09:00"},
            "items": [],
            "suggestion_ids": draft["suggestion_ids"],
        },
        headers={**headers, "Idempotency-Key": "general-submit"},
    )
    assert resp.status_code == 201
    link = await session.scalar(
        select(SuggestionEvent).where(SuggestionEvent.suggestion_id == s.id)
    )
    assert link is not None


async def test_event_drafts_food_grouped_across_kinds(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """food 묶기는 agent 기준이다 — 일반+일반, 개인화+일반 모두 초안 1장."""
    headers, _ = bearer
    for kinds in (
        (SuggestionKind.GENERAL, SuggestionKind.GENERAL),
        (SuggestionKind.PERSONALIZED, SuggestionKind.GENERAL),
    ):
        rows = [
            await _make_suggestion(
                session,
                child_id=cid,
                agent=SuggestionAgent.FOOD,
                status=SuggestionStatus.APPROVED,
                kind=kind,
            )
            for kind in kinds
        ]
        resp = await db_client.post(
            f"/api/v1/children/{cid}/suggestions/event-drafts",
            json={"suggestion_ids": [str(r.id) for r in rows]},
            headers=headers,
        )
        assert resp.status_code == 201
        drafts = resp.json()["drafts"]
        assert len(drafts) == 1
        assert drafts[0]["event"]["title"] == "저녁 식사"
        assert len(drafts[0]["suggestion_ids"]) == 2


async def test_event_drafts_carry_activity_items(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """활동 추천의 준비물이 초안에 그대로 실린다 (#315)."""
    s = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, items=["돗자리", "물통"]
    )
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    [draft] = resp.json()["drafts"]
    assert draft["items"] == [
        {"item_id": None, "item_name": "돗자리"},
        {"item_id": None, "item_name": "물통"},
    ]


async def test_event_drafts_food_items_merged_once(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """food 둘을 한 끼로 묶으면 겹치는 재료는 한 번 — 공백만 다른 것도 같은 재료 (#315)."""
    s1 = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        items=["두부", " 애호박", ""],
    )
    s2 = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        items=["두부 ", "양파"],
    )
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s1.id), str(s2.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    [draft] = resp.json()["drafts"]
    assert [i["item_name"] for i in draft["items"]] == ["두부", "애호박", "양파"]
    assert all(i["item_id"] is None for i in draft["items"])


async def test_event_drafts_food_core_items_merged_once(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """주재료만 남긴 food 둘을 한 끼로 묶어도 핵심 재료는 처음 나온 순서로 한 번씩이다 (#474)."""
    s1 = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        items=["두부", "시금치"],
    )
    s2 = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        items=["두부", "당근"],
    )
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s1.id), str(s2.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    [draft] = resp.json()["drafts"]
    assert [i["item_name"] for i in draft["items"]] == ["두부", "시금치", "당근"]


async def test_event_drafts_food_without_items_make_empty_draft_items(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """주재료가 없거나 모두 상비 재료라 items 가 비어도 초안은 만들어지고 준비물만 빈다 (#474).

    Activity 준비물은 그대로이고, food 초안과 섞이지 않는다.
    """
    food = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        items=[],
    )
    activity = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, items=["돗자리", "물통"]
    )
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(food.id), str(activity.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    by_suggestion = {
        tuple(d["suggestion_ids"]): [i["item_name"] for i in d["items"]]
        for d in resp.json()["drafts"]
    }
    assert by_suggestion == {(str(food.id),): [], (str(activity.id),): ["돗자리", "물통"]}


async def test_event_drafts_items_stay_with_their_suggestion(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """활동 추천 둘은 초안도 둘이고 준비물도 각자다. food 재료가 활동 초안에 섞이지 않는다."""
    a1 = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, items=["돗자리"]
    )
    a2 = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, items=["물감"]
    )
    f1 = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        items=["두부"],
    )
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(a1.id), str(f1.id), str(a2.id)]},
        headers=headers,
    )
    assert resp.status_code == 201
    by_suggestion = {
        tuple(d["suggestion_ids"]): [i["item_name"] for i in d["items"]]
        for d in resp.json()["drafts"]
    }
    assert by_suggestion == {
        (str(f1.id),): ["두부"],
        (str(a1.id),): ["돗자리"],
        (str(a2.id),): ["물감"],
    }


async def test_event_draft_items_can_be_submitted_as_is(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """초안의 준비물을 고치지 않고 그대로 제출해도 받는다 — 초안이 제출 상한 안에 있다."""
    idempotency.clear()
    s = await _make_suggestion(
        session,
        child_id=cid,
        status=SuggestionStatus.APPROVED,
        items=["가" * 101, "수영복", " 수영복"],
    )
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    [draft] = resp.json()["drafts"]
    assert [i["item_name"] for i in draft["items"]] == ["수영복"]

    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {**draft["event"], "starts_at": "2026-10-15T09:00:00+09:00"},
            "items": draft["items"],
            "suggestion_ids": draft["suggestion_ids"],
        },
        headers={**headers, "Idempotency-Key": "draft-items-as-is"},
    )
    assert resp.status_code == 201
    assert [i["item_name"] for i in resp.json()["event"]["items"]] == ["수영복"]


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


# ── 초안 만들기 직전 알레르기 재확인 (#859) ───────────────────────────────────
# 판정 · "모름" · 조회 실패는 같은 함수라 위의 채택 테스트가, 내린 기록은 사전검사 테스트
# (test_suggestions_endpoint.py · test_allergy_states.py)가 본다. 여기서는 초안 만들기가 그 함수를
# 부르는지와, 부르는 자리에서만 갈릴 수 있는 것만 본다.


async def _drafts(db_client: AsyncClient, cid: uuid.UUID, headers, *rows: Suggestion):
    return await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(r.id) for r in rows]},
        headers=headers,
    )


async def test_채택_뒤에_등록된_알레르기에_걸리면_초안을_하나도_만들지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """food 를 한 장으로 묶기 전에 본다 — 같이 고른 food · 놀이 중 걸린 추천 id 만 싣는다."""
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    headers, parent_id = bearer
    approved = SuggestionStatus.APPROVED
    rice = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, status=approved
    )
    walnut = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, status=approved, allergens=["호두"]
    )
    play = await _make_suggestion(session, child_id=cid, status=approved)
    await create_safety(
        session, child_id=cid, parent_id=parent_id, kind=SafetyKind.ALLERGY, label="견과류"
    )

    resp = await _drafts(db_client, cid, headers, rice, walnut, play)

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "allergen_blocked"
    assert error["detail"] == {"suggestion_ids": [str(walnut.id)]}


async def test_채택_뒤에_건강정보_동의를_거두면_알레르기_항목이_든_추천으로_초안을_만들지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """동의가 없어 알레르기를 못 읽는 것을 "걸린 것 없음" 으로 넘기지 않는다 (루트 §2).

    부르는 자리에서 동의를 먼저 보고 재확인을 건너뛰게 바뀌면 여기서만 깨진다.
    """
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(False))
    headers, _ = bearer
    walnut = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        allergens=["호두"],
    )

    resp = await _drafts(db_client, cid, headers, walnut)

    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "consent_required"
    assert error["detail"]["scopes"] == ["child_health"]


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


# ── 제출 직전 알레르기 재확인 (#902) ─────────────────────────────────────────
# 판정 · 모름 · 조회 실패는 같은 함수라 채택 테스트가 본다. 여기서는 제출이 그 함수를 쓰기 전에
# 부르는지와, 부르는 자리에서만 갈릴 수 있는 것만 본다.


async def _submit(db_client: AsyncClient, cid: uuid.UUID, headers, key: str, *rows: Suggestion):
    return await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "저녁 식사",
                "starts_at": "2026-10-10T18:00:00+09:00",
                "ends_at": None,
                "all_day": False,
                "event_type": "episodic",
                "category": "etc",
            },
            "items": [],
            "suggestion_ids": [str(r.id) for r in rows],
        },
        headers={**headers, "Idempotency-Key": key},
    )


async def test_초안을_만든_뒤_등록된_알레르기에_걸리면_일정을_넣지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """05 시트를 열어 둔 사이 등록된 알레르기 — 걸린 id 만 싣고, 일정 · 연결을 쓰지 않는다."""
    from app.domains.schedule.models import Event

    idempotency.clear()
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(True))
    headers, parent_id = bearer
    approved = SuggestionStatus.APPROVED
    rice = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, status=approved
    )
    walnut = await _make_suggestion(
        session, child_id=cid, agent=SuggestionAgent.FOOD, status=approved, allergens=["호두"]
    )
    await create_safety(
        session, child_id=cid, parent_id=parent_id, kind=SafetyKind.ALLERGY, label="견과류"
    )

    resp = await _submit(db_client, cid, headers, "allergen-1", rice, walnut)

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "allergen_blocked"
    assert error["detail"] == {"suggestion_ids": [str(walnut.id)]}
    assert (await session.scalars(select(Event).where(Event.child_id == cid))).all() == []
    assert (await session.scalars(select(SuggestionEvent))).all() == []


async def test_초안을_만든_뒤_건강정보_동의를_거두면_알레르기_항목이_든_추천으로_일정을_넣지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """동의가 없어 알레르기를 못 읽는 것을 "걸린 것 없음" 으로 넘기지 않는다 (루트 §2).

    부르는 자리에서 동의를 먼저 보고 재확인을 건너뛰게 바뀌면 여기서만 깨진다.
    """
    idempotency.clear()
    monkeypatch.setattr("app.api.v1.routers.suggestions.child_health_granted", _granted(False))
    headers, _ = bearer
    walnut = await _make_suggestion(
        session,
        child_id=cid,
        agent=SuggestionAgent.FOOD,
        status=SuggestionStatus.APPROVED,
        allergens=["호두"],
    )

    resp = await _submit(db_client, cid, headers, "allergen-2", walnut)

    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "consent_required"
    assert error["detail"]["scopes"] == ["child_health"]


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

    drafts, _ = await repo.list_suggestions(session, child_id=cid, status=SuggestionStatus.DRAFT)
    draft_ids = {s.id for s in drafts}
    assert s_alive.id in draft_ids  # 살아있는 draft는 보인다
    assert s_expired.id not in draft_ids  # 만료된 draft는 안 보인다

    all_approved, _ = await repo.list_suggestions(
        session, child_id=cid, status=SuggestionStatus.APPROVED
    )
    assert s_approved.id in {s.id for s in all_approved}  # approved는 만료 무관


# ── 남의 아이 제안 방어 ───────────────────────────────────────────────────


async def test_event_drafts_other_childs_suggestion(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """남의 아이 제안으로 초안 요청 → 404."""
    _, other_parent_id = await issue_bearer(session, token="test-token-other-parent")
    other_cid = await link_child(session, parent_id=other_parent_id)
    s = await _make_suggestion(session, child_id=other_cid, status=SuggestionStatus.APPROVED)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/event-drafts",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_submit_event_other_childs_suggestion(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """남의 아이 제안으로 일정 제출 → 404."""
    idempotency.clear()
    _, other_parent_id = await issue_bearer(session, token="test-token-other-parent")
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
    assert resp.json()["error"]["code"] == "not_found"


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


async def test_Redis_가_죽으면_일정_등록은_503_이고_아무것도_저장하지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """#671 — 장애 직전에 Redis 에 적힌 키는 장애 동안 안 보여서, 같은 키 재시도가 일정을
    하나 더 만든다. 되돌릴 수 없는 쓰기(승인 게이트 ㉠)라 메모리로 넘어가지 않고 막는다
    (docs/api/idempotency-v1.md §3-6).
    """
    import redis

    from app.domains.schedule.models import Event

    idempotency.clear()
    down = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    monkeypatch.setattr(idempotency, "get_redis", lambda: down)
    headers, _ = bearer

    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={
            "event": {
                "title": "장애 중 일정",
                "starts_at": "2026-10-10T15:00:00+09:00",
                "ends_at": None,
                "all_day": False,
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [],
        },
        headers={**headers, "Idempotency-Key": "redis-down-1"},
    )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "temporarily_unavailable"
    assert (await session.scalars(select(Event).where(Event.child_id == cid))).all() == []


async def test_Redis_가_쓰기만_거절해도_같은_키로_일정이_두_번_생기지_않는다(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, monkeypatch
):
    """#671 리뷰 — RDB 저장 실패(MISCONF)면 GET 은 되고 SET 만 거절된다. GET 만 보고 통과시키면
    저장 · commit 뒤 키를 못 남겨 같은 키 재시도가 일정을 하나 더 만든다."""
    from app.domains.schedule.models import Event
    from tests.unit.api.test_idempotency_redis import WriteRefused

    idempotency.clear()
    monkeypatch.setattr(idempotency, "get_redis", lambda: WriteRefused())
    headers, _ = bearer
    body = {
        "event": {
            "title": "쓰기 거절 중 일정",
            "starts_at": "2026-10-10T15:00:00+09:00",
            "ends_at": None,
            "all_day": False,
            "event_type": "episodic",
            "category": "activity",
        },
        "items": [],
    }

    codes = [
        (
            await db_client.post(
                f"/api/v1/children/{cid}/events",
                json=body,
                headers={**headers, "Idempotency-Key": "misconf-1"},
            )
        ).status_code
        for _ in range(2)
    ]

    assert codes == [503, 503]
    assert (await session.scalars(select(Event).where(Event.child_id == cid))).all() == []
