"""제안 채택·일정 초안·일정 제출·추천 생성·목록·평가·일정 수정 — #236 · #284.

채택은 되돌릴 수 있어 승인 게이트에 해당하지 않는다.
일정 제출·수정만 승인 게이트 ㉠ — Idempotency-Key 필수.
"""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.agents import entrypoint
from app.agents.entrypoint import Readout
from app.api import agent_ports, idempotency, quota
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope, constraint_name
from app.api.runs import suggestion_retry
from app.api.v1.list_query import PageQueryDep
from app.api.v1.reminder_check import check_reminder, reminder_out
from app.api.v1.schemas.common import Ref
from app.api.v1.schemas.suggestions import (
    AnswerRequest,
    AnswerResponse,
    ApproveSuggestionsRequest,
    ApproveSuggestionsResponse,
    CalendarEventOut,
    CreateEventDraftsRequest,
    CreateEventDraftsResponse,
    DocEvidenceOut,
    EventBodyOut,
    EventDraftOut,
    EventItemOut,
    EvidenceOut,
    GeneralSuggestionOut,
    GroupNoticeOut,
    GuardOut,
    PrecheckOut,
    ScarcityOut,
    ScarcityQuestionOut,
    SubmitEventRequest,
    SubmitEventResponse,
    SuggestionFeedbackRequest,
    SuggestionFeedbackResponse,
    SuggestionGroupOut,
    SuggestionListResponse,
    SuggestionOut,
    SuggestionsRequest,
    SuggestionsResponse,
    UpdateEventRequest,
)
from app.core import deeplinks
from app.core.config import settings
from app.domains.consent.repository import child_health_granted
from app.domains.food import repository as food_repo
from app.domains.safety.labels import list_safety_labels
from app.domains.schedule import repository as schedule_repo
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType
from app.domains.suggestion import repository as suggestion_repo
from app.domains.suggestion.draft_items import draft_items
from app.domains.suggestion.evidence_view import EvidenceView, describe_evidence
from app.domains.suggestion.models import SuggestionFeedback, SuggestionKind, SuggestionStatus
from app.rules.book_evidence import BOOK_EVIDENCE_KIND
from app.rules.home_prompts import prompt_request
from app.rules.menu_evidence import MENU_EVIDENCE_KIND

router = APIRouter(tags=["suggestions"])

KST = ZoneInfo("Asia/Seoul")

# 일정 초안을 한 끼로 묶는 Agent. 05 의 merges_into_one 과 create_event_drafts 가 같이 본다 —
# 정하는 곳이 둘이면 화면은 "한 건으로 묶여요" 라고 말했는데 초안이 세 건 나올 수 있다
_MERGED_AGENTS = frozenset({"food"})

# 확인 문구를 내는 Agent — entrypoint.allergy_cautions 와 같은 목록
_CAUTION_AGENTS = frozenset({"activity"})

_GUARD_CODES = {"blocked": "safety_unknown", "unsupported": "unsupported"}
# 안전이 아닌 이유로 닫힌 Growth(closed.* · doc.no_row). 화면은 `safety_unknown` 일 때만 알레르기
# 등록 안내를 그린다 — 다른 이유에 그 코드를 쓰면 알레르기 안내가 잘못 뜬다
_CLOSED_CODE = "closed"
# guard 별 딥링크 — 알레르기를 몰라 막힌 것만 갈 곳이 있다. 04 safety_record 안내와 같은 곳이다.
# 준비 중 · 지원하지 않는 주제는 어디서 무엇을 넣어도 풀리지 않아 null (#625)
_GUARD_DEEPLINKS = {"safety_unknown": deeplinks.PROFILE}
_NOT_READY = "아직 준비 중인 주제예요."
_UNKNOWN_NOTE = "알레르기 기록에 없음"

# 하루 한도 문구. 05 는 "적기" 가 아니라 "준비하기" 라 /inputs 와 문구를 따로 둔다 (코드는 같다)
_DAILY_LIMIT = "오늘은 더 준비할 수 없어요. 내일 다시 골라 주세요."


