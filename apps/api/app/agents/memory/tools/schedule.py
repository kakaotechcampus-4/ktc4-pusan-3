"""event / event_item 의 CRUD tool 7개.

create_event와 update_event는 DB에 쓰지 않는다. 시각 해석과 구간 검증을 통과한 값을
EventDraft로 만들어 context.drafts에 넣고, 저장은 보호자가 초안을 제출한 뒤 백엔드가 한다.
새 일정의 준비물도 여기서 items로 같이 받는다.

수정도 현재 값에 바뀐 값을 얹은 초안을 만들고, 바뀐 필드 이름을 changed에 적는다.
현재 값은 버퍼의 초안이 먼저고 없으면 DB 행이다. 기존 일정의 준비물 추가와 이름 변경도
같은 초안에 얹는다.

바로 쓰는 것은 셋이다. 준비물 챙김 표시(is_prepared)는 되돌릴 수 있고,
일정 삭제와 준비물 삭제는 승인 없이 지운다.
이름과 챙김 표시가 한 호출에 같이 오면 챙김을 먼저 쓰고 이름 초안을 그 위에 얹는다.

기존 일정을 가리키는 event_id 는 query_event가 돌려준 값이어야 하고,
없는 id 면 UNKNOWN_EVENT 로 되돌려 모델이 먼저 일정을 찾게 한다.
created_by / child_id 는 규칙이 채운다.

알림은 등록된 일정을 기준으로 자동 설정되므로 Agent는 일정만 만들고 안내한다.
"""

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, time, tzinfo
from typing import Any

from app.agents.common.datetime_rules import (
    DateParseError,
    EventWhen,
    MissingEndTime,
    MissingStartTime,
    WhenPatch,
    check_when,
    is_all_day,
    resolve_time,
    resolve_when,
)
from app.agents.memory.context import AgentContext, FoundRows
from app.agents.memory.drafts import (
    DraftItem,
    EventDraft,
    EventSnapshot,
    RepeatRule,
    SeriesDraft,
    SeriesSnapshot,
)
from app.agents.memory.result import ErrorCode, Operation, ToolResult, cut, fail, nearest, ok
from app.agents.memory.schemas.common import WEEKDAY_ISO, EventCategory, EventType, Weekday
from app.agents.memory.schemas.schedule import (
    EventCreate,
    EventItemCreate,
    EventItemRef,
    EventItemUpdate,
    EventQuery,
    EventUpdate,
)
from app.agents.memory.store.ports import EventItemRow, EventRow, SeriesRow, supports_series
from app.agents.memory.tools.dates import (
    check_new_event,
    date_remedy,
    resolve_bounds,
    resolve_event_days,
    resolve_repeat_until,
)
from app.rules.recurrence import MAX_REPEAT_DAYS, RepeatError, occurrence_dates

logger = logging.getLogger(__name__)

EVENT = "event"
EVENT_ITEM = "event_item"

_EVENT_FIELDS = ("title", "starts_at", "ends_at", "all_day", "event_type", "category")
_CHANGE_ORDER = (*_EVENT_FIELDS, "items")

_UNKNOWN_EVENT = "그 event_id 의 일정이 없다. query_event 결과의 id 를 쓴다."
_NEEDS_START_TIME = (
    "일정은 시작 시각이 있어야 만든다. 몇 시인지 보호자에게 묻고 답을 들은 뒤 다시 부른다. "
    "'낮'·'아침' 처럼 시간대만 아는 것도 시각이 아니다."
)
_NEEDS_END_TIME = (
    "끝나는 날짜는 있는데 끝나는 시각이 없다. ends_time에 끝나는 시각을 넣거나, "
    "모르면 보호자에게 묻는다. 끝을 비워 둘 거면 ends_on도 함께 뺀다."
)
_UNTIL_WITHOUT_WEEKDAYS = (
    "repeat_until 만 있고 반복 요일이 없다. 요일마다 되풀이되는 일정이면 repeat_weekdays 를 "
    "같이 넣고, 아니면 repeat_until 을 뺀다."
)
_REPEAT_ENDS_ON = (
    "반복 일정의 한 회차는 하루 안에서 끝난다. ends_on 을 빼고 필요하면 ends_time 만 넣는다."
)
_NEEDS_REPEAT_UNTIL = (
    "반복 일정은 끝 날짜가 있어야 만든다. 언제까지 반복할지 보호자에게 묻고 답을 들은 뒤 "
    "다시 부른다. 끝 날짜를 정해 넣지 않는다."
)
_REPEAT_TOO_LONG = (
    f"반복은 시작 날짜부터 {MAX_REPEAT_DAYS}일 안에서만 만든다. "
    f"{MAX_REPEAT_DAYS}일 안의 끝 날짜를 보호자에게 묻는다."
)
_ITEM_GONE_AFTER_CHECK = (
    "챙김 표시를 먼저 반영한 뒤 이름 초안을 만들다 준비물을 찾지 못했다. "
    "query_event 로 준비물이 남아 있는지 확인한다."
)
_NEEDS_SCOPE = (
    "반복 일정이다. 날짜를 집어 말한 이 날 하나만이면 scope=one, 날짜 없이 그 반복 일정 전체이면 "
    "scope=series 로 다시 부른다. 찾은 결과를 보고도 정해지지 않으면 보호자에게 묻는다."
)
_NOT_REPEATING = "반복 일정이 아니다. scope 를 빼고 다시 부른다."
_REPEAT_ARGS_NEED_SERIES = (
    "repeat_weekdays · repeat_until 은 반복 일정 전체(scope=series)를 고칠 때만 쓴다."
)
_SERIES_NO_DATES = (
    "반복 일정 전체는 날짜가 아니라 반복 규칙(repeat_weekdays · repeat_until)으로 바꾼다. "
    "하루만 옮기려면 scope=one 으로 그 회차를 고친다."
)
_SERIES_END_BEFORE_START = (
    "끝나는 시각이 시작보다 늦어야 한다. 반복 일정의 한 회차는 하루 안에서 끝난다."
)
_SERIES_ALL_DAY_NO_END = "종일 일정에는 끝나는 시각이 없다."
_SERIES_NEEDS_START = "종일 반복 일정을 시간 있는 일정으로 바꾸려면 starts_time 도 같이 넣는다."
_SERIES_NO_FUTURE = (
    "그 반복 일정은 오늘 이후 남은 회차가 없다. 이어서 하려면 새 반복 일정으로 만든다."
)
_SERIES_RULE_NO_FUTURE = (
    "그 요일과 끝 날짜로는 오늘 이후 회차가 하나도 없다. 요일이나 끝 날짜를 보호자에게 다시 묻는다."
)
_CHECK_IS_ONE_DAY = (
    "챙김 표시는 늘 그날 회차 하나다. scope 를 빼고 그 회차의 item_id 로 다시 부른다."
)
_SERIES_ITEM_RENAME_ONLY = (
    "scope=series 는 준비물 이름을 바꿀 때만 쓴다. item_name 을 넣거나 scope 를 뺀다."
)
_SERIES_ITEM_NOT_FOUND = (
    "묶음의 준비물 목록에 그 이름이 없다. query_event 로 준비물을 다시 확인한다."
)
_SERIES_ALWAYS_CORE = (
    "반복 일정은 늘 core 다. event_type 은 바꾸지 않는다. 한 번 가는 일정으로 바꾸려면 "
    "반복을 지우고 새로 만든다."
)
_REPEAT_UNSUPPORTED = (
    "아직 요일마다 반복되는 일정은 만들 수 없다. 반복으로 만들지 말고 보호자에게 아직 지원하지 "
    "않는다고 알린다. 날짜마다 한 번 가는 일정으로 따로 만들지는 보호자에게 묻는다."
)

