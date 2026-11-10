"""기관 공지 조회 tool (#832). 읽기만 한다 — 고치기 · 지우기 · 저장은 없다.

store 가 식별자 없는 `NoticeRow` 를 주고, 여기서 모델이 읽을 값으로 바꾼다.
  · enum 은 한국어 라벨로, 날짜 · 요일은 코드가 `context.timezone` 으로 계산한다
    (모델에게 UTC ISO 를 주지 않는다 — 루트 §2 · §3).
  · `context.found` 에 넣지 않는다. 조회 카드가 생기지 않고 답은 일반 응답 문장으로 나간다.
  · 원문(`raw_text`)은 모델이 내용을 찾으려고 읽는 자료다. 사용자에게 그대로 옮기지 않는다는
    규칙은 프롬프트(`prompt._NOTICE`)가 말한다.
로그에는 제목 · 원문 · 항목 글을 남기지 않는다.
"""

from datetime import date, datetime, tzinfo
from typing import Any

from app.agents.common.datetime_rules import DateParseError
from app.agents.memory.context import AgentContext
from app.agents.memory.result import ErrorCode, ToolResult, fail, ok
from app.agents.memory.schemas.notice import NoticeQuery
from app.agents.memory.store.ports import NoticeItemRow, NoticeRow
from app.agents.memory.tools.dates import date_remedy, resolve_bounds

NOTICE = "notice"
# 한 번에 5건. 원문이 늘 같이 가서 관찰 조회 상한(40건)은 입력이 너무 크다
MAX_NOTICES = 5

_NOTICE_CUT = (
    f"최근 받은 공지 {MAX_NOTICES}건만 돌려줬다. 더 있지만 보이지 않는다. "
    "알릴 때는 최근 공지까지만 봤다고 말하고, 찾는 공지가 없으면 기간이나 낱말로 좁혀 달라고 한다."
)

# 키는 `domains/notice/models.py` enum 의 문자열 값이다 (Agent 는 domains 를 import 하지 않는다).
# enum 이 늘면 테스트(test_라벨이_도메인_enum_을_모두_덮는다)가 깨져 여기를 고치게 한다
DOC_KIND_LABELS = {
    "daily_note": "알림장",
    "newsletter": "가정통신문",
    "meal_plan": "식단표",
    "schedule": "일정표",
    "message": "문자",
    "other": "기타",
}
INPUT_FORMAT_LABELS = {
    "image": "사진",
    "pdf": "PDF",
    "hwp": "한글 파일",
    "hwpx": "한글 파일",
    "xlsx": "엑셀 파일",
    "text": "글",
}
AUDIENCE_LABELS = {
    "child": "우리 아이",
    "class": "반 전체",
    "institution": "기관 전체",
    "unknown": "대상 모름",
}
RELATION_LABELS = {"correction": "정정 공지", "follow_up": "후속 공지"}
HANDLING_LABELS = {"calendar": "일정", "todo": "할 일", "info": "안내"}
TARGET_LABELS = {
    "child": "우리 아이",
    "class": "반 전체",
    "all": "전체",
    "conditional": "해당자만",
    "unknown": "대상 모름",
}

_WEEKDAYS = "월화수목금토일"


def _label(labels: dict[str, str], value: str) -> str:
    """표에 없는 값은 원래 문자열 그대로 둔다."""
    return labels.get(value, value)


def _day(value: date) -> str:
    return f"{value.isoformat()}({_WEEKDAYS[value.weekday()]})"


def _when(value: datetime | None, *, all_day: bool, tz: tzinfo) -> str | None:
    """`YYYY-MM-DD(요)` (종일) 또는 `YYYY-MM-DD(요) HH:MM`. 없으면 None."""
    if value is None:
        return None
    local = value.astimezone(tz)
    return _day(local.date()) if all_day else f"{_day(local.date())} {local:%H:%M}"


def _item(item: NoticeItemRow, tz: tzinfo) -> dict[str, Any]:
    return {
        "handling": _label(HANDLING_LABELS, item.handling),
        "target_scope": _label(TARGET_LABELS, item.target_scope),
        "label": item.label,
        "title": item.title,
        "source_quote": item.source_quote,
        "supplies": list(item.supplies),
        "details": [{"name": name, "value": value} for name, value in item.details],
        "starts_at": _when(item.starts_at, all_day=item.all_day, tz=tz),
        "ends_at": _when(item.ends_at, all_day=item.all_day, tz=tz),
        "due_at": _when(item.due_at, all_day=item.due_all_day, tz=tz),
        "done_at": _when(item.done_at, all_day=False, tz=tz),
    }


def _notice(row: NoticeRow, tz: tzinfo) -> dict[str, Any]:
    return {
        "doc_kind": _label(DOC_KIND_LABELS, row.doc_kind),
        "input_format": _label(INPUT_FORMAT_LABELS, row.input_format),
        "audience_scope": _label(AUDIENCE_LABELS, row.audience_scope),
        "relation": _label(RELATION_LABELS, row.relation) if row.relation else None,
        "title": row.title,
        "raw_text": row.raw_text,
        "issued_on": _day(row.issued_on) if row.issued_on else None,
        "received_at": _when(row.received_at, all_day=False, tz=tz),
        "items": [_item(item, tz) for item in row.items],
    }


async def query_notice(context: AgentContext, args: NoticeQuery) -> ToolResult:
    try:
        bounds = await resolve_bounds(context, args, direction=args.temporal_direction)
    except DateParseError as exc:
        return fail("query", NOTICE, ErrorCode.DATE_UNPARSEABLE, date_remedy(exc))

    rows = await context.store.query_notices(
        child_id=context.child_id,
        text_query=args.text_query,
        doc_kind=args.doc_kind,
        limit=MAX_NOTICES + 1,  # 한 건 더 받아 잘렸는지 안다
        **bounds,
    )
    kept, truncated = rows[:MAX_NOTICES], len(rows) > MAX_NOTICES
    return ok(
        "query",
        NOTICE,
        count=len(kept),
        notices=[_notice(row, context.timezone) for row in kept],
        **({"truncated": True, "notice": _NOTICE_CUT} if truncated else {}),
    )


NOTICE_HANDLERS = {"query_notice": query_notice}
