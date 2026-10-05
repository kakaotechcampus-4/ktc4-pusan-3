"""제안 채택·일정 초안·일정 제출 — #236.

채택은 되돌릴 수 있어 승인 게이트에 해당하지 않는다.
일정 제출만 승인 게이트 ㉠ — Idempotency-Key 필수.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.api import idempotency
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.suggestions import (
    ApproveSuggestionsRequest,
    ApproveSuggestionsResponse,
    CalendarEventOut,
    CreateEventDraftsRequest,
    CreateEventDraftsResponse,
    EventBodyOut,
    EventDraftOut,
    EventItemOut,
    SubmitEventRequest,
    SubmitEventResponse,
    SuggestionOut,
)
from app.domains.schedule import repository as schedule_repo
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType
from app.domains.suggestion import repository as suggestion_repo
from app.domains.suggestion.models import SuggestionStatus

router = APIRouter()


def _suggestion_out(s) -> SuggestionOut:
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
    if not body.suggestion_ids:
        raise ApiError(400, "validation_failed", "suggestion_ids가 비어 있어요")

    # 먼저 존재 + 소유 확인
    found = []
    for sid in body.suggestion_ids:
        row = await suggestion_repo.find_suggestion(
            session, child_id=child.child_id, suggestion_id=sid
        )
        if row is None:
            raise ApiError(404, "not_found", "제안을 찾을 수 없어요")
        if row.status != SuggestionStatus.DRAFT:
            raise ApiError(409, "not_draft", "이미 처리된 제안이 포함돼 있어요")
        if row.expires_at <= datetime.now(timezone.utc):
            raise ApiError(409, "suggestion_expired", "만료된 제안이 포함돼 있어요")
        found.append(row)

    approved = await suggestion_repo.approve_suggestions(
        session, child_id=child.child_id, suggestion_ids=body.suggestion_ids
    )
    if not approved:
        raise ApiError(409, "not_draft", "이미 처리된 제안이 포함돼 있어요")

    await session.commit()
    return ApproveSuggestionsResponse(suggestions=[_suggestion_out(s) for s in approved])


# ── 일정 초안 ─────────────────────────────────────────────────────────────

_AGENT_CATEGORY = {"activity": "activity", "health": "health"}
_counter = 0


def _next_draft_id() -> str:
    global _counter
    _counter += 1
    return f"d{_counter}"


@router.post(
    "/children/{cid}/suggestions/event-drafts",
    status_code=200,
    responses={status: {"model": ErrorEnvelope} for status in (404, 422)},
)
async def create_event_drafts(
    child: AccessibleChild,
    body: CreateEventDraftsRequest,
    session: SessionDep,
) -> CreateEventDraftsResponse:
    if not body.suggestion_ids:
        raise ApiError(400, "validation_failed", "suggestion_ids가 비어 있어요")

    suggestions = []
    for sid in body.suggestion_ids:
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
    responses={status: {"model": ErrorEnvelope} for status in (400, 409, 422)},
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
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key 헤더가 필요해요")

    scope = {
        "parent_id": parent.parent_id,
        "method": request.method,
        "path": request.url.path,
        "key": idempotency_key,
    }
    replayed = idempotency.recall(**scope)
    if replayed is not None and isinstance(replayed, dict):
        return JSONResponse(status_code=201, content=replayed)

    # suggestion_ids 검증
    suggestion_ids = body.suggestion_ids or []
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
    starts_at = datetime.fromisoformat(body.event.starts_at)
    ends_at = datetime.fromisoformat(body.event.ends_at) if body.event.ends_at else None
    created_by = EventCreatedBy.AGENT if suggestion_ids else EventCreatedBy.CAREGIVER

    event = await schedule_repo.create_event(
        session,
        child_id=child.child_id,
        title=body.event.title,
        event_type=EventType(body.event.event_type),
        starts_at=starts_at,
        ends_at=ends_at,
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
    idempotency.remember(**scope, run_id=response_dict)
    return JSONResponse(status_code=201, content=response_dict)