def _suggestion_out(s, views: Sequence[EvidenceView] = ()) -> SuggestionOut:
    evidence = [
        EvidenceOut(
            ref=Ref(kind=v.source_kind, id=str(v.source_id)),
            label=v.label,
            observed_to=v.observed_to.isoformat(),
            confidence_source=v.confidence_source,
            is_stale=v.is_stale,
            note=v.note,
        )
        for v in views
    ]
    return SuggestionOut(
        id=str(s.id),
        child_id=str(s.child_id),
        agent=str(s.agent),
        kind=str(s.kind),
        content=s.content,
        place_notice=s.place_notice,
        warnings=list(s.warnings),
        reason=s.reason,
        status=str(s.status),
        expires_at=s.expires_at.isoformat(),
        feedback=str(s.feedback) if s.feedback else None,
        source_refs=[e.ref for e in evidence],
        evidence=evidence,
    )


# ── 채택 ──────────────────────────────────────────────────────────────────


@router.post(
    "/children/{cid}/suggestions/approve",
    summary="추천 채택",
    description=(
        "선택한 추천을 채택(draft → approved)한다. 이미 채택된 것은 멱등 처리. 만료된 추천은 409."
    ),
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
    summary="일정 초안 생성",
    description=(
        "채택한 추천으로 일정 초안을 만든다. food는 하나로 묶이고 나머지는 별도 초안. "
        "준비물은 그 추천의 items — food 묶음에서 같은 재료는 한 번. "
        "DB에 저장하지 않는다 — 응답으로만 반환."
    ),
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

    food = [s for s in suggestions if str(s.agent) in _MERGED_AGENTS]
    rest = [s for s in suggestions if str(s.agent) not in _MERGED_AGENTS]

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
                # 묶은 food 추천의 재료를 한 번씩 (#315). 화면은 받은 items 를 거르지 않는다
                items=draft_items(s.items for s in food),
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
                items=draft_items([s.items]),
                suggestion_ids=[str(s.id)],
            )
        )

    return CreateEventDraftsResponse(drafts=drafts)


# ── 일정 제출 (승인 게이트 ㉠) ────────────────────────────────────────────


@router.post(
    "/children/{cid}/events",
    summary="일정 등록 (승인 게이트 ㉠)",
    description=(
        "초안에 시간을 채워 캘린더에 확정한다. Idempotency-Key 필수. "
        "추천에서 왔으면 suggestion ↔ event 연결."
    ),
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
    if not idempotency_key:
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

    if body.reminder is not None:
        check_reminder(
            body.reminder.lead, starts_at=body.event.starts_at, all_day=body.event.all_day
        )

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

    # 알림을 함께 걸면 연결부터 잡는다 — 일정을 넣으면 아이 행에 외래 키 잠금이 걸리는데, 그 뒤에
    # 보호자 → 아이 순으로 잠그면 같은 순서로 잡는 탈퇴(`withdraw_parent`)와 엇갈린다 (#603 리뷰)
    if body.reminder is not None and not await schedule_repo.lock_link(
        session, child_id=child.child_id, parent_id=parent.parent_id
    ):
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")

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

    # 알림은 제출한 보호자에게만 건다 — 다른 보호자는 캘린더에서 각자 건다 (#594)
    reminder = None
    if body.reminder is not None:
        reminder = await schedule_repo.put_reminder(
            session,
            child_id=child.child_id,
            event_id=event.id,
            parent_id=parent.parent_id,
            lead=body.reminder.lead,
        )
        if reminder is None:  # 위에서 연결을 잡아 두어 오지 않는다. 계약대로 막아만 둔다
            raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")

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
                    prepared_at=ei.prepared_at.isoformat() if ei.prepared_at else None,
                )
                for ei in items
            ],
            reminder=reminder_out(reminder),
        )
    )

    response_dict = response_data.model_dump(mode="json")
    idempotency.remember(**scope, replay=response_dict)
    return JSONResponse(status_code=201, content=response_dict)


# ── 추천 받기 (J — #284 · #249) ──────────────────────────────────────────


