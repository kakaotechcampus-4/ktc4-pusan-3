"""event / event_item 의 CRUD tool 7개.

일정은 관찰과 달리 의존성(준비물은 event 없이 존재 X)이 있음.
그래서 event_id는 항상 create_event / query_event 가 돌려준 값이어야 하고,
없는 id 면 UNKNOWN_EVENT 로 되돌려 모델이 먼저 일정을 찾게 한다.
status / created_by / expires_at / child_id 는 규칙이 채운다.
새 일정도 고친 일정도 draft다. 확정은 보호자 승인을 거쳐야 한다.

알림은 등록된 일정을 기준으로 자동 설정되므로 Agent는 일정만 만들고 안내한다.
"""

from datetime import timedelta
from typing import Any

from app.agents.common.datetime_rules import (
    DateParseError,
    EventWhen,
    MissingEndTime,
    MissingStartTime,
    WhenPatch,
    check_when,
    resolve_query_bound,
    resolve_when,
)
from app.agents.memory.context import AgentContext
from app.agents.memory.result import ErrorCode, Operation, ToolResult, fail, ok
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
_NEEDS_START_TIME = (
    "일정은 시작 시각이 있어야 저장한다. 몇 시인지 보호자에게 묻고 답을 들은 뒤 다시 부른다. "
    "'낮'·'아침' 처럼 시간대만 아는 것도 시각이 아니다."
)
_NEEDS_END_TIME = (
    "끝나는 날짜는 있는데 끝나는 시각이 없다. ends_time에 끝나는 시각을 넣거나, "
    "모르면 보호자에게 묻는다. 끝을 비워 둘 거면 ends_on도 함께 뺀다."
)


def _date_remedy(exc: DateParseError) -> str:
    return f"{exc} 오늘·모레·금요일 같은 원문 표현이나 YYYY-MM-DD 로 넣는다."


async def _event_summary(context: AgentContext, row: EventRow) -> dict[str, Any]:
    """준비물을 함께 실어 보낸다. 조회 tool 이 없어서 여기서만 볼 수 있다."""
    return row.to_summary(items=await context.store.list_event_items(event_id=row.id))


# ── event ───────────────────────────────────────────────────────
async def create_event(context: AgentContext, args: EventCreate) -> ToolResult:
    patch = WhenPatch(
        starts_on=args.starts_on,
        starts_time=args.starts_time,
        ends_on=args.ends_on,
        ends_time=args.ends_time,
        direction=args.temporal_direction,
    )
    when = _resolve_when(context, patch, current=None, operation="create")
    if isinstance(when, ToolResult):
        return when

    row = await context.store.create_event(
        child_id=context.child_id,
        title=args.title,
        starts_at=when.starts_at,
        ends_at=when.ends_at,
        all_day=when.all_day,
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
        # 날짜 조건은 starts_at 기준
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

    patch = WhenPatch(
        starts_on=args.starts_on,
        starts_time=args.starts_time,
        ends_on=args.ends_on,
        ends_time=args.ends_time,
        direction=args.temporal_direction,
        # ends_at은 fields가 아니라 when으로 가기 때문에 store의 clear과 무관
        drop_end="ends_at" in args.clear,
    )
    before = EventWhen(
        starts_at=current.starts_at, ends_at=current.ends_at, all_day=current.all_day
    )
    # 언제로 변경할지 언급이 없으면 구간 재설정은 스킵
    when: EventWhen | None = None
    if not patch.is_empty():
        resolved = _resolve_when(context, patch, current=before, operation="update")
        if isinstance(resolved, ToolResult):
            return resolved
        when = resolved

    edits = {"title": args.title, "event_type": args.event_type, "category": args.category}
    # 안 바꿀 필드(None)는 store 로 넘기지 않는다
    fields: dict[str, Any] = {key: value for key, value in edits.items() if value is not None}
    moved = when is not None and when != before
    if moved or fields:
        # 고친 일정은 다시 승인을 받도록. 만료 시계도 수정 시점부터 다시 셈
        fields["status"] = "draft"
        fields["expires_at"] = context.now + timedelta(hours=DRAFT_TTL_HOURS)

    row = await context.store.update_event(event_id=args.event_id, fields=fields, when=when)
    if row is None:
        return fail("update", EVENT, ErrorCode.UNKNOWN_EVENT, _UNKNOWN_EVENT)
    # 비운 필드를 필드명만 실어서 모델이 결과로 지워진 걸 확인 가능하게 함
    cleared = {"cleared": ["ends_at"]} if patch.drop_end else {}
    return ok("update", EVENT, id=row.id, starts_at=row.starts_at.isoformat(), **cleared)


def _resolve_when(
    context: AgentContext,
    patch: WhenPatch,
    *,
    current: EventWhen | None,
    operation: Operation,
) -> EventWhen | ToolResult:
    """시간 구간을 확정한다. 실패하면 ToolResult로 돌려 모델이 고쳐 다시 부르게 한다."""
    try:
        when = resolve_when(patch, current=current, today=context.today, tz=context.timezone)
    except MissingStartTime:
        # 알림이 시작 시각을 기준으로 가기 때문에
        # 자정으로 임의로 두게 하지 않고 몇 시인지 체크하게 함
        return fail(operation, EVENT, ErrorCode.DATE_UNPARSEABLE, _NEEDS_START_TIME)
    except MissingEndTime:
        return fail(operation, EVENT, ErrorCode.DATE_UNPARSEABLE, _NEEDS_END_TIME)
    except DateParseError as exc:
        return fail(operation, EVENT, ErrorCode.DATE_UNPARSEABLE, _date_remedy(exc))

    problem = check_when(when, patch)
    if problem is not None:
        return fail(operation, EVENT, ErrorCode.VALIDATION_ERROR, problem)
    return when


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
    edits = {"item_name": args.item_name, "is_prepared": args.is_prepared}
    row = await context.store.update_event_item(
        item_id=args.item_id,
        # 안 바꿀 필드(None)는 넘기지 않는다. is_prepared=False 는 바꾸는 값이라 남는다
        fields={key: value for key, value in edits.items() if value is not None},
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
