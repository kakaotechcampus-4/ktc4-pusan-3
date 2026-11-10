"""사진 확인 화면에서 고친 날짜 — app/api/runs/photo_run.py 의 apply_edits (#723)

화면(`apps/web/src/lib/photo-entry.ts` 의 `entryDateRole`)과 같은 순서로 무엇을 고칠지 정한다 —
시작을 읽었으면 시작, 마감만 읽었으면 마감, 둘 다 없으면 종류(일정은 시작 · 그 밖은 마감).
"""

from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.api.runs.photo_run import apply_edits
from app.domains.notice.assemble import NewNoticeItem
from app.domains.notice.models import NoticeItemHandling, NoticeItemTarget

KST = ZoneInfo("Asia/Seoul")
EDITED = date(2026, 5, 2)


def _item(
    handling: NoticeItemHandling,
    *,
    starts_at: datetime | None = None,
    due_at: datetime | None = None,
) -> NewNoticeItem:
    return NewNoticeItem(
        handling=handling,
        label="행사",
        title="체험 신청",
        target_scope=NoticeItemTarget.CLASS,
        starts_at=starts_at,
        ends_at=None,
        all_day=starts_at is None,
        due_at=due_at,
        due_all_day=due_at is not None,
        details=(),
        supplies=(),
        source_quote="",
        needs_review=True,
        review_reason=None,
    )


def _day(moment: datetime | None) -> date | None:
    return moment.astimezone(KST).date() if moment else None


def _edit(item: NewNoticeItem) -> NewNoticeItem:
    return apply_edits(item, title=item.title, day=EDITED, supplies=[])


def test_마감만_읽은_일정_항목은_날짜를_고치면_마감을_고치고_시작은_비워_둔다() -> None:
    # "4월 30일까지" — assemble 이 시작 없는 "까지" 를 일정 항목에서도 마감으로 옮긴 경우.
    # 화면은 이 칸을 "마감" 이라고 부르므로, 고친 날이 시작으로 들어가면 시작이 옛 마감보다 늦어진다
    item = _item(NoticeItemHandling.CALENDAR, due_at=datetime(2026, 4, 30, 23, 59, tzinfo=KST))

    edited = _edit(item)

    assert edited.starts_at is None
    assert _day(edited.due_at) == EDITED
    assert edited.due_all_day is True


@pytest.mark.parametrize("handling", [NoticeItemHandling.CALENDAR, NoticeItemHandling.TODO])
def test_시작을_읽은_항목은_종류와_상관없이_시작을_그날_하루로_고친다(
    handling: NoticeItemHandling,
) -> None:
    item = _item(handling, starts_at=datetime(2026, 4, 28, 9, 30, tzinfo=KST))

    edited = _edit(item)

    assert _day(edited.starts_at) == EDITED
    assert _day(edited.ends_at) == EDITED
    assert edited.all_day is True
    assert edited.due_at is None


@pytest.mark.parametrize(
    ("handling", "field"),
    [(NoticeItemHandling.CALENDAR, "starts_at"), (NoticeItemHandling.TODO, "due_at")],
)
def test_날짜를_못_읽은_항목에_날짜를_넣으면_일정은_시작_할_일은_마감이다(
    handling: NoticeItemHandling, field: str
) -> None:
    edited = _edit(_item(handling))

    assert _day(getattr(edited, field)) == EDITED
    other = "due_at" if field == "starts_at" else "starts_at"
    assert getattr(edited, other) is None


# ── 고친 시각 — 보호자가 시트에서 시작 시간 · 하루 종일을 바꾸면 기록에도 그 값이 들어간다 ──────
# 캘린더에는 고친 값이 들어가는데 저장되는 기록에는 사진에서 읽은 시각이 남던 것을 막는다.
READ = datetime(2026, 4, 28, 8, 0, tzinfo=KST)


def test_같은_날의_시각을_고치면_기록의_시작도_고친_시각이다() -> None:
    # 저녁 8시를 오전 8시로 읽은 것을 보호자가 고쳤다
    item = _item(NoticeItemHandling.CALENDAR, starts_at=READ)
    fixed = datetime(2026, 4, 28, 20, 0, tzinfo=KST)

    edited = apply_edits(
        item, title=item.title, day=date(2026, 4, 28), supplies=[], starts_at=fixed, all_day=False
    )

    assert edited.starts_at == fixed
    assert edited.all_day is False
    assert edited.ends_at is None


def test_날짜와_시각을_함께_고치면_하루_종일로_두지_않고_그_시각으로_저장한다() -> None:
    item = _item(NoticeItemHandling.CALENDAR, starts_at=READ)
    fixed = datetime(2026, 5, 2, 20, 0, tzinfo=KST)

    edited = apply_edits(
        item, title=item.title, day=EDITED, supplies=[], starts_at=fixed, all_day=False
    )

    assert edited.starts_at == fixed
    assert edited.all_day is False


def _timed(day: date, *, ends_at: datetime | None) -> NewNoticeItem:
    """8시에 시작하고 `ends_at` 에 끝난다고 사진에서 읽은 일정."""
    return replace(_item(NoticeItemHandling.CALENDAR, starts_at=READ), ends_at=ends_at)


def test_읽은_끝이_고친_시작보다_뒤면_함께_저장한다() -> None:
    read_end = datetime(2026, 4, 28, 21, 0, tzinfo=KST)
    item = _timed(date(2026, 4, 28), ends_at=read_end)

    edited = apply_edits(
        item,
        title=item.title,
        day=date(2026, 4, 28),
        supplies=[],
        starts_at=datetime(2026, 4, 28, 20, 0, tzinfo=KST),
        ends_at=read_end,
        all_day=False,
    )

    assert edited.ends_at == read_end