@router.post(
    "/children/{cid}/suggestions",
    summary="추천 받기",
    description=(
        "Agent 를 돌려 Agent 마다 추천 0~3개를 낸다. run_id 가 있으면 그 run 의 결과를 돌려주고 "
        "결과를 못 낸 Agent 만 다시 만든다. 20초를 넘긴 Agent 는 빠지고 나머지를 돌려준다 (NF-06)."
    ),
    status_code=200,
    response_model=SuggestionsResponse,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 429)},
)
async def create_suggestions(
    child: AccessibleChild,
    body: SuggestionsRequest,
    parent: CurrentParent,
    session: SessionDep,
) -> SuggestionsResponse:
    agents = list(dict.fromkeys(body.agents))
    # prompt_key 검증 — 표에 없는 조합이면 400 (#286)
    if body.prompt_key is not None and any(
        prompt_request(agent, body.prompt_key) is None for agent in agents
    ):
        raise ApiError(400, "validation_failed", "허용되지 않은 추천 요청이에요")

    now = datetime.now(timezone.utc)
    owner = {"parent_id": parent.parent_id, "child_id": child.child_id}
    # run 이 없으면 처음 요청이다 — 추천 라벨이 하나인 Agent 만 무엇을 돌릴지 안다.
    # Growth 는 라벨이 셋이라 처음 요청은 not_ready 고, run 이 있으면 그 run 의 맥락으로 돈다
    startable = (
        entrypoint.SUGGESTION_AGENTS if body.run_id is not None else entrypoint.FIRST_REQUEST_AGENTS
    )
    ready = [a for a in agents if a in startable]
    guards = [_guard_out("not_ready", a, _NOT_READY) for a in agents if a not in startable]

    # 판단 순서 (#249) — run_id 가 없으면 전부 처음 요청이다
    run_id = body.run_id
    replay: dict[str, list] = {}  # ① 그 run 의 추천
    notices: dict[str, GroupNoticeOut] = {}  # ② 0개 기록 · ④ 이어서 할 수 없음
    claimed: dict[str, suggestion_retry.RetryTask] = {}  # ③ 다시 만들 맥락
    settled: set[str] = set()  # 다시 만들어 결과를 낸 Agent — 그 맥락은 지운다
    run = entrypoint.SuggestRun(outcomes=(), failed=())
    # 🚨 맥락을 꺼낸(claim) 뒤 무엇이 터져도 "사용 중" 을 푼다. 안 풀면 15분 동안 다시 시도가
    #    BUSY 로 떨어져 실패 카드만 뜬다 (#433 리뷰 ②)
    try:
        if run_id is None:
            fresh = [
                entrypoint.SuggestTask(
                    a,
                    entrypoint.recommendation_task_type(a),
                    (prompt_request(a, body.prompt_key),) if body.prompt_key else (),
                )
                for a in ready
            ]
        else:
            fresh = []
            for a in ready:
                rows = await suggestion_repo.list_run_suggestions(
                    session, child_id=child.child_id, run_id=run_id, agent=a, now=now
                )
                if rows:
                    replay[a] = rows
                    continue
                message = suggestion_retry.empty_message(run_id=run_id, agent=a, **owner)
                if message is not None:
                    notices[a] = GroupNoticeOut(code="empty", message=message)
                    continue
                recorded = suggestion_retry.guard_of(run_id=run_id, agent=a, **owner)
                if recorded is not None:
                    # 다시 만들었더니 닫혔던 Agent — 같은 guard 를 다시 낸다 (#433 리뷰 ⑧)
                    code, message = recorded
                    guards.append(_guard_out(code, a, message))
                    continue
                task = suggestion_retry.claim(run_id=run_id, agent=a, **owner)
                if task == suggestion_retry.BUSY:
                    continue  # 다른 요청이 다시 만드는 중 — 둘을 만들지 않는다. 실패와 같다
                if task is None:
                    notices[a] = _notice_out(entrypoint.cannot_resume_notice())
                else:
                    claimed[a] = task

        to_run = fresh + [
            entrypoint.SuggestTask(a, t.task_type, t.request_texts) for a, t in claimed.items()
        ]
        health_consent = await child_health_granted(session, child_id=child.child_id)
        if to_run:
            # Agent 를 실제로 새로 돌릴 때만 하루 한도를 쓴다 — 재생은 모델을 부르지 않는다 (D8)
            if not quota.consume(
                parent_id=parent.parent_id,
                today=quota.today_kst(),
                limit=settings.INPUT_DAILY_LIMIT,
            ):
                if not (replay or notices or guards):
                    raise ApiError(429, "daily_input_limit", _DAILY_LIMIT)
                # 같은 응답에 보여 줄 다른 결과가 있으면 막지 않는다 — 돌릴 Agent 묶음에 안내만
                # 단다. 꺼낸 맥락은 finally 가 푼다 (#433 리뷰 ④)
                for task in to_run:
                    notices[task.agent] = GroupNoticeOut(code="daily_limit", message=_DAILY_LIMIT)
                to_run = []
        if to_run:
            # 요청 세션의 트랜잭션을 닫아 커넥션을 풀에 돌려준다. Agent 가 도는 동안(최대 20초)
            # 쥐고 있으면 포트가 여는 짧은 세션과 겹쳐 동시 요청에서 풀이 마른다 (#433 리뷰 ⑨).
            # 여기까지 읽기만 했고, expire_on_commit=False 라 읽은 행도 그대로 쓸 수 있다
            await session.commit()
            sent_at = next(iter(claimed.values())).sent_at if claimed else None
            run = await entrypoint.suggest(
                child_id=child.child_id,
                tasks=to_run,
                request_id=str(_uuid.uuid4()),
                save_run_id=run_id if claimed else None,
                sent_at=sent_at,
                save_suggestions=agent_ports.suggestion_saver(child.child_id),
                birth_date=child.birth_date,
                child_health_granted=health_consent,
                domain_store=agent_ports.domain_store_opener(child.child_id),
                **agent_ports.food_stores(),
                **agent_ports.safety_readers(),
                **agent_ports.growth_book_ports(),
                growth_log=agent_ports.growth_log_reader(),
                affinity_reader=agent_ports.affinity_reader(),
            )
            settled = {o.agent for o in run.outcomes}
    finally:
        for a in claimed:
            if a in settled:
                suggestion_retry.drop(run_id=run_id, agent=a)
            else:
                suggestion_retry.release(run_id=run_id, agent=a)

    # 다시 만든 결과를 지운 맥락 자리에 남긴다 — 다음 요청이 같은 모양을 재생한다 (24시간).
    # 지운 뒤에 넣어야 한다 — 같은 (run_id, agent) 칸이라 순서를 바꾸면 기록이 지워진다
    for o in run.outcomes:
        if o.agent not in claimed:
            continue
        empty = entrypoint.empty_notice(o.readouts)
        code = _guard_code(o)
        if empty is not None:
            suggestion_retry.put_empty(run_id=run_id, agent=o.agent, message=empty.body, **owner)
        elif code is not None:
            suggestion_retry.put_guard(
                run_id=run_id, agent=o.agent, code=code, message=_guard_message(o), **owner
            )

    return await _suggestions_response(
        session,
        child_id=child.child_id,
        agents=ready,
        replay=replay,
        notices=notices,
        run=run,
        guards=guards,
        health_consent=health_consent,
        now=now,
    )


