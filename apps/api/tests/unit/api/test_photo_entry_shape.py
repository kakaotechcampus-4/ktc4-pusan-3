"""사진 확인 화면으로 보내는 항목의 하루 종일 — app/api/runs/photo_run.py 의 _item_entry (#723)

화면(`apps/web/src/lib/photo-entry.ts`)은 `all_day` 를 위 칸(시작이나 마감)의 하루 종일로 읽는다.
시작을 읽었으면 시작의 것, 마감만 읽었으면 마감의 것이어야 한다 — 둘을 합치면 시각을 읽은 시작이
마감 때문에 하루 종일로 보이고, 보호자가 못 본 끝 시각이 캘린더에 들어간다.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.api.runs.photo_run import _item_entry
from app.domains.notice.assemble import NewNoticeItem
from app.domains.notice.models import NoticeItemHandling, NoticeItemTarget

KST = ZoneInfo("Asia/Seoul")


def _item(
    *,
    starts_at: datetime | None = None,
    all_day: bool = False,
    due_at: datetime | None = None,
    due_all_day: bool = False,
) -> NewNoticeItem:
    return NewNoticeItem(
        handling=NoticeItemHandling.CALENDAR,
        label="행사",
        title="숲 체험",
        target_scope=NoticeItemTarget.CLASS,
        starts_at=starts_at,
        ends_at=None,
        all_day=all_day,
        due_at=due_at,
        due_all_day=due_all_day,
        details=(),
        supplies=(),
        source_quote="",
        needs_review=False,
        review_reason=None,
    )


def test_시각을_읽은_시작은_마감이_하루_종일이어도_하루_종일이_아니다() -> None:
    # "11월 20일 10:00 ~ 12:00, 신청은 11월 15일까지" — 시작은 시각을, 마감은 날짜만 읽었다
    item = _item(
        starts_at=datetime(2026, 11, 20, 10, 0, tzinfo=KST),
        due_at=datetime(2026, 11, 15, 23, 59, tzinfo=KST),
        due_all_day=True,
    )

    assert _item_entry("pe_1", item)["all_day"] is False


@pytest.mark.parametrize("due_all_day", [True, False])
def test_마감만_읽었으면_마감의_하루_종일을_보낸다(due_all_day: bool) -> None:
    item = _item(due_at=datetime(2026, 11, 3, 14, 0, tzinfo=KST), due_all_day=due_all_day)

    assert _item_entry("pe_1", item)["all_day"] is due_all_day


def test_하루_종일인_시작은_하루_종일이다() -> None:
    item = _item(starts_at=datetime(2026, 11, 20, tzinfo=KST), all_day=True)

    assert _item_entry("pe_1", item)["all_day"] is True