def test_읽은_끝이_고친_시작보다_앞이면_끝을_비운다() -> None:
    read_end = datetime(2026, 4, 28, 9, 0, tzinfo=KST)
    item = _timed(date(2026, 4, 28), ends_at=read_end)

    edited = apply_edits(
        item,
        title=item.title,
        day=date(2026, 4, 28),
        supplies=[],
        starts_at=datetime(2026, 4, 28, 20, 0, tzinfo=KST),
        ends_at=read_end,
        all_day=False,
    )

    assert edited.ends_at is None


@pytest.mark.parametrize(
    ("starts_at", "ends_at"),
    [
        # 시작을 고치고 끝을 먼 뒷날로 보냈다
        (datetime(2026, 4, 28, 20, 0, tzinfo=KST), datetime(2029, 1, 1, 0, 0, tzinfo=KST)),
        # 시작은 읽은 그대로 두고 끝만 일주일 뒤로 보냈다
        (READ, datetime(2026, 5, 5, 18, 0, tzinfo=KST)),
    ],
)
def test_사진에서_읽은_끝이_아니면_받지_않는다(starts_at: datetime, ends_at: datetime) -> None:
    # 화면이 보내는 끝은 읽은 끝 그대로이거나 빈 값뿐이다 (web `carriedEndsAt`).
    # 시작보다 뒤이기만 하면 받으면, 요청만 바꿔서 읽은 적 없는 기간이 기록에 들어간다 (#952 리뷰)
    item = _timed(date(2026, 4, 28), ends_at=datetime(2026, 4, 28, 11, 0, tzinfo=KST))

    edited = apply_edits(
        item,
        title=item.title,
        day=date(2026, 4, 28),
        supplies=[],
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=False,
    )

    assert edited.starts_at == starts_at
    assert edited.ends_at is None


def test_읽은_끝이_없는_항목은_어떤_끝을_보내도_끝이_없다() -> None:
    item = _timed(date(2026, 4, 28), ends_at=None)

    edited = apply_edits(
        item,
        title=item.title,
        day=date(2026, 4, 28),
        supplies=[],
        starts_at=datetime(2026, 4, 28, 20, 0, tzinfo=KST),
        ends_at=datetime(2026, 4, 28, 21, 0, tzinfo=KST),
        all_day=False,
    )

    assert edited.ends_at is None


def test_읽은_시작과_끝을_그대로_다시_보내면_둘_다_그대로다() -> None:
    read_end = datetime(2026, 4, 28, 11, 0, tzinfo=KST)
    item = _timed(date(2026, 4, 28), ends_at=read_end)

    edited = apply_edits(
        item,
        title=item.title,
        day=date(2026, 4, 28),
        supplies=[],
        starts_at=READ,
        ends_at=read_end,
        all_day=False,
    )

    assert (edited.starts_at, edited.ends_at, edited.all_day) == (READ, read_end, False)


def test_시각을_읽은_일정을_하루_종일로_바꾸면_그날_하루_종일이다() -> None:
    item = _item(NoticeItemHandling.CALENDAR, starts_at=READ)

    edited = apply_edits(
        item, title=item.title, day=date(2026, 4, 28), supplies=[], starts_at=READ, all_day=True
    )

    assert edited.all_day is True
    assert _day(edited.starts_at) == date(2026, 4, 28)
    assert _day(edited.ends_at) == date(2026, 4, 28)


def test_보낸_시각의_날짜가_고친_날짜와_다르면_지금처럼_그날_하루로_둔다() -> None:
    # 예전 화면은 날짜만 고치고 읽은 시각을 그대로 다시 보낸다 — 그 시각을 새 날짜에 붙이지 않는다
    item = _item(NoticeItemHandling.CALENDAR, starts_at=READ)

    edited = apply_edits(
        item, title=item.title, day=EDITED, supplies=[], starts_at=READ, all_day=False
    )

    assert _day(edited.starts_at) == EDITED
    assert edited.all_day is True


def test_읽은_값을_그대로_다시_보내면_아무것도_바뀌지_않는다() -> None:
    item = _item(NoticeItemHandling.CALENDAR, starts_at=READ)

    edited = apply_edits(
        item, title=item.title, day=date(2026, 4, 28), supplies=[], starts_at=READ, all_day=False
    )

    assert (edited.starts_at, edited.ends_at, edited.all_day) == (READ, None, False)


def test_마감_항목은_시각을_보내도_마감만_고친다() -> None:
    # 할 일의 "시작하는 날 · 시작 시간" 은 캘린더에 넣을 때만 쓰는 값이다. 기록의 날짜는 마감이다
    item = _item(NoticeItemHandling.TODO, due_at=datetime(2026, 4, 30, 23, 59, tzinfo=KST))

    edited = apply_edits(
        item,
        title=item.title,
        day=EDITED,
        supplies=[],
        starts_at=datetime(2026, 5, 2, 20, 0, tzinfo=KST),
        all_day=False,
    )

    assert edited.starts_at is None
    assert _day(edited.due_at) == EDITED


def test_시간대가_없는_시각은_받지_않는다() -> None:
    item = _item(NoticeItemHandling.CALENDAR, starts_at=READ)

    edited = apply_edits(
        item,
        title=item.title,
        day=EDITED,
        supplies=[],
        starts_at=datetime(2026, 5, 2, 20, 0),
        all_day=False,
    )

    assert edited.all_day is True