async def _suggestions_response(
    session,
    *,
    child_id,
    agents: list[str],
    replay: dict[str, list],
    notices: dict[str, GroupNoticeOut],
    run: entrypoint.SuggestRun,
    guards: list[GuardOut],
    health_consent: bool,
    now: datetime,
) -> SuggestionsResponse:
    today = now.astimezone(KST).date()
    outcomes = {o.agent: o for o in run.outcomes}
    for agent, o in outcomes.items():
        code = _guard_code(o)
        if code is not None:
            guards.append(_guard_out(code, agent, _guard_message(o)))

    # 이번에 보일 추천 행 — 재생 행 + 방금 저장한 행. 묶음 순서는 요청한 Agent 순서다
    fresh_ids = [_uuid.UUID(i) for o in run.outcomes for i in o.suggestion_ids]
    fresh_rows = await suggestion_repo.list_suggestions_by_ids(
        session, child_id=child_id, ids=fresh_ids
    )
    rows = [r for a in agents for r in replay.get(a, [])] + fresh_rows
    ids = [r.id for r in rows]
    child_ev = await suggestion_repo.list_evidence_batch(session, suggestion_ids=ids)
    views = await describe_evidence(
        session, child_id=child_id, rows=[e for es in child_ev.values() for e in es], today=today
    )
    general_ev = await suggestion_repo.list_general_evidence_batch(session, suggestion_ids=ids)
    cards = entrypoint.describe_docs(
        (e.source_kind, e.source_id) for es in general_ev.values() for e in es
    )
    # Food 일반 추천의 메뉴 근거 이름(#442). 메뉴 근거가 있을 때만 한 번 읽는다
    has_menu = any(e.source_kind == MENU_EVIDENCE_KIND for es in general_ev.values() for e in es)
    menus = await food_repo.resolved_recipe_names(session) if has_menu else {}
    safety_rows: list[entrypoint.SafetyLabel] | None = None

    async def safety() -> list[entrypoint.SafetyLabel]:
        # 처음 필요할 때 한 번 읽는다 — allergens 가 든 추천이 없으면 읽지 않는다 (#433 리뷰 ⑬)
        nonlocal safety_rows
        if safety_rows is None:
            labels = await list_safety_labels(session, child_id=child_id)
            safety_rows = [entrypoint.SafetyLabel(*r) for r in labels]
        return safety_rows

    record_counts: dict[str, int] = {}

    async def records(agent: str) -> int:
        # Agent 마다 한 번 센다 — 일반 카드의 "기록 N건" 은 그 도메인 수다 (#433 리뷰 ⑥)
        if agent not in record_counts:
            record_counts[agent] = await suggestion_repo.count_child_records(
                session, child_id=child_id, kinds=agent_ports.RECORD_KINDS.get(agent, ())
            )
        return record_counts[agent]

    personal: list[SuggestionOut] = []
    general: list[GeneralSuggestionOut] = []
    prechecks: list[PrecheckOut] = []
    shown: dict[str, int] = dict.fromkeys(agents, 0)
    for row in rows:
        if row.allergens:
            if not health_consent:
                continue  # 동의 없이 묻지 않는다 — 묻는 순간 건강정보 수집이다 (D10)
            check = entrypoint.check_allergens(row.allergens, await safety())
            if check.blocked:
                continue
        else:
            check = entrypoint.AllergenCheck(blocked=(), unknown=())
        if row.kind == SuggestionKind.PERSONALIZED:
            seen = views.get(row.id, [])
            if not seen:
                continue  # 보이는 근거가 없는 개인화는 내보내지 않는다 (루트 §2)
            personal.append(_suggestion_out(row, seen))
        else:
            basis = [
                out
                for e in general_ev.get(row.id, [])
                if (out := _general_evidence_out(e, cards, menus, book_title=row.content))
                is not None
            ]
            if not basis:
                # 일반 추천의 근거는 문서 행(Food 는 메뉴 행도)이다 — 검수가 풀린 문서 · 후보 풀에서
                # 빠진 메뉴만 남았으면 보여 줄 근거가 없어서 내지 않는다
                continue
            general.append(_general_out(row, basis, await records(str(row.agent))))
        prechecks.extend(
            PrecheckOut(
                code="unknown_ingredient",
                item=item,
                note=_UNKNOWN_NOTE,
                suggestion_id=str(row.id),
            )
            for item in check.unknown
        )
        shown[str(row.agent)] += 1

    blocked = {a for g in guards for a in g.blocked_agents}
    groups: list[SuggestionGroupOut] = []
    for agent in agents:
        o = outcomes.get(agent)
        if agent in blocked or (o is None and agent not in replay and agent not in notices):
            continue  # 닫힘은 guard 로, 실패는 묶음 없음으로
        # 식품 아닌 알레르기 확인 문구 — 04 와 같은 말을 코드로 다시 만든다.
        # 동의가 없으면 읽지 않는다 (#433 리뷰 ③)
        cautions = (
            list(entrypoint.allergy_cautions(agent, await safety()))
            if health_consent and shown[agent] > 0 and agent in _CAUTION_AGENTS
            else []
        )
        groups.append(
            SuggestionGroupOut(
                agent=agent,
                merges_into_one=agent in _MERGED_AGENTS,
                notice=notices.get(agent) or _count_notice(shown[agent], o),
                cautions=cautions,
            )
        )

    scarcity = None
    general_agents = {str(item.agent) for item in general}
    if general_agents:
        count = sum([await records(a) for a in sorted(general_agents)])
        # 질문을 낸 Agent 로 id 를 단다 — 답(#246)이 그 Agent 로 간다 (#433 리뷰 ⑤)
        asked = next(
            (
                (a, o.question)
                for a in agents
                if a in general_agents and (o := outcomes.get(a)) and o.question
            ),
            None,
        )
        scarcity = ScarcityOut(
            count=count,
            question=None
            if asked is None
            else ScarcityQuestionOut(id=f"{asked[0]}:q", text=asked[1]),
        )

    return SuggestionsResponse(
        suggestions=personal,
        groups=groups,
        general=general,
        looked_at="",
        guards=guards,
        scarcity=scarcity,
        prechecks=prechecks,
    )


