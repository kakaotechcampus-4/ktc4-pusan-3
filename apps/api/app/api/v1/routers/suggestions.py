"""제안 채택·일정 초안·일정 제출·추천 생성·목록·평가·일정 수정 — #236 · #284.

채택은 되돌릴 수 있어 승인 게이트에 해당하지 않는다.
일정 제출·수정만 승인 게이트 ㉠ — Idempotency-Key 필수.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api import idempotency
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope, constraint_name
from app.api.v1.schemas.common import Ref
from app.api.v1.schemas.suggestions import (
    AnswerRequest,
    ApproveSuggestionsRequest,
    ApproveSuggestionsResponse,
    CalendarEventOut,
    CreateEventDraftsRequest,
    CreateEventDraftsResponse,
    EventBodyOut,
    EventDraftOut,
    EventItemOut,
    EvidenceOut,
    SubmitEventRequest,
    SubmitEventResponse,
    SuggestionFeedbackRequest,
    SuggestionFeedbackResponse,
    SuggestionListResponse,
    SuggestionOut,
    SuggestionsRequest,
    UpdateEventRequest,
)
from app.domains.schedule import repository as schedule_repo
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType
from app.domains.suggestion import repository as suggestion_repo
from app.domains.suggestion.models import SuggestionFeedback, SuggestionKind, SuggestionStatus

router = APIRouter()


def _suggestion_out(s, evidence_rows=None) -> SuggestionOut:
    evidence = []
    source_refs = []
    if evidence_rows:
        for e in evidence_rows:
            source_refs.append(Ref(kind=e.source_kind, id=str(e.source_id)))
            evidence.append(
                EvidenceOut(
                    ref=Ref(kind=e.source_kind, id=str(e.source_id)),
                    label=e.source_kind,
                    observed_to=e.created_at.isoformat(),
                    confidence_source="",
                    note=e.note,
                )
            )
    return SuggestionOut(
        id=str(s.id),
        child_id=str(s.child_id),
        agent=str(s.agent),
        kind=str(s.kind),
        content=s.content,
        reason=s.reason,
        status=str(s.status),
        expires_at=s.expires_at.isoformat(),
        feedback=str(s.feedback) if s.feedback else None,
        source_refs=source_refs,
        evidence=evidence,
    )


# ── 채택 ──────────────────────────────────────────────────────────────────


@router.post(
    "/children/{cid}/suggestions/approve",
    status_code=200,
    responses={status: {"model": ErrorEnvelope} for status in (400, 404, 409)},
)
async def approve_suggestions(
    child: AccessibleChild,
    body: ApproveSuggestionsRequest,
    session: SessionDep,
) -> ApproveSuggestionsResponse:
    ids = list(dict.fromkeys(body.suggestion_ids))  # 중복 제거, 순서 유지
    if not ids:
        raise ApiError(400, "validation_failed", "제안을 선택해 주세요")

    # 존재 + 소유 확인, 이미 approved면 멱등 처리
    found = []
    already_approved = []
    for sid in ids:
        row = await suggestion_repo.find_suggestion(
            session, child_id=child.child_id, suggestion_id=sid
        )
        if row is None:
            raise ApiError(404, "not_found", "제안을 찾을 수 없어요")
        if row.status == SuggestionStatus.APPROVED:
            already_approved.append(row)
            continue
        if row.status != SuggestionStatus.DRAFT:
            raise ApiError(409, "not_draft", "이미 처리된 제안이 포함돼 있어요")
        if row.expires_at <= datetime.now(timezone.utc):
            raise ApiError(409, "suggestion_expired", "만료된 제안이 포함돼 있어요")
        found.append(row)

    # 전부 이미 approved면 그대로 돌려준다 (멱등)
    if not found:
        return ApproveSuggestionsResponse(
            suggestions=[_suggestion_out(s) for s in already_approved]
        )

    to_approve = [s.id for s in found]
    approved = await suggestion_repo.approve_suggestions(
        session, child_id=child.child_id, suggestion_ids=to_approve
    )
    if not approved:
        raise ApiError(409, "not_draft", "이미 처리된 제안이 포함돼 있어요")

    await session.commit()
    return ApproveSuggestionsResponse(
        suggestions=[_suggestion_out(s) for s in approved + already_approved]
    )


# ── 일정 초안 ─────────────────────────────────────────────────────────────

_AGENT_CATEGORY = {"activity": "activity", "health": "health"}


def _next_draft_id() -> str:
    """초안 번호. 아이의 모든 초안 사이에서 유일해야 한다 (Agent SSE 초안 포함)."""
    return str(_uuid.uuid4())


@router.post(
    "/children/{cid}/suggestions/event-drafts",
    status_code=201,
    responses={status: {"model": ErrorEnvelope} for status in (400, 404, 422)},
)
async def create_event_drafts(
    child: AccessibleChild,
    body: CreateEventDraftsRequest,
    session: SessionDep,
) -> CreateEventDraftsResponse:
    draft_ids = list(dict.fromkeys(body.suggestion_ids))  # 중복 제거
    if not draft_ids:
        raise ApiError(400, "validation_failed", "제안을 선택해 주세요")

    suggestions = []
    for sid in draft_ids:
        row = await suggestion_repo.find_suggestion(
            session, child_id=child.child_id, suggestion_id=sid
        )
        if row is None:
            raise ApiError(404, "not_found", "제안을 찾을 수 없어요")
        if row.status != SuggestionStatus.APPROVED:
            raise ApiError(422, "not_approved", "채택되지 않은 제안이 포함돼 있어요")
        suggestions.append(row)

    food = [s for s in suggestions if s.agent == "food"]
    rest = [s for s in suggestions if s.agent != "food"]

    drafts: list[EventDraftOut] = []
    if food:
        drafts.append(
            EventDraftOut(
                draft_id=_next_draft_id(),
                event=EventBodyOut(
                    title="저녁 식사",
                    starts_at=None,
                    ends_at=None,
                    all_day=False,
                    event_type="episodic",
                    category="etc",
                ),
                items=[],
                suggestion_ids=[str(s.id) for s in food],
            )
        )
    for s in rest:
        category = _AGENT_CATEGORY.get(str(s.agent), "etc")
        drafts.append(
            EventDraftOut(
                draft_id=_next_draft_id(),
                event=EventBodyOut(
                    title=s.content,
                    starts_at=None,
                    ends_at=None,
                    all_day=False,
                    event_type="episodic",
                    category=category,
                ),
                items=[],
                suggestion_ids=[str(s.id)],
            )
        )

    return CreateEventDraftsResponse(drafts=drafts)


# ── 일정 제출 (승인 게이트 ㉠) ────────────────────────────────────────────


@router.post(
    "/children/{cid}/events",
    status_code=201,
    response_model=SubmitEventResponse,
    responses={status: {"model": ErrorEnvelope} for status in (400, 404, 409, 422)},
)
async def submit_event(
    child: AccessibleChild,
    body: SubmitEventRequest,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    idempotency_key: str | None = Header(alias="Idempotency-Key", default=None),
) -> JSONResponse:
    if idempotency_key is None:
        raise ApiError(
            400, "idempotency_key_required", "요청을 처리할 수 없어요. 다시 시도해 주세요"
        )

    scope = {
        "parent_id": parent.parent_id,
        "method": request.method,
        "path": request.url.path,
        "key": idempotency_key,
    }
    replayed = idempotency.recall(**scope)
    if replayed is not None and isinstance(replayed, dict):
        return JSONResponse(status_code=201, content=replayed)

    # suggestion_ids 검증 (중복 제거)
    suggestion_ids = list(dict.fromkeys(body.suggestion_ids)) if body.suggestion_ids else []
    if suggestion_ids:
        for sid in suggestion_ids:
            row = await suggestion_repo.find_suggestion(
                session, child_id=child.child_id, suggestion_id=sid
            )
            if row is None:
                raise ApiError(404, "not_found", "제안을 찾을 수 없어요")
            if row.status != SuggestionStatus.APPROVED:
                raise ApiError(422, "not_approved", "채택되지 않은 제안이 포함돼 있어요")

        if await suggestion_repo.is_any_linked(session, suggestion_ids=suggestion_ids):
            raise ApiError(409, "already_confirmed", "이미 캘린더에 넣은 제안이에요")

    # Event INSERT
    created_by = EventCreatedBy.AGENT if suggestion_ids else EventCreatedBy.CAREGIVER

    event = await schedule_repo.create_event(
        session,
        child_id=child.child_id,
        title=body.event.title,
        event_type=EventType(body.event.event_type),
        starts_at=body.event.starts_at,
        ends_at=body.event.ends_at,
        all_day=body.event.all_day,
        category=EventCategory(body.event.category),
        created_by=created_by,
    )

    # EventItem INSERT
    items = []
    for item in body.items:
        ei = await schedule_repo.create_event_item(
            session, child_id=child.child_id, event_id=event.id, item_name=item.item_name
        )
        if ei:
            items.append(ei)

    # SuggestionEvent INSERT
    if suggestion_ids:
        await suggestion_repo.link_suggestions_to_event(
            session, suggestion_ids=suggestion_ids, event_id=event.id
        )

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if constraint_name(exc) == "uq_suggestion_event_suggestion_id":
            raise ApiError(409, "already_confirmed", "이미 캘린더에 넣은 제안이에요") from exc
        raise

    response_data = SubmitEventResponse(
        event=CalendarEventOut(
            id=str(event.id),
            title=event.title,
            event_type=str(event.event_type),
            starts_at=event.starts_at.isoformat(),
            ends_at=event.ends_at.isoformat() if event.ends_at else None,
            all_day=event.all_day,
            category=str(event.category),
            created_by=str(event.created_by),
            source_notice_id=str(event.source_notice_id) if event.source_notice_id else None,
            source_refs=event.source_refs or [],
            items=[
                EventItemOut(
                    item_id=str(ei.item_id),
                    item_name=ei.item_name,
                    is_prepared=ei.is_prepared,
                )
                for ei in items
            ],
        )
    )

    response_dict = response_data.model_dump(mode="json")
    idempotency.remember(**scope, replay=response_dict)
    return JSONResponse(status_code=201, content=response_dict)


# ── 추천 생성 (J — #284, agent 연결 전 stub) ─────────────────────────────


@router.post(
    "/children/{cid}/suggestions",
    status_code=200,
    responses={403: {"model": ErrorEnvelope}},
)
async def create_suggestions(
    child: AccessibleChild,
    body: SuggestionsRequest,
    session: SessionDep,
) -> JSONResponse:
    # TODO(#284): Agent 진입점 연결 — 이시하님 후속
    return JSONResponse(
        status_code=501,
        content={"error": {"code": "not_implemented", "message": "Agent 연결 전 (#284)"}},
    )


@router.post(
    "/children/{cid}/answers",
    status_code=200,
)
async def answer_question(
    child: AccessibleChild,
    body: AnswerRequest,
    session: SessionDep,
) -> JSONResponse:
    # TODO(#284): Agent 진입점 연결 — 이시하님 후속
    return JSONResponse(
        status_code=501,
        content={"error": {"code": "not_implemented", "message": "Agent 연결 전 (#284)"}},
    )


# ── 추천 목록 (I — #284) ─────────────────────────────────────────────────


@router.get(
    "/children/{cid}/suggestions",
    status_code=200,
)
async def list_suggestions(
    child: AccessibleChild,
    session: SessionDep,
    status: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> SuggestionListResponse:
    status_enum = SuggestionStatus(status) if status else None
    rows, next_cursor = await suggestion_repo.list_suggestions(
        session,
        child_id=child.child_id,
        status=status_enum,
        kind=SuggestionKind.PERSONALIZED,
        cursor=cursor,
        limit=limit,
    )
    evidence_map = await suggestion_repo.list_evidence_batch(
        session, suggestion_ids=[s.id for s in rows]
    )
    return SuggestionListResponse(
        items=[_suggestion_out(s, evidence_map.get(s.id)) for s in rows],
        next_cursor=next_cursor,
    )


# ── 추천 평가 (I — #284) ─────────────────────────────────────────────────


@router.patch(
    "/children/{cid}/suggestions/{sid}/feedback",
    status_code=200,
    responses={404: {"model": ErrorEnvelope}},
)
async def set_suggestion_feedback(
    child: AccessibleChild,
    sid: _uuid.UUID,
    body: SuggestionFeedbackRequest,
    session: SessionDep,
) -> SuggestionFeedbackResponse:
    updated = await suggestion_repo.set_feedback(
        session,
        child_id=child.child_id,
        suggestion_id=sid,
        feedback=SuggestionFeedback(body.feedback),
    )
    if updated is None:
        raise ApiError(404, "not_found", "제안을 찾을 수 없어요")

    await session.commit()
    evidence_rows = await suggestion_repo.list_evidence(session, suggestion_id=sid)
    return SuggestionFeedbackResponse(
        suggestion=_suggestion_out(updated, evidence_rows),
    )


# ── 일정 수정 (I — #284, 승인 게이트 ㉠) ─────────────────────────────────


@router.patch(
    "/children/{cid}/events/{eid}",
    status_code=200,
    response_model=SubmitEventResponse,
    responses={status: {"model": ErrorEnvelope} for status in (400, 404, 409, 422)},
)
async def update_event(
    child: AccessibleChild,
    eid: _uuid.UUID,
    body: UpdateEventRequest,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    idempotency_key: str | None = Header(alias="Idempotency-Key", default=None),
) -> JSONResponse:
    if idempotency_key is None:
        raise ApiError(
            400, "idempotency_key_required", "요청을 처리할 수 없어요. 다시 시도해 주세요"
        )

    scope = {
        "parent_id": parent.parent_id,
        "method": request.method,
        "path": request.url.path,
        "key": idempotency_key,
    }
    replayed = idempotency.recall(**scope)
    if replayed is not None and isinstance(replayed, dict):
        return JSONResponse(status_code=200, content=replayed)

    event = await schedule_repo.update_event(
        session,
        child_id=child.child_id,
        event_id=eid,
        title=body.event.title,
        event_type=EventType(body.event.event_type),
        starts_at=body.event.starts_at,
        ends_at=body.event.ends_at,
        all_day=body.event.all_day,
        category=EventCategory(body.event.category),
    )
    if event is None:
        raise ApiError(404, "not_found", "일정을 찾을 수 없어요")

    items = await schedule_repo.replace_event_items(
        session,
        event_id=eid,
        items=[i.model_dump() for i in body.items],
    )

    await session.commit()

    response_data = SubmitEventResponse(
        event=CalendarEventOut(
            id=str(event.id),
            title=event.title,
            event_type=str(event.event_type),
            starts_at=event.starts_at.isoformat(),
            ends_at=event.ends_at.isoformat() if event.ends_at else None,
            all_day=event.all_day,
            category=str(event.category),
            created_by=str(event.created_by),
            source_notice_id=str(event.source_notice_id) if event.source_notice_id else None,
            source_refs=event.source_refs or [],
            items=[
                EventItemOut(
                    item_id=str(ei.item_id),
                    item_name=ei.item_name,
                    is_prepared=ei.is_prepared,
                )
                for ei in items
            ],
        )
    )

    response_dict = response_data.model_dump(mode="json")
    idempotency.remember(**scope, replay=response_dict)
    return JSONResponse(status_code=200, content=response_dict)
