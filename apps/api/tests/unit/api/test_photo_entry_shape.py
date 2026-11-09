"""사진 확인 화면으로 보내는 항목 — app/api/runs/photo_run.py 의 하루 종일 (#723) · 지난 일정 (#828)

화면(`apps/web/src/lib/photo-entry.ts`)은 `all_day` 를 위 칸(시작이나 마감)의 하루 종일로 읽는다.
시작을 읽었으면 시작의 것, 마감만 읽었으면 마감의 것이어야 한다 — 둘을 합치면 시각을 읽은 시작이
마감 때문에 하루 종일로 보이고, 보호자가 못 본 끝 시각이 캘린더에 들어간다.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.api.runs.photo_run import _item_entry, _meal_entries
from app.domains.notice.assemble import NewNoticeItem
from app.domains.notice.models import NoticeItemHandling, NoticeItemTarget

KST = ZoneInfo("Asia/Seoul")


TODAY = date(2026, 11, 9)


def _item(
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
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
        ends_at=ends_at,
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

    assert _item_entry("pe_1", item, today=TODAY)["all_day"] is False


@pytest.mark.parametrize("due_all_day", [True, False])
def test_마감만_읽었으면_마감의_하루_종일을_보낸다(due_all_day: bool) -> None:
    item = _item(due_at=datetime(2026, 11, 3, 14, 0, tzinfo=KST), due_all_day=due_all_day)

    assert _item_entry("pe_1", item, today=TODAY)["all_day"] is due_all_day


def test_하루_종일인_시작은_하루_종일이다() -> None:
    item = _item(starts_at=datetime(2026, 11, 20, tzinfo=KST), all_day=True)

    assert _item_entry("pe_1", item, today=TODAY)["all_day"] is True


# ── 지난 일정 (#828) ─────────────────────────────────────────────────────────
# 지난주 알림장을 올리면 이미 지난 일정도 읽힌다. 화면은 이 표시로 체크를 켜 둘지 정한다 —
# 오늘과 견주는 것은 날짜 계산이라 서버가 한다 (루트 §2 · apps/web CLAUDE.md §4).


def test_시작이_오늘보다_앞이면_지난_일정이다() -> None:
    item = _item(starts_at=datetime(2026, 11, 6, 10, 0, tzinfo=KST))

    assert _item_entry("pe_1", item, today=TODAY)["past"] is True


def test_오늘_일정은_지난_일정이_아니다() -> None:
    item = _item(starts_at=datetime(2026, 11, 9, 9, 0, tzinfo=KST))

    assert _item_entry("pe_1", item, today=TODAY)["past"] is False


def test_여러_날_행사가_오늘에_걸쳐_있으면_지난_일정이_아니다() -> None:
    # "11월 6일 ~ 11월 12일 독서 주간" — 시작은 지났지만 아직 끝나지 않았다
    item = _item(
        starts_at=datetime(2026, 11, 6, tzinfo=KST),
        ends_at=datetime(2026, 11, 12, 23, 59, tzinfo=KST),
        all_day=True,
    )

    assert _item_entry("pe_1", item, today=TODAY)["past"] is False


def test_행사는_앞이어도_신청_마감이_남았으면_지난_일정이_아니다() -> None:
    # 시작 · 끝 · 마감 중 가장 늦은 것으로 본다
    item = _item(
        starts_at=datetime(2026, 11, 8, 10, 0, tzinfo=KST),
        due_at=datetime(2026, 11, 10, 23, 59, tzinfo=KST),
    )

    assert _item_entry("pe_1", item, today=TODAY)["past"] is False


def test_마감만_읽었고_지났으면_지난_일정이다() -> None:
    item = _item(due_at=datetime(2026, 11, 3, 23, 59, tzinfo=KST), due_all_day=True)

    assert _item_entry("pe_1", item, today=TODAY)["past"] is True


def test_날짜를_못_읽었으면_지난_일정이_아니다() -> None:
    assert _item_entry("pe_1", _item(), today=TODAY)["past"] is False


@pytest.mark.parametrize(
    ("moment", "past"),
    [
        # 한국 11-08 23:30 = UTC 11-08 14:30 — 한국 날짜로 어제다
        (datetime(2026, 11, 8, 14, 30, tzinfo=UTC), True),
        # 한국 11-09 00:30 = UTC 11-08 15:30 — UTC 날짜로는 어제지만 한국 날짜로는 오늘이다
        (datetime(2026, 11, 8, 15, 30, tzinfo=UTC), False),
    ],
)
def test_지났는지는_한국_날짜로_본다(moment: datetime, past: bool) -> None:
    assert _item_entry("pe_1", _item(starts_at=moment), today=TODAY)["past"] is past


def test_급식도_같은_칸을_싣는다() -> None:
    from types import SimpleNamespace

    from app.api.runs.photo_run import PhotoRun

    meal = SimpleNamespace(
        meal_type=SimpleNamespace(value="lunch"), items=[SimpleNamespace(name="밥")]
    )
    plan = SimpleNamespace(
        days=[SimpleNamespace(day=8, meals=[meal]), SimpleNamespace(day=9, meals=[meal])],
        unparsed=[],
        date_of=lambda day: date(2026, 11, day),
    )
    run = PhotoRun("r1", None, None, 0.0)

    entries = _meal_entries(run, plan, today=TODAY)

    got = [(e["date"], e["past"]) for e in entries]
    assert got == [("2026-11-08", True), ("2026-11-09", False)]