def _count_notice(shown: int, outcome: entrypoint.SuggestOutcome | None) -> GroupNoticeOut | None:
    """보이는 개수로 다시 만든다 — 알레르기 · 근거로 빠진 것이 있으면 Agent 의 안내와 다르다.

    0개면 그 Agent 의 0개 문구(Food 의 pool.empty)를 쓴다. 한 run 에 행이 상한을 넘게
    쌓였어도(다시 만들기가 겹친 경우) 다 채운 것으로 본다 — 500 으로 다른 Agent 까지 가리지 않는다
    (#433 리뷰 ⑦).
    """
    if shown == 0 and outcome is not None:
        own = entrypoint.empty_notice(outcome.readouts)
        if own is not None:
            return _notice_out(own)
    notice = entrypoint.count_notice(min(shown, entrypoint.MAX_SUGGESTIONS))
    return None if notice is None else _notice_out(notice)


def _notice_out(readout: Readout) -> GroupNoticeOut:
    return GroupNoticeOut(code=readout.code, message=readout.body)


def _guard_code(outcome: entrypoint.SuggestOutcome) -> str | None:
    """닫힌 Agent 를 guard 로 알리는 code. 정상 결과면 None.

    Food · Activity 의 `blocked` 는 안전 조회 실패뿐이라 상태만 본다. Growth 는 월령 · 동의 · 포트 ·
    문서 행 없음으로도 닫혀서 코드 문구의 종류가 `blocked`(안전)일 때만 `safety_unknown` 이다.
    """
    code = _GUARD_CODES.get(outcome.status)
    if code == "safety_unknown" and outcome.agent == "growth":
        if not any(r.kind == "blocked" for r in outcome.readouts):
            return _CLOSED_CODE
    return code


