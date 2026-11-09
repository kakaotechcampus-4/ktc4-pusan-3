"""사진 run — 읽은 결과를 보호자가 저장을 누를 때까지 들고 있는다.

🚨 사진 흐름에서 DB 에 쓰는 곳은 `POST /photo-runs/{rid}/commit` 하나다. 여기까지는 메모리에만 있고,
   저장하지 않고 나가면 남는 것이 없다.
🚨 프로세스 메모리다 — run 채널과 같은 --workers 1 전제. 공지 원문이 실려 있어 시간이 지나면 지운다.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.agents import entrypoint
from app.api.runs import sse
from app.api.runs.registry import RunChannel
from app.core import llm_usage
from app.domains.daycare_meal.models import DaycareSlot
from app.domains.daycare_meal.repository import NewDaycareMeal
from app.domains.notice.assemble import NewNoticeItem
from app.domains.notice.models import NoticeItemHandling
from app.rules.menu_key import normalize_menu_key

KST = ZoneInfo("Asia/Seoul")
TTL_SECONDS = 30 * 60
"""보호자가 읽은 내용을 하나씩 확인하는 시간. SSE 채널(5분)보다 길다."""

# 화면이 아는 항목 종류. 일정 · 할 일 · 안내 · 급식
_ENTRY_KIND = {
    NoticeItemHandling.CALENDAR: "event",
    NoticeItemHandling.TODO: "todo",
    NoticeItemHandling.INFO: "info",
}
_SLOTS = {slot.value for slot in DaycareSlot}


@dataclass(frozen=True)
class MealSlot:
    serve_date: date
    slot: DaycareSlot


@dataclass
class PhotoRun:
    run_id: str
    parent_id: uuid.UUID
    child_id: uuid.UUID
    expires_at: float
    reading: entrypoint.PhotoReading | None = None
    items: dict[str, NewNoticeItem] = field(default_factory=dict)
    meals: dict[str, MealSlot] = field(default_factory=dict)
    response: dict | None = None
    """저장이 끝난 응답. 같은 저장을 다시 보내면 두 번 넣지 않고 이 값을 돌려준다."""


_runs: dict[str, PhotoRun] = {}


def _purge() -> None:
    now = time.monotonic()
    for run_id in [k for k, run in _runs.items() if run.expires_at < now]:
        _runs.pop(run_id, None)


def open_photo_run(*, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID) -> PhotoRun:
    _purge()
    run = PhotoRun(run_id, parent_id, child_id, time.monotonic() + TTL_SECONDS)
    _runs[run_id] = run
    return run


def get(run_id: str, *, parent_id: uuid.UUID) -> PhotoRun | None:
    """남의 run 은 없는 run 과 같다 (SSE 와 같은 이유)."""
    _purge()
    run = _runs.get(run_id)
    return run if run is not None and run.parent_id == parent_id else None


def _entry_date(item: NewNoticeItem) -> date | None:
    moment = item.starts_at or item.due_at
    return moment.astimezone(KST).date() if moment else None


def _is_past(item: NewNoticeItem, today: date) -> bool:
    """읽은 시작 · 끝 · 마감이 모두 오늘(한국 날짜)보다 앞인가 (#828). 하나도 못 읽었으면 아니다.

    화면은 이 값으로 지난 일정의 체크를 켜 두지 않는다 — 오늘과 견주는 것은 날짜 계산이라 서버가
    한다 (루트 §2 · apps/web CLAUDE.md §4). 가장 늦은 것으로 본다 — 여러 날 행사가 오늘에 걸쳐
    있거나, 행사는 지났어도 신청 마감이 남았으면 지난 일정이 아니다.
    """
    moments = [m for m in (item.starts_at, item.ends_at, item.due_at) if m is not None]
    return bool(moments) and max(m.astimezone(KST).date() for m in moments) < today


def _item_entry(entry_id: str, item: NewNoticeItem, *, today: date) -> dict:
    entry: dict = {
        "id": entry_id,
        "kind": _ENTRY_KIND[item.handling],
        "title": item.title,
        "date": str(day) if (day := _entry_date(item)) else None,
        # 🚨 위 칸(`date`)의 하루 종일이다 — 시작을 읽었으면 시작의 것, 마감만 읽었으면 마감의 것.
        #    둘을 합치면 시각을 읽은 시작이 마감 때문에 하루 종일로 보이고, 화면이 그 시각을 버린 채
        #    보호자가 못 본 끝 시각을 캘린더에 싣는다 (#723)
        "all_day": item.all_day if item.starts_at is not None else item.due_all_day,
        "items": list(item.supplies),
        "needs_review": item.needs_review,
        "past": _is_past(item, today),
        "label": item.label,
        "target_scope": item.target_scope.value,
        "starts_at": item.starts_at.isoformat() if item.starts_at else None,
        "ends_at": item.ends_at.isoformat() if item.ends_at else None,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "details": [detail.model_dump() for detail in item.details],
        "source_quote": item.source_quote,
    }
    if item.review_reason:
        entry["review_reason"] = item.review_reason
    return entry


def _meal_entries(run: PhotoRun, plan, *, today: date) -> list[dict]:
    entries: list[dict] = []
    for day in plan.days:
        for meal in day.meals:
            # 기관 급식 끼니는 셋이다. 아침 · 저녁은 daycare_meal 에 자리가 없어 싣지 않는다
            if meal.meal_type.value not in _SLOTS or not meal.items:
                continue
            entry_id = f"pe_meal_{day.day}_{meal.meal_type.value}"
            run.meals[entry_id] = MealSlot(plan.date_of(day.day), DaycareSlot(meal.meal_type.value))
            entries.append(
                {
                    "id": entry_id,
                    "kind": "meal",
                    "title": ", ".join(item.name for item in meal.items),
                    "date": str(plan.date_of(day.day)),
                    "all_day": True,
                    "items": [],
                    "needs_review": False,
                    "past": plan.date_of(day.day) < today,
                }
            )
    for index, cell in enumerate(plan.unparsed):
        entries.append(
            {
                "id": f"pe_meal_unread_{index}",
                "kind": "meal",
                "title": cell.raw or "읽지 못한 칸",
                "date": None,
                "all_day": True,
                "items": [],
                "needs_review": True,
                "past": False,
                "review_reason": cell.why or "이 칸은 읽지 못했어요.",
            }
        )
    return entries


def build_entries(run: PhotoRun, reading: entrypoint.PhotoReading, *, today: date) -> list[dict]:
    """읽은 결과 → 화면 항목. 항목마다 id 를 주고, 저장할 때 그 id 로 원래 값을 찾는다.

    `today` 는 사진을 받은 날(한국 날짜)이다 — 항목마다 `past`(지난 일정인가)를 그 날과 견준다.
    """
    entries: list[dict] = []
    for index, item in enumerate(reading.notice.items):
        entry_id = f"pe_{index}"
        run.items[entry_id] = item
        entries.append(_item_entry(entry_id, item, today=today))
    if reading.meal_plan is not None:
        entries.extend(_meal_entries(run, reading.meal_plan, today=today))
    if reading.meal_failed:
        entries.append(
            {
                "id": "pe_meal_unread_0",
                "kind": "meal",
                "title": "식단표",
                "date": None,
                "all_day": True,
                "items": [],
                "needs_review": True,
                "past": False,
                "review_reason": "식단표의 표를 읽지 못했어요. 더 밝게 다시 찍어 주세요.",
            }
        )
    return entries


def photo_job(run: PhotoRun, *, data: bytes, received_at: datetime, declared_lane: str):
    """🚨 `saved` 를 내지 않는다. 한 줄 입력과 갈리는 지점이다 — 저장은 commit 에서만 일어난다."""

    async def run_job(channel: RunChannel) -> None:
        channel.publish(("step", {"index": 1, "total": 2, "label": "사진에서 글자를 찾고 있어요"}))
        try:
            reading = await asyncio.to_thread(entrypoint.read_photo, data, received_at=received_at)
        except entrypoint.UnreadablePhoto:
            channel.publish(sse.failed_event("unparsable", ""))
            return
        run.reading = reading
        entries = build_entries(run, reading, today=received_at.astimezone(KST).date())
        if not entries:
            # 영수증 · 게임 화면처럼 글자는 있는데 기관 문서가 아닌 사진이다. 빈 확인 화면을 보내면
            # 저장 버튼만 꺼진 채 이유를 모른다. "실패는 200 + 빈 배열이 아니다" (계약서 §06)
            channel.publish(sse.failed_event("not_document", ""))
            return
        # 무엇을 찍었는지는 찍은 사람이 안다. 서버가 추측해 덮지 않으므로 고른 값을 되돌린다
        channel.publish(("lane", {"guess": declared_lane, "confidence": 1.0}))
        channel.publish(("step", {"index": 2, "total": 2, "label": "읽은 것을 정리하고 있어요"}))
        channel.publish(
            ("parsed", {"raw_text": reading.notice.raw_text, "entries": entries, "tags": []})
        )
        channel.publish(("done", {"run_id": channel.run_id, "model_calls": 1}))

    async def job(channel: RunChannel) -> None:
        # 공지 읽기와 식단표 읽기가 한 장에서 같이 불린다 (#710).
        # 창구는 하나(photo)고 모델은 role 이 가른다
        with llm_usage.attribute(parent_id=run.parent_id, run_id=channel.run_id, source="photo"):
            await run_job(channel)

    return job


def apply_edits(item: NewNoticeItem, *, title: str, day: date | None, supplies: list[str]):
    """보호자가 고친 값을 올린다. 날짜를 바꾸면 그 날의 시각은 알 수 없어 종일로 둔다.

    날짜를 지웠으면 읽은 날짜가 틀렸다는 뜻이다. 시각까지 같이 비운다.
    """
    current = _entry_date(item)
    changed = {
        "title": title,
        "supplies": tuple(supplies),
        "needs_review": False,
        "review_reason": None,
    }
    if day == current:
        return replace(item, **changed)
    if day is None:
        return replace(
            item,
            **changed,
            starts_at=None,
            ends_at=None,
            all_day=False,
            due_at=None,
            due_all_day=False,
        )
    start = datetime(day.year, day.month, day.day, tzinfo=KST).astimezone(UTC)
    end = datetime(day.year, day.month, day.day, 23, 59, tzinfo=KST).astimezone(UTC)
    # 무엇을 고칠지는 화면이 그 칸을 부르는 이름과 같은 순서로 정한다 — 시작을 읽었으면 시작, 마감만
    # 읽었으면 마감, 둘 다 없으면 종류 (`apps/web/src/lib/photo-entry.ts` 의 `entryDateRole`, #723).
    # 일정 항목이어도 "4월 30일까지" 처럼 마감만 읽었으면 화면은 "마감" 이라고 부른다 —
    # 시작으로 쓰면 옛 마감이 그대로 남아 시작이 마감보다 늦은 항목이 된다
    if item.starts_at is not None or (
        item.due_at is None and item.handling is NoticeItemHandling.CALENDAR
    ):
        return replace(item, **changed, starts_at=start, ends_at=end, all_day=True)
    return replace(item, **changed, due_at=end, due_all_day=True)


def meal_row(slot: MealSlot, *, title: str, day: date | None) -> NewDaycareMeal | None:
    keys = tuple(k for k in (normalize_menu_key(n) for n in title.split(",")) if k)
    if not keys:
        return None
    return NewDaycareMeal(serve_date=day or slot.serve_date, meal_slot=slot.slot, menu_keys=keys)