_SERIES_FIELDS = (
    "title",
    "starts_time",
    "ends_time",
    "all_day",
    "event_type",
    "category",
    "repeat",
    "items",
)


def _new_items(names: list[str]) -> tuple[DraftItem, ...]:
    """새 일정의 준비물. 한 호출에 같은 이름이 두 번 오면 앞의 것만 남긴다."""
    items: tuple[DraftItem, ...] = ()
    for name in names:
        if not _has_item(items, name):
            items = (*items, DraftItem(item_id=None, item_name=name))
    return items


def _has_item(items: tuple[DraftItem, ...], name: str) -> bool:
    """같은 이름의 준비물이 이미 있는지.

    제출 API 가 items를 최종 목록으로 읽으므로 같은 이름이 둘이면 그대로 두 행이 되어,
    item_id는 안 겹치지만(한쪽은 None) 보호자에게는 같은 준비물이 두 번 보인다.
    """
    target = name.strip()
    return any(item.item_name.strip() == target for item in items)


def _repeat_weekdays(args: EventCreate) -> tuple[int, ...] | None:
    """반복 요일을 ISO 숫자로. 반복이 아니면 None (빈 목록도 반복이 아니다)."""
    if not args.repeat_weekdays:
        return None
    return _weekday_numbers(args.repeat_weekdays)


def _check_repeat_args(args: EventCreate, weekdays: tuple[int, ...] | None) -> ToolResult | None:
    """반복 인자 조합이 맞는지. 날짜를 풀기 전에 값싸게 거른다."""
    if weekdays is None:
        if args.repeat_until is None:
            return None
        return fail("create", EVENT, ErrorCode.INVALID_ARGS, _UNTIL_WITHOUT_WEEKDAYS)
    if args.ends_on is not None:
        return fail("create", EVENT, ErrorCode.INVALID_ARGS, _REPEAT_ENDS_ON)
    if args.repeat_until is None:
        return fail("create", EVENT, ErrorCode.REPEAT_UNTIL_REQUIRED, _NEEDS_REPEAT_UNTIL)
    return None


def _repeat_failure(exc: RepeatError, operation: Operation = "create") -> ToolResult:
    """규칙이 성립하지 않는 이유를 모델이 다음에 할 일로 바꾼다."""
    if exc.code == "too_long":
        return fail(operation, EVENT, ErrorCode.DATE_UNPARSEABLE, _REPEAT_TOO_LONG)
    if exc.code in ("until_before_start", "no_occurrence"):
        message = f"{exc} 언제까지 반복하는지 보호자에게 묻는다."
        return fail(operation, EVENT, ErrorCode.DATE_UNPARSEABLE, message)
    return fail(operation, EVENT, ErrorCode.INVALID_ARGS, str(exc))


