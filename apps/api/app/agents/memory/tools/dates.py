"""관찰 · 일정 tool 이 같이 쓰는 날짜 규칙 (#497).

날짜를 받는 tool 은 모두 같은 묶음을 받는다 — 날짜 표현 + 기준(anchor_event / anchor_on).

  1. 기준이 있으면 거기서 센다. anchor_on(보호자가 말한 날짜)이 anchor_event(등록된 일정 이름)
     보다 앞선다. 기준 일정은 정확히 한 건일 때만 쓰고, 아니면 고르지 않고 되묻는다
  2. 기준이 없으면 말한 날(오늘)에서 센다
  3. 계산한 날이 말이 되는지는 쓰는 쪽이 본다 — 관찰은 미래면, 새 일정은 과거면 되묻는다

계산은 datetime_rules 가 하고, 여기서는 기준 일정을 찾고 안내 문구를 붙인다.
"""

from datetime import date, time, timedelta
from typing import Any

from app.agents.common.datetime_rules import (
    DateNeedsAnchor,
    DateNeedsAsking,
    DateParseError,
    DateRange,
    TemporalDirection,
    resolve_date,
    resolve_query_bound,
)
from app.agents.memory.context import AgentContext

# 기준 일정이 여러 건일 때 되물으며 보여 줄 날짜 수
_SHOWN_ANCHOR_DAYS = 3

_ANCHOR_HINT = (
    " 발화에 기준이 되는 일정 이름이 있으면 anchor_event 에, 보호자가 기준 날짜를 말했으면 "
    "anchor_on 에 넣어 다시 부른다. 둘 다 없으면 언제인지 보호자에게 묻는다."
)


def date_remedy(exc: DateParseError) -> str:
    """날짜 실패를 모델이 다음에 할 일로 바꾼다.
    날짜를 받는 tool 은 모두 기준 칸이 있어 문구가 하나다."""
    if isinstance(exc, DateNeedsAnchor):
        return f"{exc}{_ANCHOR_HINT}"
    if isinstance(exc, DateNeedsAsking):
        return str(exc)  # 다른 표현으로 고쳐 부르면 날짜를 지어낸다. 메시지대로 묻는다
    return f"{exc} 오늘·어제·모레·금요일 같은 원문 표현이나 YYYY-MM-DD 로 넣는다."


async def anchor_span(
    context: AgentContext, args: Any, *, direction: TemporalDirection
) -> DateRange | None:
    """args 의 기준(anchor_on · anchor_event)을 기간으로. 기준이 없으면 None.

    관찰은 저장 전에 사람이 확인하지 않아, 잘못 고른 일정은 틀린 날짜로 그대로 남는다.
    그래서 등록된 일정은 정확히 한 건일 때만 쓴다(같은 날 두 건이어도 되묻는다).
    """
    if args.anchor_on is not None:
        day = resolve_date(args.anchor_on, today=context.today, direction=direction)
        return DateRange(start=day, end=day + timedelta(days=1))
    if args.anchor_event is None:
        return None

    name = args.anchor_event.strip()
    if not name:
        # 저장소는 빈 제목을 "조건 없음" 으로 읽는다. 그대로 찾으면 아무 일정이나 기준이 된다
        raise DateParseError("anchor_event 가 비어 있다. 기준이 없으면 빼고 다시 부른다.")
    rows = await context.store.query_events(child_id=context.child_id, title_query=name)
    if not rows:
        raise DateNeedsAsking(
            f"'{name}' 일정이 등록돼 있지 않아 기준 날짜를 모른다. {name}이 언제인지 보호자에게 "
            "묻고, 답을 들으면 그 날짜를 anchor_on 에 넣어 다시 부른다."
        )
    if len(rows) > 1:
        shown = ", ".join(
            row.starts_at.astimezone(context.timezone).date().isoformat()
            for row in rows[:_SHOWN_ANCHOR_DAYS]
        )
        raise DateNeedsAsking(
            f"'{name}' 일정이 여러 건이다({shown}). 어느 {name}인지 보호자에게 묻는다."
        )
    row = rows[0]
    first = row.starts_at.astimezone(context.timezone).date()
    last = first
    if row.ends_at is not None:
        ends = row.ends_at.astimezone(context.timezone)
        last = ends.date()
        if ends.time() == time.min:
            # 자정에 끝나면 그 전날까지다 — 밤 11시~자정 일정의 "다음날" 이 하루 밀리지 않게
            last -= timedelta(days=1)
        last = max(first, last)
    return DateRange(start=first, end=last + timedelta(days=1))


def require_expression(args: Any, *expressions: str | None) -> None:
    """기준만 오고 날짜 표현이 없으면 기준에서 며칠인지 모른다."""
    if (args.anchor_on is not None or args.anchor_event is not None) and not any(expressions):
        raise DateParseError(
            "anchor_event · anchor_on 은 기준이다. "
            "기준에서 며칠인지를 날짜 표현('전날'·'이틀 전')에 함께 넣는다."
        )


async def resolve_day(
    context: AgentContext, expression: str, args: Any, *, direction: TemporalDirection
) -> date:
    """하루가 필요한 자리(관찰 날짜 · 일정 시작일). 기준이 있으면 거기서, 없으면 오늘에서 센다."""
    anchor = await anchor_span(context, args, direction=direction)
    return resolve_date(expression, today=context.today, direction=direction, anchor=anchor)


async def resolve_bounds(
    context: AgentContext, args: Any, *, direction: TemporalDirection
) -> dict[str, date | None]:
    """조회 경계. "지난주" 같은 기간은 그대로 기간으로 쓴다."""
    require_expression(args, args.date_from, args.date_to)
    anchor = await anchor_span(context, args, direction=direction)
    return {
        "date_from": resolve_query_bound(
            args.date_from, today=context.today, direction=direction, is_end=False, anchor=anchor
        ),
        "date_to": resolve_query_bound(
            args.date_to, today=context.today, direction=direction, is_end=True, anchor=anchor
        ),
    }


def check_observed(day: date, today: date) -> None:
    """관찰은 이미 일어난 일이다. 오늘보다 뒤면 기준을 잘못 짚었을 가능성이 커서 저장하지 않는다."""
    if day > today:
        raise DateNeedsAsking(
            f"관찰 날짜가 {day.isoformat()} 로 아직 오지 않은 날이다. 저장하지 않고 "
            "언제였는지 보호자에게 묻는다."
        )


def check_new_event(day: date, today: date, args: Any) -> None:
    """새 일정이 오늘보다 앞이면 오늘이 아니라 다른 일정에서 센 날일 가능성이 크다
    ("소풍 이틀 전에 장보기" 를 오늘 기준으로 풀면 지난 날짜가 된다)."""
    if day >= today:
        return
    message = f"새 일정 날짜가 {day.isoformat()} 로 오늘보다 앞이다."
    if args.anchor_on is None and args.anchor_event is None:
        raise DateNeedsAnchor(message)
    # 기준을 넣었는데도 과거면 기준 자체가 지난 일이다. 다시 넣을 기준이 없으니 묻는다
    raise DateNeedsAsking(f"{message} 언제인지 보호자에게 묻는다.")
