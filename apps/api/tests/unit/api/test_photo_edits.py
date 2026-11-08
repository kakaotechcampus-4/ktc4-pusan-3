"""사진 확인 화면에서 고친 날짜 — app/api/runs/photo_run.py 의 apply_edits (#723)

화면(`apps/web/src/lib/photo-entry.ts` 의 `entryDateRole`)과 같은 순서로 무엇을 고칠지 정한다 —
시작을 읽었으면 시작, 마감만 읽었으면 마감, 둘 다 없으면 종류(일정은 시작 · 그 밖은 마감).
"""

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