def _guard_out(code: str, agent: str, message: str) -> GuardOut:
    """막힌 Agent 하나의 guard. 다시 만든 뒤 재생하는 guard 도 같은 딥링크를 싣는다."""
    return GuardOut(
        code=code, blocked_agents=[agent], message=message, deeplink=_GUARD_DEEPLINKS.get(code)
    )


def _guard_message(outcome: entrypoint.SuggestOutcome) -> str:
    """닫힌 Agent 의 코드 문구 — readout 본문이다. 없으면 기본 문구."""
    return next((r.body for r in outcome.readouts), "추천을 만들지 않았어요.")


# Food 일반 추천의 메뉴 근거 카드 — 고른 메뉴가 온 공개 레시피의 출처다(#442). 화면은 문서 카드와
# 같이 "참고 · 메뉴 이름 (기관, 출처)" 로 그린다
MENU_SOURCE_ORG = "농식품정보원"
MENU_SOURCE_TITLE = "농림축산식품 공공데이터포털 레시피 기본 · 재료정보"
MENU_SOURCE_URL = "https://data.mafra.go.kr"

# Growth 도서 추천의 근거 카드 — 그 책을 찾은 공개 서지 정보의 출처다. 화면은 문서 카드와 같이
# "참고 · 제목(저자) (기관, 출처)" 로 그린다. 책 정보 표가 없어서 제목은 그 추천의 "제목(저자)" 다
BOOK_SOURCE_ORG = "국립중앙도서관"
BOOK_SOURCE_TITLE = "도서관 정보나루"
BOOK_SOURCE_URL = "https://data4library.kr"


