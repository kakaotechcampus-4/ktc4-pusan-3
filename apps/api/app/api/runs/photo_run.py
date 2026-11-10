"""사진 run — 읽은 결과를 보호자가 저장을 누를 때까지 들고 있는다.

🚨 사진 흐름에서 기록(공지 · 급식)을 쓰는 곳은 `POST /photo-runs/{rid}/commit` 하나다. 읽은 결과는
   그때까지 보관함 표(`run_hold`, UNLOGGED · 백업 제외, #877 2단계)에 30분 있고, 저장하지 않고
   나가면 만료 뒤 `hold_purge` 가 지운다. 워커가 여럿이어도 저장 요청이 같은 결과를 본다.
🚨 공지 원문이 실려 있다. 아이를 지우면 FK CASCADE 로 함께 지워진다 (#751).
🚨 저장은 행을 잠근(`lock`) 트랜잭션 안에서 한다 — 같은 저장이 두 워커에 동시에 와도 뒤 요청은
   앞 요청이 끝난 뒤 남긴 응답(`response`)을 본다.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import entrypoint
from app.api.deps.db import background_session
from app.api.runs import sse
from app.api.runs.registry import RunChannel
from app.api.v1.schemas.photos import DATE_WINDOW_YEARS, MAX_COMMIT_ENTRIES, PhotoEntry
from app.core import llm_usage
from app.core.event_draft import MAX_ITEM_NAME
from app.domains.daycare_meal.models import DaycareSlot
from app.domains.daycare_meal.repository import NewDaycareMeal
from app.domains.notice.assemble import NewNoticeItem
from app.domains.notice.models import NoticeItemHandling
from app.domains.run_hold.models import HoldKind
from app.domains.run_hold.repository import (
    get_hold,
    lock_hold,
    put_hold,
    set_payload,
    share_holder_child,
)
from app.rules.menu_key import normalize_menu_key

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# 사진 한 장을 읽는 데 기다리는 상한. 넘으면 failed(timeout) 으로 끝낸다 — 한 줄 입력의 60초
# 안전망(runner.RUN_DEADLINE_SECONDS)은 사진 job 에 걸리지 않아서 따로 둔다.
#   근거 (2026-11-10 실측 · docs/notice/notice-ocr-v1.md) — 급식표 한 장(27일치)을 운영 모델로
#   3번 읽으니 전부 성공했고 합계 119 · 131 · 218초였다. 공지 읽기 43~62초에 식단표 읽기
#   57~171초가 붙는다. 🚨 그래서 180초로는 정상인 급식표가 잘린다. 실측 최대보다 넉넉히 둔다.
#   3번만 쟀고 동시에 돌렸다 — 정상인 급식표가 잘리면 이 값을 올린다.
# 🚨 스레드는 밖에서 끊지 못한다. 그래서 식단표의 표 읽기에는 기한을 넘겨 스스로 멈추게 한다
#    (아래 PHOTO_MEAL_MARGIN_SECONDS — 기한이 지나면 받던 응답을 끊고 연결을 닫는다).
#    공지 읽기에는 기한이 없다. 여기서 실패를 내도 그 호출은 SDK 시간 상한(120초, 재시도 1회)
#    까지 스레드에서 더 돌 수 있고 결과는 버린다.
PHOTO_READ_DEADLINE_SECONDS = 300.0
# 식단표의 표 읽기는 전체 상한보다 이만큼 먼저 끊는다. 그러면 timeout 으로 다 버리지 않고
# 이미 읽은 공지 항목을 낸다 (시간을 쓰는 쪽이 표 읽기다 — 실측 57~171초).
PHOTO_MEAL_MARGIN_SECONDS = 20.0
TTL = timedelta(minutes=30)
"""보호자가 읽은 내용을 하나씩 확인하는 시간. SSE 채널(5분)보다 길다."""
_KIND = HoldKind.PHOTO_RUN

_session_factory: Callable[[], AsyncSession] = background_session

# 화면이 아는 항목 종류. 일정 · 할 일 · 안내 · 급식
_ENTRY_KIND = {
    NoticeItemHandling.CALENDAR: "event",
    NoticeItemHandling.TODO: "todo",
    NoticeItemHandling.INFO: "info",
}
_SLOTS = {slot.value for slot in DaycareSlot}

_MEAL_UNREAD = "식단표의 표를 읽지 못했어요. 더 밝게 다시 찍어 주세요."
# 시간이 모자라 못 읽었다. 사진 탓이 아니라서 다시 찍으라고 하지 않는다
_MEAL_TIMED_OUT = (
    "식단표의 표를 읽는 데 시간이 오래 걸려 끝내지 못했어요. 잠시 뒤 다시 올려 주세요."
)
# 연도를 몰라 급식을 내보내지 않았다 (`rules/meal_plan_month` ③). 다시 찍어도 연도는 생기지 않는다
_MEAL_NO_YEAR = (
    "급식표에 연도가 적혀 있지 않아 언제 것인지 알 수 없어요. {month}월 급식은 저장하지 않아요."
)


@dataclass(frozen=True)
class MealSlot:
    serve_date: date
    slot: DaycareSlot


@dataclass
class PhotoRun:
    run_id: str
    parent_id: uuid.UUID
    child_id: uuid.UUID
    reading: entrypoint.PhotoReading | None = None
    items: dict[str, NewNoticeItem] = field(default_factory=dict)
    meals: dict[str, MealSlot] = field(default_factory=dict)
    response: dict | None = None
    """저장이 끝난 응답. 같은 저장을 다시 보내면 두 번 넣지 않고 이 값을 돌려준다."""


_RUN = TypeAdapter(PhotoRun)


def _dump(run: PhotoRun) -> dict:
    return _RUN.dump_python(run, mode="json")


def _load(payload: dict) -> PhotoRun:
    return _RUN.validate_python(payload)


def _readable(run_id: str, payload: dict) -> PhotoRun | None:
    """못 읽으면(PhotoRun 모양을 바꾼 배포 뒤의 옛 행) None — 없는 run 과 같은 404 로 끝난다.

    🚨 검증 오류의 글에 공지 원문이 실릴 수 있어 예외 종류만 남긴다 (#899 리뷰).
       행은 30분 뒤 만료된다.
    """
    try:
        return _load(payload)
    except Exception as exc:
        log.warning("사진 run %s 을 읽지 못했다 — %s", run_id, type(exc).__name__)
        return None


def open_photo_run(*, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID) -> PhotoRun:
    """빈 run 을 만든다. 표에는 읽기가 끝난 뒤 `keep` 이 넣는다 — 그 전 저장 요청은 404 다."""
    return PhotoRun(run_id, parent_id, child_id)


async def keep(run: PhotoRun, *, now: datetime | None = None) -> bool:
    """읽은 결과를 30분 넣는다. 아이가 그 사이 지워졌으면 남기지 않고 False."""
    async with _session_factory() as session:
        try:
            stored = await put_hold(
                session,
                kind=_KIND,
                key=run.run_id,
                parent_id=run.parent_id,
                child_id=run.child_id,
                payload=_dump(run),
                ttl=TTL,
                now=now,
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            stored = False
    if not stored:
        log.warning("사진 run %s 을 넣지 않았다 — 보호자 · 아이가 없거나 끊겼다", run.run_id)
    return stored


async def get(run_id: str, *, parent_id: uuid.UUID, now: datetime | None = None) -> PhotoRun | None:
    """남의 run 은 없는 run 과 같다 (SSE 와 같은 이유)."""
    async with _session_factory() as session:
        payload = await get_hold(
            session, kind=_KIND, key=run_id, parent_id=parent_id, child_id=None, now=now
        )
    return None if payload is None else _readable(run_id, payload)


async def lock(session: AsyncSession, run_id: str, *, parent_id: uuid.UUID) -> PhotoRun | None:
    """저장 요청의 트랜잭션에서 그 run 을 잡는다.

    같은 run 을 잡으려는 다른 요청은 이 트랜잭션이 commit 할 때까지 기다린다.
    🚨 아이 행을 먼저(`FOR KEY SHARE`) 잡고 이 행을 잡는다 — 아이 삭제와 같은 잠금 순서라 겹쳐도
       교착이 없다. 삭제가 먼저면 기다렸다가 아이가 없어 None(404)이다.
    """
    if not await share_holder_child(session, kind=_KIND, key=run_id, parent_id=parent_id):
        return None
    row = await lock_hold(session, kind=_KIND, key=run_id, parent_id=parent_id)
    return None if row is None else _readable(run_id, row.payload)


async def finish(session: AsyncSession, run: PhotoRun, response: dict) -> None:
    """저장 응답을 남긴다. `lock` 한 같은 트랜잭션에서 부르고, 기록과 함께 commit 한다."""
    run.response = response
    await set_payload(session, kind=_KIND, key=run.run_id, payload=_dump(run))


def _entry_date(item: NewNoticeItem) -> date | None:
    moment = item.starts_at or item.due_at
    return moment.astimezone(KST).date() if moment else None


def _is_past(item: NewNoticeItem, today: date) -> bool:
    """지난 항목인가 — 견줄 날이 모두 오늘(한국 날짜)보다 앞인가 (#828). 하나도 못 읽었으면 아니다.

    화면은 이 값으로 지난 일정의 체크를 켜 두지 않는다 — 오늘과 견주는 것은 날짜 계산이라 서버가
    한다 (루트 §2 · apps/web CLAUDE.md §4).
    🚨 **일정은 화면이 캘린더에 넣는 날로 본다** (web `photo-entry.ts` 의 `eventBody`).
       시작을 읽었으면 시작 · 끝 중 늦은 것 — 여러 날 행사가 오늘에 걸쳐 있으면 지난 일정이
       아니다. 시작이 있는데 마감까지 보면 "행사 11-08 · 신청 11-10 까지" 가 켜진 채로 시작해,
       누르면 이미 지난 11-08 일정이 들어간다 (#854 리뷰). 시작이 없으면 마감이 지났는가다 —
       그때 넣는 날은 보호자가 고른다.
    🚨 **할 일 · 안내는 마감까지 본다** — 시작 · 끝 · 마감 중 가장 늦은 것. "신청 11-05 ~ 11-12" 는
       기간이 열렸어도 마감이 남았으면 아직 할 수 있다 (#903 리뷰).
    """
    if item.handling is not NoticeItemHandling.CALENDAR:
        moments = [m for m in (item.starts_at, item.ends_at, item.due_at) if m is not None]
    elif item.starts_at is not None:
        moments = [m for m in (item.starts_at, item.ends_at) if m is not None]
    else:
        moments = [item.due_at] if item.due_at is not None else []
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
    # (원문, 이유, 어디) — 모델이 못 읽었다고 한 칸 뒤에 이름이 하나도 없는 끼니를
    # 같은 모양으로 붙인다
    unread = [
        (cell.raw, cell.why, _where(plan, cell.day, cell.meal_type)) for cell in plan.unparsed
    ]
    for day in plan.days:
        for meal in day.meals:
            # 기관 급식 끼니는 셋이다. 아침 · 저녁은 daycare_meal 에 자리가 없어 싣지 않는다
            if meal.meal_type.value not in _SLOTS or not meal.items:
                continue
            entry_id = f"pe_meal_{day.day}_{meal.meal_type.value}"
            if entry_id in run.meals:
                # 한 날에 같은 끼니가 두 번 왔다. 같은 id 두 개를 내보내면 저장 전체가 "같은 항목을
                # 두 번 보냈어요" 로 막힌다 — 합치기의 같은 날 규칙처럼 앞의 것을 남긴다
                unread.append(
                    (
                        ", ".join(item.raw for item in meal.items),
                        "같은 날 같은 끼니가 두 번 읽혀 앞의 것만 남겼어요.",
                        _where(plan, day.day, meal.meal_type),
                    )
                )
                continue
            if not any(item.name for item in meal.items):
                # 원문이 번호뿐이라 이름이 전부 비었다. 빈 제목으로 내보내면 저장이 422 로 막힌다 —
                # 조용히 빼지 않고 못 읽은 칸으로 확인을 받는다
                unread.append(
                    (
                        ", ".join(item.raw for item in meal.items),
                        None,
                        _where(plan, day.day, meal.meal_type),
                    )
                )
                continue
            run.meals[entry_id] = MealSlot(plan.date_of(day.day), DaycareSlot(meal.meal_type.value))
            entries.append(
                {
                    "id": entry_id,
                    "kind": "meal",
                    # 원문이 번호뿐이면 이름이 비어 있다(#908) — 빈 조각을 잇지 않는다
                    "title": ", ".join(item.name for item in meal.items if item.name),
                    "date": str(plan.date_of(day.day)),
                    "all_day": True,
                    "items": [],
                    "needs_review": False,
                    "past": plan.date_of(day.day) < today,
                }
            )
    for index, (raw, why, where) in enumerate(unread):
        reason = why or "이 칸은 읽지 못했어요."
        entries.append(
            {
                "id": f"pe_meal_unread_{index}",
                "kind": "meal",
                "title": raw or "읽지 못한 칸",
                "date": None,
                "all_day": True,
                "items": [],
                "needs_review": True,
                "past": False,
                # 어느 날 어느 끼니인지 보여야 보호자가 종이에서 찾는다.
                # 🚨 날짜 칸(`date`)은 비운다 — 저장하지 않는 칸이고(commit 이
                #    `pe_meal_unread_` 를 건너뛴다), 의심스러운 날일 수 있다
                "review_reason": f"{where} — {reason}" if where else reason,
            }
        )
    return entries


_SLOT_NAME = {
    "breakfast": "아침",
    "snack_am": "오전 간식",
    "lunch": "점심",
    "snack_pm": "오후 간식",
    "dinner": "저녁",
}


def _where(plan, day: int | None, meal_type) -> str | None:
    """ "11월 9일 오후 간식" — 그 달에 있는 날일 때만. 없는 날("11월 31일")은 말하지 않는다."""
    if day is None:
        return None
    try:
        served = plan.date_of(day)
    except ValueError:
        return None
    where = f"{served.month}월 {served.day}일"
    slot = _SLOT_NAME.get(meal_type.value) if meal_type is not None else None
    return f"{where} {slot}" if slot else where


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
        for thin in reading.meal_thin:
            # 화면 줄은 못 읽은 칸으로 이미 들어 있다(`agents/notice_ocr.flag_thin_ranges`).
            # 숫자만 남긴다 — 메뉴 글자는 남기지 않는다 (CLAUDE.md §2 개인정보)
            log.warning(
                "사진 run %s 급식 %d일~%d일 구간이 덜 읽혔을 수 있다 — 읽은 날 %d · 평일 %d",
                run.run_id,
                thin.first,
                thin.last,
                thin.read_days,
                thin.weekdays,
            )
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
                "review_reason": _meal_failed_reason(reading),
            }
        )
    return _servable(run, entries)


def _meal_failed_reason(reading: entrypoint.PhotoReading) -> str:
    if reading.meal_timed_out:
        return _MEAL_TIMED_OUT
    month = reading.meal_month
    if month is not None and month.year is None:
        return _MEAL_NO_YEAR.format(month=month.month)
    return _MEAL_UNREAD


# ── 화면 항목은 저장 검증을 통과한다 (2026-11-11 운영 장애) ─────────────────────
# 읽은 항목은 검증 없이 화면에 나가고 저장 때 `PhotoEntry` 로 검증된다. 한 건이 걸리면
# 저장 전체가 400 이었다 — "잘 읽었어요" 를 보고 저장을 눌렀는데 "저장하지 못했어요".
# 그래서 내보내기 전에 저장과 **같은 스키마**로 미리 검사한다(규칙을 두 벌 두지 않는다).

UNREAD_PREFIXES = ("pe_meal_unread_", "pe_unread_")
"""저장하지 않는 "읽지 못한 칸" 의 id. commit 은 이 항목을 받아도 건너뛴다."""
_MAX_TITLE = PhotoEntry.model_fields["title"].metadata[0].max_length
_MAX_ITEMS = PhotoEntry.model_fields["items"].metadata[0].max_length
_UNTITLED = "제목을 읽지 못한 항목"
_REVIEW_DATE = (
    f"날짜를 확인해 주세요. 읽은 날짜가 저장할 수 있는 범위(앞뒤 {DATE_WINDOW_YEARS}년) 밖이에요."
)
_REVIEW_TITLE = "무엇인지 확인해 주세요. 제목을 읽지 못했어요."
_REVIEW_LONG = "내용이 길어 앞부분만 담았어요. 확인해 주세요."
_REVIEW_ITEMS = "준비물을 확인해 주세요. 너무 길거나 많아서 줄였어요."
_REVIEW_TOO_MANY = f"한 번에 {MAX_COMMIT_ENTRIES}건까지 저장할 수 있어요. 이 항목은 확인해 주세요."
# 저장 때 쓰지 않는 보여 주기 칸(`PhotoEntry` docstring). 걸리면 비운다 — 저장 값은 run 에 있다
_DISPLAY_DEFAULTS: dict = {
    "label": None,
    "target_scope": None,
    "starts_at": None,
    "ends_at": None,
    "due_at": None,
    "details": [],
    "source_quote": None,
    "all_day": True,
    "past": False,
    "review_reason": None,
}


def _commit_errors(entry: dict) -> list[str]:
    """저장 때와 같은 검증에 걸리는 칸 이름. 웹은 항목 그대로에 `event_id` 를 더해 보낸다."""
    try:
        PhotoEntry.model_validate({**entry, "event_id": None})
    except ValidationError as e:
        return sorted({str(err["loc"][0]) for err in e.errors() if err["loc"]})
    return []


def _cut_meal_title(title: str) -> str:
    """메뉴 경계에서 자른다 — 이름 한가운데서 자르면 반쪽 이름이 저장 키가 된다(`meal_row`)."""
    kept: list[str] = []
    for name in title.split(", "):
        if len(", ".join([*kept, name])) > _MAX_TITLE:
            break
        kept.append(name)
    return ", ".join(kept) or title[:_MAX_TITLE]


def _fix(entry: dict, fields: list[str]) -> dict:
    """고칠 수 있는 칸은 고친다.

    내용을 바꿨으면 확인을 받는다 — 보호자가 보기 전에 저장되지 않는다.
    """
    fixed = dict(entry)
    reasons: list[str] = []
    if "date" in fields:
        fixed["date"] = None
        reasons.append(_REVIEW_DATE)
    if "title" in fields:
        title = entry.get("title") if isinstance(entry.get("title"), str) else ""
        title = title.strip()
        if not title:
            fixed["title"] = (entry.get("label") or "").strip() or _UNTITLED
            reasons.append(_REVIEW_TITLE)
        else:
            cut = _cut_meal_title(title) if entry.get("kind") == "meal" else title[:_MAX_TITLE]
            fixed["title"] = cut
            reasons.append(_REVIEW_LONG)
    if "items" in fields:
        raw = entry.get("items") if isinstance(entry.get("items"), list) else []
        names = [n.strip() for n in raw if isinstance(n, str) and n.strip()]
        fixed["items"] = [n[:MAX_ITEM_NAME] for n in names][:_MAX_ITEMS]
        if fixed["items"] != names:
            reasons.append(_REVIEW_ITEMS)  # 빈 칸만 뺐으면 내용이 그대로라 묻지 않는다
    for name in fields:
        if name in _DISPLAY_DEFAULTS:
            fixed[name] = _DISPLAY_DEFAULTS[name]
    if reasons:
        prior = fixed.get("review_reason")
        fixed["needs_review"] = True
        fixed["review_reason"] = " ".join(dict.fromkeys([*([prior] if prior else []), *reasons]))
    return fixed


def _unread(entry_id: str, *, kind: str) -> dict:
    return {
        "id": entry_id,
        "kind": kind,
        "title": "읽지 못한 칸" if kind == "meal" else "읽지 못한 항목",
        "date": None,
        "all_day": True,
        "items": [],
        "needs_review": True,
        "past": False,
        "review_reason": "이 항목은 읽지 못했어요.",
    }


def _servable(run: PhotoRun, entries: list[dict]) -> list[dict]:
    """🚨 화면에 나가는 항목은 저장 검증을 반드시 통과한다 — 웹이 그대로 되돌려 보내도 400 이 없다.

    - 걸린 칸은 고친다: 날짜는 비우고, 제목 · 준비물은 자르거나 빈 것을 뺀다. 고쳤으면 확인을
      받는다. 확인이 필요한 항목은 보호자가 시트에서 확인하기 전에는 저장 대상이 아니다
      (web `photo-review`).
    - 🚨 날짜가 걸린 급식은 읽지 못한 칸으로 돌린다. 날짜를 비운 급식을 확인하면 commit 이 run 의
      날짜(`MealSlot.serve_date`, 범위 밖)로 저장하기 때문이다.
    - 그래도 걸리면 그 항목은 "읽지 못한 것" 으로만 보이고 저장 대상에서 빠진다(전체를 막지 않는다).
    - 🚨 로그에는 항목 id 와 칸 이름만 남긴다. 원문 · 제목 글자는 남기지 않는다 (§2 개인정보).
    """
    meal_unread = sum(1 for e in entries if e["id"].startswith("pe_meal_unread_"))
    other_unread = 0
    out: list[dict] = []
    for entry in entries:
        fields = _commit_errors(entry)
        if not fields:
            out.append(entry)
            continue
        entry_id = entry["id"]
        log.warning(
            "사진 run %s 항목 %s 이 저장 검사에 걸렸다 — 칸 %s",
            run.run_id,
            entry_id,
            ",".join(fields),
        )
        fixed = _fix(entry, fields)
        is_meal = entry_id in run.meals
        if _commit_errors(fixed) or (is_meal and fixed["date"] is None):
            run.items.pop(entry_id, None)
            run.meals.pop(entry_id, None)
            kind = "meal" if entry.get("kind") == "meal" else "info"
            if kind == "meal":
                new_id, meal_unread = f"pe_meal_unread_{meal_unread}", meal_unread + 1
            else:
                new_id, other_unread = f"pe_unread_{other_unread}", other_unread + 1
            if is_meal and not _commit_errors(fixed):
                # 날짜만 걸린 급식 — 무엇이었는지는 보이게 둔다
                fixed = {**fixed, "id": new_id, "date": None, "needs_review": True}
            else:
                fixed = _unread(new_id, kind=kind)
        out.append(fixed)
    return _within_commit_size(run, out)


def _within_commit_size(run: PhotoRun, entries: list[dict]) -> list[dict]:
    """확인한 항목이 한 번에 저장할 수 있는 수를 넘으면 넘는 것은 확인을 받는다.

    그대로 보내면 400 이다 (`PhotoCommitRequest.entries` 상한).
    """
    checked = [e for e in entries if not e["needs_review"]]
    if len(checked) <= MAX_COMMIT_ENTRIES:
        return entries
    over = {id(e) for e in checked[MAX_COMMIT_ENTRIES:]}
    log.warning(
        "사진 run %s 확인한 항목 %d건 — %d건을 확인 필요로 돌렸다",
        run.run_id,
        len(checked),
        len(over),
    )
    return [
        {**e, "needs_review": True, "review_reason": _REVIEW_TOO_MANY} if id(e) in over else e
        for e in entries
    ]


def photo_job(run: PhotoRun, *, data: bytes, received_at: datetime, declared_lane: str):
    """🚨 `saved` 를 내지 않는다. 한 줄 입력과 갈리는 지점이다 — 저장은 commit 에서만 일어난다."""

    async def run_job(channel: RunChannel) -> None:
        channel.publish(("step", {"index": 1, "total": 2, "label": "사진에서 글자를 찾고 있어요"}))
        try:
            async with asyncio.timeout(PHOTO_READ_DEADLINE_SECONDS):
                reading = await asyncio.to_thread(
                    entrypoint.read_photo,
                    data,
                    received_at=received_at,
                    meal_deadline=time.monotonic()
                    + PHOTO_READ_DEADLINE_SECONDS
                    - PHOTO_MEAL_MARGIN_SECONDS,
                )
        except entrypoint.UnreadablePhoto:
            channel.publish(sse.failed_event("unparsable", ""))
            return
        except TimeoutError:
            log.warning(
                "사진 run %s 읽기가 %s초 안에 끝나지 않아 끊었다",
                channel.run_id,
                PHOTO_READ_DEADLINE_SECONDS,
            )
            channel.publish(sse.failed_event("timeout", ""))
            return
        run.reading = reading
        entries = build_entries(run, reading, today=received_at.astimezone(KST).date())
        if entries:
            # 화면이 확인 화면을 받는 순간 저장을 누를 수 있다 — 그 전에 표에 넣는다
            await keep(run)
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
