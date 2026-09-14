"""event / event_item 의 CRUD tool 7개.

일정은 관찰과 달리 의존성(준비물은 event 없이 존재 X)이 있음.
그래서 event_id는 항상 create_event / query_event 가 돌려준 값이어야 하고,
없는 id 면 UNKNOWN_EVENT 로 되돌려 모델이 먼저 일정을 찾게 한다.
status / created_by / expires_at / child_id 는 규칙이 채운다.

알림 tool 은 두지 않는다. 알림 등록은 일정 승인 뒤 보호자가 거는 경로다 (계약서 §07).
"""

from datetime import datetime, time, timedelta
from typing import Any

from app.agents.common.datetime_rules import (
    DateParseError,
    combine,
    resolve_date,
    resolve_query_bound,
    resolve_time,
)
from app.agents.memory.context import AgentContext
from app.agents.memory.result import ErrorCode, ToolResult, fail, ok
from app.agents.memory.schemas.schedule import (
    EventCreate,
    EventItemCreate,
    EventItemRef,
    EventItemUpdate,
    EventQuery,
    EventRef,
    EventUpdate,
)
from app.agents.memory.store.ports import EventRow

EVENT = "event"
EVENT_ITEM = "event_item"

DRAFT_TTL_HOURS = 24  # 승인 없는 draft 는 24시간 뒤 만료

_UNKNOWN_EVENT = "그 event_id 의 일정이 없다. create_event 나 query_event 결과의 id 를 쓴다."


def _date_remedy(exc: DateParseError) -> str:
    return f"{exc} 오늘·모레·금요일 같은 원문 표현이나 YYYY-MM-DD 로 넣는다."


def _time_remedy(exc: DateParseError) -> str:
    return f"{exc} 시각은 '오전 10시'·'저녁 8시'·'14:30' 처럼 몇 시인지 알 때만 넣는다."


async def _event_summary(context: AgentContext, row: EventRow) -> dict[str, Any]:
    """준비물을 함께 실어 보낸다. 조회 tool 이 없어서 여기서만 볼 수 있다."""
    return row.to_summary(items=await context.store.list_event_items(event_id=row.id))


# ── event ───────────────────────────────────────────────────────
async def create_event(context: AgentContext, args: EventCreate) -> ToolResult:
    try:
        day = resolve_date(args.starts_on, today=context.today, direction=args.temporal_direction)
    except DateParseError as exc:
        return fail("create", EVENT, ErrorCode.DATE_UNPARSEABLE, _date_remedy(exc))

    try:
        starts_moment = resolve_time(args.starts_time)
        ends_moment = resolve_time(args.ends_time)
    except DateParseError as exc:
        return fail("create", EVENT, ErrorCode.DATE_UNPARSEABLE, _time_remedy(exc))

    # 시각을 말하지 않았으면 만들어내지 않고 하루 종일 일정으로 둔다
    all_day = starts_moment is None
    row = await context.store.create_event(
        child_id=context.child_id,
        title=args.title,
        starts_at=combine(day, starts_moment, context.timezone),
        ends_at=combine(day, ends_moment, context.timezone) if ends_moment else None,
        all_day=all_day,
        fields={
            "event_type": args.event_type,
            "category": args.category,
            "status": "draft",  # 승인 전까지 draft. 자동 확정 경로를 만들지 않는다
            "created_by": "agent",
            "expires_at": context.now + timedelta(hours=DRAFT_TTL_HOURS),
        },
    )
    return ok(
        "create",
        EVENT,
        id=row.id,
        starts_at=row.starts_at.isoformat(),
        all_day=row.all_day,
    )


async def query_event(context: AgentContext, args: EventQuery) -> ToolResult:
    try:
        # 날짜 조건은 starts_at 기준이다. "내일 무슨 일정 있어?" 가 이 경로다
        bounds = {
            "date_from": resolve_query_bound(
                args.date_from,
                today=context.today,
                direction=args.temporal_direction,
                is_end=False,
            ),
            "date_to": resolve_query_bound(
                args.date_to,
                today=context.today,
                direction=args.temporal_direction,
                is_end=True,
            ),
        }
    except DateParseError as exc:
        return fail("query", EVENT, ErrorCode.DATE_UNPARSEABLE, _date_remedy(exc))

    rows = await context.store.query_events(
        child_id=context.child_id, title_query=args.title_query, **bounds
    )
    return ok(
        "query",
        EVENT,
        count=len(rows),
        events=[await _event_summary(context, row) for row in rows],
    )