async def _expand_repeat(
    context: AgentContext, args: EventCreate, weekdays: tuple[int, ...], when: EventWhen
) -> tuple[EventWhen, RepeatRule, int] | ToolResult:
    """첫 회차로 구간을 옮기고 반복 규칙과 회차 수를 돌려준다. 회차 날짜는 규칙이 정한다."""
    start_day = when.starts_at.astimezone(context.timezone).date()
    if when.ends_at is not None and when.ends_at.astimezone(context.timezone).date() != start_day:
        return fail("create", EVENT, ErrorCode.INVALID_ARGS, _REPEAT_ENDS_ON)
    try:
        until = await resolve_repeat_until(context, args, start=start_day)
        days = occurrence_dates(start_day, weekdays, until)
    except DateParseError as exc:
        return fail("create", EVENT, ErrorCode.DATE_UNPARSEABLE, date_remedy(exc))
    except RepeatError as exc:
        return _repeat_failure(exc)
    shift = days[0] - start_day
    first = EventWhen(
        starts_at=when.starts_at + shift,
        ends_at=when.ends_at + shift if when.ends_at is not None else None,
        all_day=when.all_day,
    )
    return first, RepeatRule(weekdays=weekdays, until=until), len(days)


# ── 반복 묶음 (#738) ────────────────────────────────────────────


def _weekday_numbers(names: Iterable[str]) -> tuple[int, ...]:
    """반복 요일 이름을 ISO 숫자(1=월 … 7=일)로. 겹친 요일은 하나로 본다."""
    return tuple(sorted({WEEKDAY_ISO[Weekday(name)] for name in names}))


def _scope_problem(
    row: EventRow | None, scope: str | None, operation: Operation, resource: str
) -> ToolResult | None:
    """대상이 묶음의 회차인데 범위가 없거나, 묶음이 아닌데 series 면 되돌린다.

    없는 일정(row 없음)은 각 tool 의 기존 길이 UNKNOWN_EVENT 로 돌려준다.
    """
    if row is None:
        return None
    if row.series is None:
        if scope == "series":
            return fail(operation, resource, ErrorCode.INVALID_ARGS, _NOT_REPEATING)
        return None
    if scope is None:
        return fail(operation, resource, ErrorCode.SCOPE_REQUIRED, _NEEDS_SCOPE)
    return None


def _clock_of(row: EventRow, tz: tzinfo) -> tuple[time | None, time | None]:
    """회차의 하루 시각 (시작, 끝). 종일이면 둘 다 None."""
    if row.all_day:
        return None, None
    ends = row.ends_at.astimezone(tz).time() if row.ends_at is not None else None
    return row.starts_at.astimezone(tz).time(), ends


def _row_values(row: EventRow, tz: tzinfo) -> tuple[Any, ...]:
    """회차의 값을 항목별로 (제목, 시각, 종류, 카테고리). 시각은 (시작, 끝, 종일) 한 묶음이다 —
    따로 세면 "종일인데 8:30" 같은 어느 회차에도 없는 조합이 나온다."""
    starts, ends = _clock_of(row, tz)
    return (
        row.title,
        (starts, ends, row.all_day),
        str(row.fields.get("event_type", EventType.CORE)),
        str(row.fields.get("category", EventCategory.ETC)),
    )


def _most_common[T](values: list[T]) -> T:
    """가장 흔한 값. 같은 수면 먼저 나온 쪽."""
    counts = Counter(values)
    top = max(counts.values())
    return next(value for value in values if counts[value] == top)


def _common_values(rows: list[EventRow], tz: tzinfo) -> tuple[tuple[Any, ...], EventRow]:
    """오늘 이후 회차에서 항목마다 가장 흔한 값과, 준비물을 읽을 회차.

    첫 회차를 기준으로 삼으면 그 회차만 따로 고쳐 둔 값이 묶음의 기준이 되어, 준비물만 더하는 초안도
    그 값을 들고 가 제출할 때 나머지 회차에 번진다. 회차 전체를 한 표로 세면 제목 하나만 달라도
    표가 쪼개져 소수 값이 뽑혀서 항목마다 따로 센다 (#744 리뷰).
    준비물은 회차마다 따로 읽어야 해서, 흔한 값과 가장 많이 맞는 회차(같으면 먼저 오는 쪽)의
    준비물을 쓴다.
    """
    values = [_row_values(row, tz) for row in rows]
    common = tuple(_most_common([value[i] for value in values]) for i in range(len(values[0])))
    matches = [sum(a == b for a, b in zip(value, common, strict=True)) for value in values]
    return common, rows[matches.index(max(matches))]


async def _series_base(
    context: AgentContext, series: SeriesRow, operation: Operation, resource: str
) -> SeriesDraft | ToolResult:
    """묶음 수정의 출발점. 버퍼의 초안이 먼저고, 없으면 오늘 이후 회차의 흔한 값으로 만든다."""
    buffered = context.drafts.get_series(series.id)
    if buffered is not None:
        return buffered
    rows = await context.store.list_series_events(series_id=series.id, date_from=context.today)
    if not rows:
        return fail(operation, resource, ErrorCode.TARGET_NOT_FOUND, _SERIES_NO_FUTURE)
    (title, (starts, ends, all_day), event_type, category), frame = _common_values(
        rows, context.timezone
    )
    items = await context.store.list_event_items(event_id=frame.id)
    before = SeriesSnapshot(
        title=title,
        starts_time=starts,
        ends_time=ends,
        all_day=all_day,
        event_type=event_type,
        category=category,
        repeat=RepeatRule(weekdays=series.weekdays, until=series.until),
        items=tuple(item.item_name for item in items),
    )
    return SeriesDraft(
        series_id=series.id,
        title=before.title,
        starts_time=before.starts_time,
        ends_time=before.ends_time,
        all_day=before.all_day,
        event_type=before.event_type,
        category=before.category,
        repeat=before.repeat,
        items=before.items,
        before=before,
    )


