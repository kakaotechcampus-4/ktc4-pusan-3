"""추천 목록 · 평가 · 추천 받기 가드 · 일정 수정 통합 테스트 — #284."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import agent_ports, idempotency
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
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
    kind: SuggestionKind = SuggestionKind.PERSONALIZED,
    with_evidence: bool = False,
) -> Suggestion:
    row = Suggestion(
        child_id=child_id,
        agent=agent,
        kind=kind,
        content="공원 산책",
        reason="야외 활동 선호",
        status=status,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    session.add(row)
    await session.flush()
    if with_evidence:
        # 근거는 원본 행이 있어야 화면에 나간다 (#323) — 지운 · 없는 원본은 빠진다
        affinity = ProfileAffinity(
            child_id=child_id,
            merge_key="공원 산책",
            domain=MemoryDomain.ACTIVITY,
            last_observed_on=datetime.now(UTC).date(),
        )
        session.add(affinity)
        await session.flush()
        session.add(
            SuggestionEvidence(
                suggestion_id=row.id,
                source_kind="profile_affinity",
                source_id=affinity.id,
                note="반복 3회",
            )
        )
        await session.flush()
    return row


# ── 추천 받기 가드 ───────────────────────────────────────────────────────


# 🚨 05 가 돌려주는 근거는 label 이 사람이 읽을 수 있는 값이고 is_stale 이 null 이 아니어야 한다.
#    진짜 entrypoint 가 돈다. 운영에서는 러너 · 05 가 안전 어댑터를 넘겨 Activity 가 열리지만(#460),
#    여기서는 넘기지 않아(UnwiredSafety) 모델 호출 없이 닫힌다 — 실제 LLM 을 부르지 않게. (#323)
_CHILD_RECORD_KINDS = frozenset(
    (
        "observation_food",
        "observation_health",
        "observation_education",
        "observation_activity",
        "observation_routine",
        "profile_affinity",
        "child_growth_log",
        "notice",
        "daycare_meal",
    )
)


async def test_evidence_label_and_is_stale_are_filled(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID, monkeypatch: pytest.MonkeyPatch
):
    """evidence.label은 source_kind가 아닌 사람이 읽을 수 있는 값, is_stale은 bool이어야 한다."""
    from app.agents.common import llm_client

    made: list[object] = []
    real_init = llm_client.LLMClient.__init__

    def counting_init(self: object, *args: object, **kwargs: object) -> None:
        made.append(kwargs.get("role"))  # Agent 가 예외를 실패로 삼켜서 세어 둔다
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(llm_client.LLMClient, "__init__", counting_init)
    monkeypatch.setattr(agent_ports, "safety_readers", dict)  # 안전 어댑터를 넘기지 않는다
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions",
        json={"agents": ["activity"]},
        headers=headers,
    )
    assert resp.status_code == 200

    data = resp.json()
    for suggestion in data.get("suggestions", []):
        for ev in suggestion.get("evidence", []):
            assert ev["label"] not in _CHILD_RECORD_KINDS, (
                f"evidence.label이 source_kind 식별자 그대로다: {ev['label']}. "
                "사람이 읽을 수 있는 라벨을 채워야 한다 (#323)"
            )
            assert ev["is_stale"] is not None, (
                "evidence.is_stale이 null이다. 6개월 규칙(§2)이 화면에서 빠진다 (#323)"
            )
    # 실제 LLM 을 부르지 않는다 — 부르면 로컬 키로 유료 호출이 나가고 결과가 매번 달라진다
    assert made == []


async def test_answer_question_returns_501(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
    """POST /children/{cid}/answers는 agent 연결 전까지 501."""
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/answers",
        json={"question_id": "q1", "answer": "국수를 잘 먹어요"},
        headers=headers,
    )
    assert resp.status_code == 501


# ── 추천 목록 ────────────────────────────────────────────────────────────


async def test_list_suggestions_empty(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
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
    ev = item["evidence"][0]
    assert ev["note"] == "반복 3회"
    assert ev["ref"]["kind"] == "profile_affinity"
    # 이름 · 낡음은 원본(관심 행)에서 온다 (#323)
    assert ev["label"] == "공원 산책"
    assert ev["is_stale"] is False
    assert len(item["source_refs"]) == 1


async def test_list_suggestions_includes_general(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """채택한 일반 추천도 07 목록에 kind 로 구분돼 나온다 (#532).

    일반 추천은 문서 행을 근거로 달고 저장되지만, 목록은 아이 기록 근거만 싣는다 — 문서 근거가
    07 에 실리면 일반 추천이 "우리 아이 기록에서 나온 것" 처럼 읽힌다 (§2 타입 구분).
    아직 채택 안 한 일반 추천(draft)은 기본 목록에 없다.
    """
    p = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, with_evidence=True
    )
    g = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, kind=SuggestionKind.GENERAL
    )
    session.add(
        SuggestionEvidence(
            suggestion_id=g.id, source_kind="activity_doc", source_id=uuid.uuid4(), note="문서"
        )
    )
    await session.flush()
    draft = await _make_suggestion(session, child_id=cid, kind=SuggestionKind.GENERAL)
    headers, _ = bearer
    resp = await db_client.get(f"/api/v1/children/{cid}/suggestions", headers=headers)
    assert resp.status_code == 200
    items = {i["id"]: i for i in resp.json()["items"]}
    assert items[str(p.id)]["kind"] == "personalized"
    assert len(items[str(p.id)]["evidence"]) == 1
    general = items[str(g.id)]
    assert general["kind"] == "general"
    assert general["evidence"] == []
    assert general["source_refs"] == []
    # 05 의 basis 와 같은 문구 — 둘 다 행의 reason 이다
    assert general["reason"] == g.reason
    assert str(draft.id) not in items


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


async def test_feedback_on_general(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """일반 추천 평가도 저장된다 — 평가는 kind 를 보지 않는다 (#532)."""
    s = await _make_suggestion(
        session, child_id=cid, status=SuggestionStatus.APPROVED, kind=SuggestionKind.GENERAL
    )
    headers, _ = bearer
    resp = await db_client.patch(
        f"/api/v1/children/{cid}/suggestions/{s.id}/feedback",
        json={"feedback": "child_liked"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["suggestion"]["kind"] == "general"
    assert resp.json()["suggestion"]["feedback"] == "child_liked"


async def test_approve_response_has_no_evidence(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """채택 응답은 근거를 읽지 않는다 — 근거가 있는 개인화도 빈 배열 (#532 계약)."""
    s = await _make_suggestion(session, child_id=cid, with_evidence=True)
    headers, _ = bearer
    resp = await db_client.post(
        f"/api/v1/children/{cid}/suggestions/approve",
        json={"suggestion_ids": [str(s.id)]},
        headers=headers,
    )
    assert resp.status_code == 200
    [approved] = resp.json()["suggestions"]
    assert approved["kind"] == "personalized"
    assert approved["evidence"] == []
    assert approved["source_refs"] == []


async def test_feedback_not_found(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
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
    await set_event_item_prepared(session, child_id=cid, item_id=items[0].item_id, is_prepared=True)
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
    assert towel["prepared_at"] is not None


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


async def test_update_event_not_found(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
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