def _general_evidence_out(
    e, cards: dict, menus: dict[_uuid.UUID, str], *, book_title: str
) -> DocEvidenceOut | None:
    """일반 추천 근거 한 행 → 화면 카드. 문서 행은 검수된 시드에서, 메뉴 행은 후보 풀(검수된
    레시피)에서 찾는다. 책 행(Growth 도서)은 그 추천의 "제목(저자)"(`book_title`)로 그린다 — 근거가
    그 책 자체라 따로 찾을 곳이 없다. 못 찾으면 None — 그 근거는 보여 주지 않는다."""
    ref = Ref(kind=e.source_kind, id=str(e.source_id))
    if e.source_kind == MENU_EVIDENCE_KIND:
        name = menus.get(e.source_id)
        if name is None:
            return None
        return DocEvidenceOut(
            ref=ref,
            title=name,
            source_org=MENU_SOURCE_ORG,
            source_title=MENU_SOURCE_TITLE,
            source_url=MENU_SOURCE_URL,
            note=e.note,
        )
    if e.source_kind == BOOK_EVIDENCE_KIND:
        return DocEvidenceOut(
            ref=ref,
            title=book_title,
            source_org=BOOK_SOURCE_ORG,
            source_title=BOOK_SOURCE_TITLE,
            source_url=BOOK_SOURCE_URL,
            note=e.note,
        )
    card = cards.get((e.source_kind, e.source_id))
    if card is None:
        return None
    return DocEvidenceOut(
        ref=ref,
        title=card.title,
        source_org=card.source_org,
        source_title=card.source_title,
        source_url=card.source_url,
        note=e.note,
    )


def _general_out(row, docs: list[DocEvidenceOut], record_count: int) -> GeneralSuggestionOut:
    return GeneralSuggestionOut(
        id=str(row.id),
        child_id=str(row.child_id),
        agent=str(row.agent),
        content=row.content,
        place_notice=row.place_notice,
        warnings=list(row.warnings),
        basis=row.reason or "",
        status=str(row.status),
        expires_at=row.expires_at.isoformat(),
        record_count=record_count,
        evidence=docs,
    )


@router.post(
    "/children/{cid}/answers",
    summary="되묻기 답변 (Agent 연결 전 stub)",
    description="근거 부족 시 보호자 답변을 관찰로 저장한다. 현재 501 stub — #249에서 해제.",
    status_code=200,
    response_model=AnswerResponse,
    responses={501: {"model": ErrorEnvelope}},
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
    summary="지난 추천 목록",
    description=(
        "개인화 · 일반 추천을 함께 `page` · `size` 쪽으로 나눠 반환하고 kind 로 구분한다. "
        "필터 없으면 draft 제외 (07 화면용). evidence 는 개인화만 채워진다. "
        "`rated=false` 는 피드백을 아직 안 남긴 것, `rated=true` 는 남긴 것만. "
        "`total` 은 필터를 건 뒤의 전체 건수다."
    ),
    status_code=200,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403)},
)
async def list_suggestions(
    child: AccessibleChild,
    session: SessionDep,
    query: PageQueryDep,
    status: str | None = Query(default=None),
    rated: bool | None = Query(
        default=None,
        description="안 보내면 전부, false 면 피드백 미평가만, true 면 피드백을 남긴 것만",
    ),
) -> SuggestionListResponse:
    try:
        status_enum = SuggestionStatus(status) if status else None
    except ValueError:
        raise ApiError(400, "validation_failed", "유효하지 않은 status 값이에요")
    rows, total = await suggestion_repo.list_suggestions(
        session,
        child_id=child.child_id,
        status=status_enum,
        rated=rated,
        offset=query.offset,
        limit=query.size,
    )
    evidence_map = await suggestion_repo.list_evidence_batch(
        session, suggestion_ids=[s.id for s in rows]
    )
    views = await describe_evidence(
        session,
        child_id=child.child_id,
        rows=[row for found in evidence_map.values() for row in found],
        today=quota.today_kst(),
    )
    return SuggestionListResponse(
        items=[_suggestion_out(s, views.get(s.id, ())) for s in rows],
        total=total,
        page=query.page,
        size=query.size,
        total_pages=query.total_pages(total),
    )


# ── 추천 평가 (I — #284) ─────────────────────────────────────────────────