def _series_changed(draft: SeriesDraft) -> tuple[str, ...]:
    """원본(`before`)과 달라진 필드. 고쳤다가 되돌린 값은 바뀐 것으로 치지 않는다.

    준비물은 이름 집합으로 본다 — 새로 넣은 이름은 맨 뒤에 붙어서, 뺐다가 다시 넣으면 내용이 같아도
    순서가 달라진다 (#744 리뷰). 초안에 싣는 순서는 그대로 둔다.
    """
    before = draft.before
    if before is None:
        return draft.changed
    return tuple(name for name in _SERIES_FIELDS if not _same_field(name, draft, before))


def _same_field(name: str, draft: SeriesDraft, before: SeriesSnapshot) -> bool:
    if name == "items":
        return set(draft.items) == set(before.items)
    return getattr(draft, name) == getattr(before, name)


def _put_series(
    context: AgentContext, draft: SeriesDraft
) -> tuple[SeriesDraft | None, tuple[str, ...]]:
    """바뀐 게 있으면 초안으로 두고, 원본과 같아졌으면 초안을 뺀다."""
    changed = _series_changed(draft)
    if not changed:
        context.drafts.drop_series(draft.series_id)
        return None, ()
    return context.drafts.put_series(replace(draft, changed=changed)), changed


def _shown_name(draft: SeriesDraft, original: str) -> str:
    """원래 이름의 준비물이 지금 초안에서 부르는 이름. 이름을 바꿨으면 바뀐 이름이다."""
    return dict(draft.renames).get(original, original)


def _with_rename(
    renames: tuple[tuple[str, str], ...], original: str, name: str
) -> tuple[tuple[str, str], ...]:
    """원래 이름 → 지금 이름. 원래 이름으로 돌아가면 기록을 지운다."""
    mapping = dict(renames)
    if name == original:
        mapping.pop(original, None)
    else:
        mapping[original] = name
    return tuple(mapping.items())


def _series_times(
    base: SeriesDraft, args: EventUpdate
) -> tuple[time | None, time | None, bool] | ToolResult:
    """묶음의 하루 시각을 고친 값 (시작, 끝, 종일). 날짜는 반복 규칙이 정하니 시각만 본다."""
    starts, ends, all_day = base.starts_time, base.ends_time, base.all_day
    try:
        if args.starts_time is not None:
            if is_all_day(args.starts_time):
                starts, ends, all_day = None, None, True
            else:
                starts, all_day = resolve_time(args.starts_time), False
        if args.ends_time is not None:
            ends = resolve_time(args.ends_time)
    except DateParseError as exc:
        return fail("update", EVENT, ErrorCode.DATE_UNPARSEABLE, date_remedy(exc))
    if "ends_at" in args.clear:
        ends = None
    if all_day and ends is not None:
        return fail("update", EVENT, ErrorCode.INVALID_ARGS, _SERIES_ALL_DAY_NO_END)
    if not all_day and starts is None:
        return fail("update", EVENT, ErrorCode.INVALID_ARGS, _SERIES_NEEDS_START)
    if starts is not None and ends is not None and ends <= starts:
        return fail("update", EVENT, ErrorCode.INVALID_ARGS, _SERIES_END_BEFORE_START)
    return starts, ends, all_day


async def _series_repeat(
    context: AgentContext, args: EventUpdate, current: RepeatRule, series: SeriesRow
) -> RepeatRule | ToolResult:
    """바꾼 반복 규칙. 90일은 묶음의 첫 회차에서 센다. 오늘 이후 회차가 남아야 한다."""
    if not args.repeat_weekdays and args.repeat_until is None:
        return current
    weekdays = _weekday_numbers(args.repeat_weekdays) if args.repeat_weekdays else current.weekdays
    try:
        until = (
            await resolve_repeat_until(context, args, start=context.today, start_name="오늘")
            if args.repeat_until is not None
            else current.until
        )
        days = occurrence_dates(series.starts_on, weekdays, until)
    except DateParseError as exc:
        return fail("update", EVENT, ErrorCode.DATE_UNPARSEABLE, date_remedy(exc))
    except RepeatError as exc:
        return _repeat_failure(exc, "update")
    if not any(day >= context.today for day in days):
        return fail("update", EVENT, ErrorCode.DATE_UNPARSEABLE, _SERIES_RULE_NO_FUTURE)
    return RepeatRule(weekdays=weekdays, until=until)


async def _update_series(context: AgentContext, args: EventUpdate, series: SeriesRow) -> ToolResult:
    """묶음 전체(오늘 이후 회차)를 고친 초안. 저장하지 않는다."""
    if args.starts_on is not None or args.ends_on is not None:
        return fail("update", EVENT, ErrorCode.INVALID_ARGS, _SERIES_NO_DATES)
    if args.event_type is not None and str(args.event_type) != EventType.CORE:
        return fail("update", EVENT, ErrorCode.INVALID_ARGS, _SERIES_ALWAYS_CORE)
    base = await _series_base(context, series, "update", EVENT)
    if isinstance(base, ToolResult):
        return base
    times = _series_times(base, args)
    if isinstance(times, ToolResult):
        return times
    starts, ends, all_day = times
    repeat = await _series_repeat(context, args, base.repeat, series)
    if isinstance(repeat, ToolResult):
        return repeat
    updated = replace(
        base,
        title=args.title if args.title is not None else base.title,
        starts_time=starts,
        ends_time=ends,
        all_day=all_day,
        category=str(args.category) if args.category is not None else base.category,
        repeat=repeat,
    )
    stored, changed = _put_series(context, updated)
    if stored is None:
        # 같은 값으로 바꾸는 요청이었다면 모델이 "이미 그렇게 되어 있어요" 라고 답함
        return ok("update", EVENT, id=args.event_id, scope="series", draft=False, changed=[])
    return ok(
        "update",
        EVENT,
        id=args.event_id,
        scope="series",
        draft=True,
        changed=list(changed),
        repeat_label=stored.repeat.to_payload().label,
    )