async def update_event(context: AgentContext, args: EventUpdate) -> ToolResult:
    current = await context.store.get_event(event_id=args.event_id)
    if current is None:
        return fail("update", EVENT, ErrorCode.UNKNOWN_EVENT, _UNKNOWN_EVENT)

    try:
        starts_at, all_day = _merge_start(context, args, current)
        ends_at = _merge_end(context, args, current, starts_at)
    except DateParseError as exc:
        return fail("update", EVENT, ErrorCode.DATE_UNPARSEABLE, _date_remedy(exc))

    fields: dict[str, Any] = {
        "title": args.title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "all_day": all_day,
        "event_type": args.event_type,
        "category": args.category,
    }
    row = await context.store.update_event(event_id=args.event_id, fields=fields)
    if row is None:
        return fail("update", EVENT, ErrorCode.UNKNOWN_EVENT, _UNKNOWN_EVENT)
    return ok("update", EVENT, id=row.id, starts_at=row.starts_at.isoformat())


def _merge_start(
    context: AgentContext, args: EventUpdate, current: EventRow
) -> tuple[datetime | None, bool | None]:
    """날짜만 바꾸면 시각은 유지하고, 시각만 바꾸면 날짜를 유지한다."""
    if args.starts_on is None and args.starts_time is None:
        return None, None  # 시작 시각은 건드리지 않는다

    local = current.starts_at.astimezone(context.timezone)
    day = (
        resolve_date(args.starts_on, today=context.today, direction=args.temporal_direction)
        if args.starts_on is not None
        else local.date()
    )
    if args.starts_time is not None:
        moment = resolve_time(args.starts_time)
        return combine(day, moment, context.timezone), moment is None

    # 시각을 안 줬으면 원래 성격을 유지한다. all_day 였으면 계속 all_day
    keep = None if current.all_day else local.timetz().replace(tzinfo=None)
    return combine(day, keep, context.timezone), current.all_day


def _merge_end(
    context: AgentContext, args: EventUpdate, current: EventRow, starts_at: datetime | None
) -> datetime | None:
    if args.ends_on is None and args.ends_time is None:
        return None  # 종료 시각은 건드리지 않는다

    anchor = starts_at or current.starts_at
    day = (
        resolve_date(args.ends_on, today=context.today, direction=args.temporal_direction)
        if args.ends_on is not None
        else anchor.astimezone(context.timezone).date()
    )
    moment: time | None = resolve_time(args.ends_time) if args.ends_time else None
    return combine(day, moment, context.timezone)


async def delete_event(context: AgentContext, args: EventRef) -> ToolResult:
    # 준비물도 함께 사라진다 (ON DELETE CASCADE)
    if not await context.store.delete_event(event_id=args.event_id):
        return fail("delete", EVENT, ErrorCode.UNKNOWN_EVENT, _UNKNOWN_EVENT)
    return ok("delete", EVENT, id=args.event_id)


# ── event_item ──────────────────────────────────────────────────
async def create_event_item(context: AgentContext, args: EventItemCreate) -> ToolResult:
    if await context.store.get_event(event_id=args.event_id) is None:
        return fail("create", EVENT_ITEM, ErrorCode.UNKNOWN_EVENT, _UNKNOWN_EVENT)

    row = await context.store.create_event_item(event_id=args.event_id, item_name=args.item_name)
    return ok("create", EVENT_ITEM, item_id=row.item_id, item_name=row.item_name)


async def update_event_item(context: AgentContext, args: EventItemUpdate) -> ToolResult:
    row = await context.store.update_event_item(
        item_id=args.item_id,
        fields={"item_name": args.item_name, "is_prepared": args.is_prepared},
    )
    if row is None:
        return fail("update", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _item_not_found())
    return ok("update", EVENT_ITEM, item_id=row.item_id, is_prepared=row.is_prepared)


async def delete_event_item(context: AgentContext, args: EventItemRef) -> ToolResult:
    if not await context.store.delete_event_item(item_id=args.item_id):
        return fail("delete", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _item_not_found())
    return ok("delete", EVENT_ITEM, item_id=args.item_id)


def _item_not_found() -> str:
    return "그 item_id 의 준비물이 없다. query_event 결과의 items[].item_id 를 쓴다."


SCHEDULE_HANDLERS = {
    "create_event": create_event,
    "query_event": query_event,
    "update_event": update_event,
    "delete_event": delete_event,
    "create_event_item": create_event_item,
    "update_event_item": update_event_item,
    "delete_event_item": delete_event_item,
}