@router.patch(
    "/children/{cid}/suggestions/{sid}/feedback",
    summary="추천 평가",
    description=(
        "채택/거절된 추천에 피드백을 남긴다 "
        "(child_liked·child_disliked·not_acted). 기억은 바꾸지 않는다."
    ),
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
    views = await describe_evidence(
        session, child_id=child.child_id, rows=evidence_rows, today=quota.today_kst()
    )
    return SuggestionFeedbackResponse(
        suggestion=_suggestion_out(updated, views.get(sid, ())),
    )


# ── 일정 수정 (I — #284, 승인 게이트 ㉠) ─────────────────────────────────


@router.patch(
    "/children/{cid}/events/{eid}",
    summary="일정 수정 (승인 게이트 ㉠)",
    description=(
        "일정 본문과 준비물을 수정한다. items는 전체 교체 "
        "(기존 is_prepared 보존). Idempotency-Key 필수."
    ),
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
    if not idempotency_key:
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

    # 연결부터 잡는다 — 수정이 알림 행을 잡은 뒤에 알림을 걸며 연결을 잠그면, 연결 → 알림 순으로
    # 지우는 끊기(`unlink_parent`)와 교착이 된다. 끊긴 뒤면 일정도 고치지 않는다 (#427)
    if not await schedule_repo.lock_link(
        session, child_id=child.child_id, parent_id=parent.parent_id
    ):
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    before = await schedule_repo.find_event(
        session, child_id=child.child_id, event_id=eid, lock="update"
    )
    if before is None:
        raise ApiError(404, "not_found", "일정을 찾을 수 없어요")

    touch_reminder = "reminder" in body.model_fields_set
    if touch_reminder and body.reminder is not None:
        mine_before = (
            await schedule_repo.list_my_reminders(
                session, parent_id=parent.parent_id, event_ids=[eid]
            )
        ).get(eid)
        # 화면은 일정 폼 전체를 보내서, 일정만 고쳐도 지금 알림 값이 같이 온다. 저장된 값과 같으면
        # 알림은 안 건드린 것으로 보고 검사도 저장도 하지 않는다 (#603 리뷰)
        #   - 시작 · 종류가 그대로면: 이미 울린 알림을 검사하면 제목 수정까지 422 가 되고,
        #     저장하면 "울렸음" 이 지워져 한 번 더 울린다.
        #   - 종류만 바뀌면: 옛 종류의 값이라 검사에 걸린다. `update_event` 가 날 수를 지켜 옮긴다.
        #   - 같은 종류에서 시작만 바뀌면 검사한다 — 그 값의 울릴 시각이 지났으면 다시 고르게 한다.
        same_lead = mine_before is not None and mine_before.lead == body.reminder.lead
        kind_changed = before.all_day != body.event.all_day
        if same_lead and (kind_changed or before.starts_at == body.event.starts_at):
            touch_reminder = False
        else:
            check_reminder(
                body.reminder.lead, starts_at=body.event.starts_at, all_day=body.event.all_day
            )

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
        now=datetime.now(timezone.utc),
    )
    if event is None:  # 위에서 잡아 두어 오지 않는다
        raise ApiError(404, "not_found", "일정을 찾을 수 없어요")

    try:
        items = await schedule_repo.replace_event_items(
            session,
            child_id=child.child_id,
            event_id=eid,
            items=[i.model_dump() for i in body.items],
        )
    except ValueError:
        raise ApiError(400, "validation_failed", "유효하지 않은 item_id예요")

    if touch_reminder and body.reminder is None:
        await schedule_repo.delete_reminder(
            session, child_id=child.child_id, event_id=eid, parent_id=parent.parent_id
        )
    elif touch_reminder:
        put = await schedule_repo.put_reminder(
            session,
            child_id=child.child_id,
            event_id=eid,
            parent_id=parent.parent_id,
            lead=body.reminder.lead,
        )
        if put is None:  # 확인한 뒤 연결이 끊겼다 (#427)
            raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    mine = (
        await schedule_repo.list_my_reminders(session, parent_id=parent.parent_id, event_ids=[eid])
    ).get(eid)

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
                    prepared_at=ei.prepared_at.isoformat() if ei.prepared_at else None,
                )
                for ei in items
            ],
            reminder=reminder_out(mine),
        )
    )

    response_dict = response_data.model_dump(mode="json")
    idempotency.remember(**scope, replay=response_dict)
    return JSONResponse(status_code=200, content=response_dict)