async def _add_series_item(context: AgentContext, series: SeriesRow, item_name: str) -> ToolResult:
    """묶음 초안에 준비물 이름을 더한다. 제출하면 오늘 이후 회차 전부에 붙는다."""
    base = await _series_base(context, series, "create", EVENT_ITEM)
    if isinstance(base, ToolResult):
        return base
    name = item_name.strip()
    if any(existing.strip() == name for existing in base.items):
        return ok(
            "create",
            EVENT_ITEM,
            draft=False,
            added=False,
            scope="series",
            item_name=name,
            items=list(base.items),
        )
    updated = replace(base, items=(*base.items, name))
    stored, _ = _put_series(context, updated)
    return ok(
        "create",
        EVENT_ITEM,
        draft=stored is not None,
        added=True,
        scope="series",
        item_name=name,
        items=list(updated.items),
    )


async def _rename_series_item(
    context: AgentContext, series: SeriesRow, current: EventItemRow, item_name: str
) -> ToolResult:
    """묶음 초안의 준비물 이름을 바꾼다. 같은 이름의 회차 준비물이 제출할 때 따라 바뀐다.

    저장된 준비물의 이름은 run 안에서 그대로라, 같은 준비물을 두 번 바꾸면 처음 이름에서
    지금 이름을 따라간다 (`renames`).
    """
    base = await _series_base(context, series, "update", EVENT_ITEM)
    if isinstance(base, ToolResult):
        return base
    shown = _shown_name(base, current.item_name)
    if shown not in base.items:
        return fail("update", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _SERIES_ITEM_NOT_FOUND)
    name = item_name.strip()
    items = tuple(dict.fromkeys(name if n == shown else n for n in base.items))
    renames = _with_rename(base.renames, current.item_name, name)
    stored, changed = _put_series(context, replace(base, items=items, renames=renames))
    return ok(
        "update",
        EVENT_ITEM,
        draft=stored is not None,
        item_id=current.item_id,
        item_name=name,
        scope="series",
        changed=list(changed),
    )


async def _delete_series_item(
    context: AgentContext, series: SeriesRow, item: EventItemRow
) -> ToolResult:
    """묶음 초안에서 준비물을 뺀다. 이름을 바꿔 둔 준비물이면 바뀐 이름을 뺀다.

    저장된 회차는 건드리지 않는다. 보호자가 확인하고 제출하면 오늘 이후 회차에서 빠진다.
    """
    base = await _series_base(context, series, "delete", EVENT_ITEM)
    if isinstance(base, ToolResult):
        return base
    shown = _shown_name(base, item.item_name)
    if shown not in base.items:
        return fail("delete", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _SERIES_ITEM_NOT_FOUND)
    items = tuple(n for n in base.items if n != shown)
    renames = tuple((a, b) for a, b in base.renames if a != item.item_name)
    stored, changed = _put_series(context, replace(base, items=items, renames=renames))
    return ok(
        "delete",
        EVENT_ITEM,
        draft=stored is not None,
        item_id=item.item_id,
        scope="series",
        changed=list(changed),
    )


async def _event_summary(context: AgentContext, row: EventRow) -> dict[str, Any]:
    """준비물을 함께 실어 보낸다. 조회 tool 이 없어서 여기서만 볼 수 있다."""
    return row.to_summary(items=await context.store.list_event_items(event_id=row.id))


# ── event ───────────────────────────────────────────────────────
async def create_event(context: AgentContext, args: EventCreate) -> ToolResult:
    weekdays = _repeat_weekdays(args)
    if weekdays is not None and not supports_series(context.store):
        return fail("create", EVENT, ErrorCode.OUT_OF_SCOPE, _REPEAT_UNSUPPORTED)
    problem = _check_repeat_args(args, weekdays)
    if problem is not None:
        return problem
    try:
        # 날짜 규칙은 관찰과 같다(tools/dates.py). 시각은 resolve_when 이 이 날짜에 얹는다
        starts_on, ends_on = await resolve_event_days(context, args, current_start=None)
        if starts_on is not None:  # create_event 는 starts_on 이 필수라 늘 있다
            check_new_event(date.fromisoformat(starts_on), context.today, args)
    except DateParseError as exc:
        return fail("create", EVENT, ErrorCode.DATE_UNPARSEABLE, date_remedy(exc))
    patch = WhenPatch(
        starts_on=starts_on,
        starts_time=args.starts_time,
        ends_on=ends_on,
        ends_time=args.ends_time,
        direction=args.temporal_direction,
    )
    when = _resolve_when(context, patch, current=None, operation="create")
    if isinstance(when, ToolResult):
        return when

    repeat: RepeatRule | None = None
    repeated: dict[str, Any] = {}
    if weekdays is not None:
        expanded = await _expand_repeat(context, args, weekdays, when)
        if isinstance(expanded, ToolResult):
            return expanded
        when, repeat, count = expanded
        repeated = {"repeat_label": repeat.to_payload().label, "occurrence_count": count}

    draft = EventDraft(
        op="create",
        event_id=None,  # 아직 저장 전이라 id 존재 X
        title=args.title,
        starts_at=when.starts_at,
        ends_at=when.ends_at,
        all_day=when.all_day,
        # 반복 일정은 일상이라 core 다. 모델이 episodic 을 줘도 코드가 정한다
        event_type=EventType.CORE if repeat is not None else args.event_type,
        category=args.category,
        items=_new_items(args.items),
        repeat=repeat,
    )
    context.drafts.put(draft)
    # 모델이 저장된 일정으로 착각하고 다음 tool에 넘기면 안 됨 ->  id 싣지 않음
    return ok(
        "create",
        EVENT,
        draft=True,
        title=draft.title,
        starts_at=draft.starts_at.isoformat(),
        all_day=draft.all_day,
        items=[item.item_name for item in draft.items],
        **repeated,
    )


async def query_event(context: AgentContext, args: EventQuery) -> ToolResult:
    try:
        # 날짜 조건은 starts_at 기준
        bounds = await resolve_bounds(context, args, direction=args.temporal_direction)
    except DateParseError as exc:
        return fail("query", EVENT, ErrorCode.DATE_UNPARSEABLE, date_remedy(exc))

    rows = await context.store.query_events(
        child_id=context.child_id, title_query=args.title_query, **bounds
    )
    kept, truncated = nearest(rows, lambda row: abs((row.starts_at - context.now).total_seconds()))
    context.found[EVENT] = FoundRows(tuple(row.id for row in kept), truncated)
    return ok(
        "query",
        EVENT,
        count=len(kept),
        events=[await _event_summary(context, row) for row in kept],
        **cut(truncated, event=True),
    )


async def update_event(context: AgentContext, args: EventUpdate) -> ToolResult:
    row = await context.store.get_event(event_id=args.event_id)
    if args.scope != "series" and (args.repeat_weekdays or args.repeat_until is not None):
        return fail("update", EVENT, ErrorCode.INVALID_ARGS, _REPEAT_ARGS_NEED_SERIES)
    problem = _scope_problem(row, args.scope, "update", EVENT)
    if problem is not None:
        return problem
    if args.scope == "series" and row is not None and row.series is not None:
        return await _update_series(context, args, row.series)

    current = await current_draft(context, args.event_id, row=row)
    if current is None:
        return fail("update", EVENT, ErrorCode.UNKNOWN_EVENT, _UNKNOWN_EVENT)

    try:
        current_start = current.starts_at.astimezone(context.timezone).date()
        starts_on, ends_on = await resolve_event_days(context, args, current_start=current_start)
    except DateParseError as exc:
        return fail("update", EVENT, ErrorCode.DATE_UNPARSEABLE, date_remedy(exc))
    patch = WhenPatch(
        starts_on=starts_on,
        starts_time=args.starts_time,
        ends_on=ends_on,
        ends_time=args.ends_time,
        direction=args.temporal_direction,
        # ends_at은 컬럼째로만 지움(ends_on·ends_time 두 인자로 나뉘어 들어와서)
        drop_end="ends_at" in args.clear,
    )
    before = EventWhen(
        starts_at=current.starts_at, ends_at=current.ends_at, all_day=current.all_day
    )
    # 언제로 변경할지 언급이 없으면 구간 재설정은 스킵
    when = before
    if not patch.is_empty():
        resolved = _resolve_when(context, patch, current=before, operation="update")
        if isinstance(resolved, ToolResult):
            return resolved
        when = resolved

    updated = replace(
        current,
        op="update",
        event_id=args.event_id,
        title=args.title if args.title is not None else current.title,
        starts_at=when.starts_at,
        ends_at=when.ends_at,
        all_day=when.all_day,
        event_type=args.event_type if args.event_type is not None else current.event_type,
        category=args.category if args.category is not None else current.category,
    )
    # 버퍼에 있던 초안의 changed를 반영
    changed = _merge_changed(current.changed, _diff(current, updated))
    cleared = {"cleared": ["ends_at"]} if patch.drop_end else {}
    if not changed:
        # 같은 값으로 바꾸는 요청이었다면 모델이 "이미 그렇게 되어 있어요" 라고 답함
        return ok("update", EVENT, id=args.event_id, draft=False, changed=[], **cleared)

    context.drafts.put(replace(updated, changed=changed))
    return ok(
        "update",
        EVENT,
        id=args.event_id,
        draft=True,
        starts_at=updated.starts_at.isoformat(),
        changed=list(changed),
        **cleared,
    )


async def current_draft(
    context: AgentContext, event_id: str, *, row: EventRow | None = None
) -> EventDraft | None:
    """수정의 출발점. 버퍼의 초안이 먼저고, 없으면 DB 행을 초안 모양으로 바꾼다.

    `row` 는 부르는 쪽이 이미 읽은 그 일정이다. 넘기면 같은 행을 다시 읽지 않는다.
    """
    buffered = context.drafts.get(event_id)
    if buffered is not None:
        return buffered

    row = row if row is not None else await context.store.get_event(event_id=event_id)
    if row is None:
        return None
    rows = await context.store.list_event_items(event_id=event_id)
    items = tuple(
        DraftItem(item_id=item.item_id, item_name=item.item_name, is_prepared=item.is_prepared)
        for item in rows
    )
    # 원본은 여기에서만. 두 번째 호출부터는 버퍼의 초안이 현재 값
    before = EventSnapshot(
        title=row.title,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        all_day=row.all_day,
        event_type=row.fields.get("event_type", EventType.EPISODIC),
        category=row.fields.get("category", EventCategory.ETC),
        items=items,
    )
    return EventDraft(
        op="update",
        event_id=row.id,
        title=before.title,
        starts_at=before.starts_at,
        ends_at=before.ends_at,
        all_day=before.all_day,
        event_type=before.event_type,
        category=before.category,
        items=items,
        before=before,
    )


def _diff(before: EventDraft, after: EventDraft) -> tuple[str, ...]:
    """모델이 넘긴 인자가 아니라 값을 비교한다."""
    names = [name for name in _EVENT_FIELDS if getattr(before, name) != getattr(after, name)]
    if before.items != after.items:
        names.append("items")
    return tuple(names)


def _merge_changed(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """중복을 빼고 필드 순서로 정렬한다.."""
    seen = {name for group in groups for name in group}
    return tuple(name for name in _CHANGE_ORDER if name in seen)


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
        return fail(operation, EVENT, ErrorCode.DATE_UNPARSEABLE, date_remedy(exc))

    problem = check_when(when, patch)
    if problem is not None:
        return fail(operation, EVENT, ErrorCode.INVALID_ARGS, problem)
    return when


# ── event_item ──────────────────────────────────────────────────
async def create_event_item(context: AgentContext, args: EventItemCreate) -> ToolResult:
    """이미 있는 일정에 준비물을 붙인다. 새 일정의 준비물은 create_event 의 items 로 간다."""
    row = await context.store.get_event(event_id=args.event_id)
    problem = _scope_problem(row, args.scope, "create", EVENT_ITEM)
    if problem is not None:
        return problem
    if args.scope == "series" and row is not None and row.series is not None:
        return await _add_series_item(context, row.series, args.item_name)
    # 버퍼의 초안은 event_id 를 갖지 않는다. 여기 오는 id 는 언제나 DB id 다
    parent = await current_draft(context, args.event_id, row=row)
    if parent is None:
        return fail("create", EVENT_ITEM, ErrorCode.UNKNOWN_EVENT, _UNKNOWN_EVENT)

    if _has_item(parent.items, args.item_name):
        # 이미 있는 준비물. 더하면 제출할 때 같은 이름 두 행이 된다
        return ok(
            "create",
            EVENT_ITEM,
            draft=False,
            added=False,
            event_id=args.event_id,
            item_name=args.item_name,
            items=[item.item_name for item in parent.items],
        )

    items = (*parent.items, DraftItem(item_id=None, item_name=args.item_name))
    context.drafts.put(_with_items(parent, args.event_id, items))
    return ok(
        "create",
        EVENT_ITEM,
        draft=True,
        added=True,
        event_id=args.event_id,
        item_name=args.item_name,
        items=[item.item_name for item in items],
    )


async def update_event_item(context: AgentContext, args: EventItemUpdate) -> ToolResult:
    current = await context.store.get_event_item(item_id=args.item_id)
    if current is None:
        return fail("update", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _item_not_found())
    if args.scope == "series" and args.is_prepared is not None:
        return fail("update", EVENT_ITEM, ErrorCode.INVALID_ARGS, _CHECK_IS_ONE_DAY)
    if args.item_name is None:
        if args.scope == "series":
            return fail("update", EVENT_ITEM, ErrorCode.INVALID_ARGS, _SERIES_ITEM_RENAME_ONLY)
        return await _check_event_item(context, current, args.is_prepared)
    parent = await context.store.get_event(event_id=current.event_id)
    problem = _scope_problem(parent, args.scope, "update", EVENT_ITEM)
    if problem is not None:
        return problem
    if args.scope == "series" and parent is not None and parent.series is not None:
        return await _rename_series_item(context, parent.series, current, args.item_name)
    if args.is_prepared is not None:
        # 챙김 표시를 먼저 쓴다. _sync_checked가 버퍼를 맞춘 뒤 이름 초안이 그 값을 읽어야
        # 같은 run 의 동시 체크를 덮어쓰지 않는다
        checked = await _check_event_item(context, current, args.is_prepared)
        if not checked.success:
            return checked
        current = await context.store.get_event_item(item_id=args.item_id)
        if current is None:
            return _gone_after_check(args.item_id)
        renamed = await _rename_event_item(context, current, args, parent)
        if not renamed.success:
            return _gone_after_check(args.item_id)
        # 이름이 그대로면 결과는 챙김만 한 호출과 같다. 이름 쪽의 changed=[] 를 내면
        # 모델이 방금 한 체크를 "이미 되어 있었다"로 읽는다
        if renamed.data.get("changed") == []:
            return checked
        # 이름 초안에도 챙김 전 값을 싣는다. 없으면 방금 한 체크가 결과에서 사라진다
        return ok("update", EVENT_ITEM, **renamed.data, was_prepared=checked.data["was_prepared"])
    return await _rename_event_item(context, current, args, parent)


def _gone_after_check(item_id: str) -> ToolResult:
    """챙김을 쓴 뒤 준비물이 사라졌다. 정상 흐름에서는 오지 않아 경고로 남긴다."""
    logger.warning("챙김 처리 뒤 준비물을 찾지 못함 item_id=%s", item_id)
    return fail("update", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _ITEM_GONE_AFTER_CHECK)


async def _check_event_item(
    context: AgentContext, current: EventItemRow, is_prepared: bool | None
) -> ToolResult:
    """챙김 표시는 바로 쓴다. 되돌릴 수 있는 값이라 승인 게이트에 넣지 않는다.

    체크 시각은 store 가 정한다 — 백엔드 PATCH 와 같은 규칙 (`MemoryStore.set_event_item_prepared`).
    """
    if is_prepared is None:
        # 이름도 챙김도 없는 호출. 쓸 것이 없어 지금 값을 그대로 돌려준다
        return ok(
            "update",
            EVENT_ITEM,
            draft=False,
            item_id=current.item_id,
            is_prepared=current.is_prepared,
        )
    row = await context.store.set_event_item_prepared(
        item_id=current.item_id, is_prepared=is_prepared
    )
    if row is None:
        return fail("update", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _item_not_found())
    _sync_checked(context, row)
    # 챙김 전 값도 싣는다. 이미 체크된 것을 다시 체크했는지를 보호자에게 갈 말을 정할 때 본다
    # (#678 리뷰)
    return ok(
        "update",
        EVENT_ITEM,
        draft=False,
        item_id=row.item_id,
        is_prepared=row.is_prepared,
        was_prepared=current.is_prepared,
    )


def _sync_checked(context: AgentContext, row: EventItemRow) -> None:
    """바로 쓴 챙김 표시를 버퍼의 초안에도 맞춘다.

    is_prepared 는 payload 에 싣지 않는다. 체크 상태는
    PATCH /event-items/{iid}의 몫이라 화면은 초안에서 그 값을 보지 않는다.

    버퍼를 맞추는 건 내부 판정 때문이다. _diff 가 items를 통째로 비교해서,
    같은 run에서 update_event가 먼저 돌면 초안이 DB의 옛 값을 들고 있다가
    "준비물이 바뀌었다"로 읽힌다.
    """
    buffered = context.drafts.get(row.event_id)
    if buffered is None:
        return

    def checked(items: tuple[DraftItem, ...]) -> tuple[DraftItem, ...]:
        return tuple(
            replace(item, is_prepared=row.is_prepared) if item.item_id == row.item_id else item
            for item in items
        )

    before = buffered.before
    context.drafts.put(
        replace(
            buffered,
            items=checked(buffered.items),
            before=before if before is None else replace(before, items=checked(before.items)),
        )
    )


async def _rename_event_item(
    context: AgentContext,
    current: EventItemRow,
    args: EventItemUpdate,
    parent_row: EventRow | None = None,
) -> ToolResult:
    """이름 변경은 보호자가 초안에서 확인하고 제출한다.

    update_event_item이 먼저 쓴 챙김 표시가 반영된 current의 is_prepared를 그대로 옮긴다.
    `parent_row` 는 부르는 쪽이 이미 읽은 부모 일정이다.
    """
    parent = await current_draft(context, current.event_id, row=parent_row)
    if parent is None:
        return fail("update", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _item_not_found())

    renamed = DraftItem(
        item_id=current.item_id,
        item_name=args.item_name or current.item_name,
        is_prepared=current.is_prepared,
    )
    items = tuple(renamed if item.item_id == current.item_id else item for item in parent.items)
    if items == parent.items and not parent.changed:
        return ok(
            "update",
            EVENT_ITEM,
            draft=False,
            item_id=current.item_id,
            changed=[],
            is_prepared=current.is_prepared,
        )

    context.drafts.put(_with_items(parent, current.event_id, items))
    return ok(
        "update",
        EVENT_ITEM,
        draft=True,
        item_id=current.item_id,
        item_name=renamed.item_name,
        is_prepared=renamed.is_prepared,
    )


def _with_items(parent: EventDraft, event_id: str, items: tuple[DraftItem, ...]) -> EventDraft:
    """준비물만 바꾼 수정 초안."""
    return replace(
        parent,
        op="update",
        event_id=event_id,
        items=items,
        changed=_merge_changed(parent.changed, ("items",) if items != parent.items else ()),
    )


async def delete_event_item(context: AgentContext, args: EventItemRef) -> ToolResult:
    row = await context.store.get_event_item(item_id=args.item_id)
    if row is None:
        return fail("delete", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _item_not_found())
    parent = await context.store.get_event(event_id=row.event_id)
    problem = _scope_problem(parent, args.scope, "delete", EVENT_ITEM)
    if problem is not None:
        return problem
    if args.scope == "series" and parent is not None and parent.series is not None:
        return await _delete_series_item(context, parent.series, row)
    if not await context.store.delete_event_item(item_id=args.item_id):
        return fail("delete", EVENT_ITEM, ErrorCode.TARGET_NOT_FOUND, _item_not_found())
    # 지운 준비물이 초안에 남지 않게
    buffered = context.drafts.get(row.event_id)
    if buffered is not None:
        kept = tuple(item for item in buffered.items if item.item_id != args.item_id)
        context.drafts.put(replace(buffered, items=kept))
    return ok("delete", EVENT_ITEM, item_id=args.item_id)


def _item_not_found() -> str:
    return "그 item_id 의 준비물이 없다. query_event 결과의 items[].item_id 를 쓴다."


SCHEDULE_HANDLERS = {
    "create_event": create_event,
    "query_event": query_event,
    "update_event": update_event,
    "create_event_item": create_event_item,
    "update_event_item": update_event_item,
    "delete_event_item": delete_event_item,
}
