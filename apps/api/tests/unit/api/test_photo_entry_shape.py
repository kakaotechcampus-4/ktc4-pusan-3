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
    handling: NoticeItemHandling = NoticeItemHandling.CALENDAR,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    all_day: bool = False,
    due_at: datetime | None = None,
    due_all_day: bool = False,
) -> NewNoticeItem:
    return NewNoticeItem(
        handling=handling,
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


def test_행사가_지났으면_신청_마감이_남아도_지난_일정이다() -> None:
    # 화면이 캘린더에 넣는 것은 시작 · 끝이다(마감은 안 넣는다, web photo-entry.ts eventBody).
    # 마감까지 보면 켜진 채로 시작해, 누르면 이미 지난 11-08 일정이 들어간다 (#854 리뷰)
    item = _item(
        starts_at=datetime(2026, 11, 8, 10, 0, tzinfo=KST),
        due_at=datetime(2026, 11, 10, 23, 59, tzinfo=KST),
    )

    assert _item_entry("pe_1", item, today=TODAY)["past"] is True


def test_행사가_남았으면_신청_마감이_지나도_지난_일정이_아니다() -> None:
    # 시작을 읽었으면 마감은 보지 않는다 — 넣는 날은 11-12 다
    item = _item(
        starts_at=datetime(2026, 11, 12, 10, 0, tzinfo=KST),
        due_at=datetime(2026, 11, 5, 23, 59, tzinfo=KST),
    )

    assert _item_entry("pe_1", item, today=TODAY)["past"] is False


def test_할_일은_기간이_열렸어도_마감이_남았으면_지난_일정이_아니다() -> None:
    # "신청 11-05 ~ 11-12" — 일정과 달리 캘린더에 넣는 날이 아니라 할 수 있는 동안이 중요하다.
    # 시작 · 끝만 보면 아직 신청할 수 있는 할 일이 지난 것으로 나간다 (#903 리뷰)
    item = _item(
        handling=NoticeItemHandling.TODO,
        starts_at=datetime(2026, 11, 5, tzinfo=KST),
        due_at=datetime(2026, 11, 12, 23, 59, tzinfo=KST),
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


def test_급식_제목은_빈_이름을_잇지_않는다() -> None:
    # 원문이 번호뿐인 항목("2")은 원문에서 만든 이름이 비어 있다(`menu_display_name`, #908).
    # 그대로 이으면 화면 제목이 "밥, , 우유" 가 된다
    from types import SimpleNamespace

    from app.api.runs.photo_run import PhotoRun

    names = ["밥", "", "우유", ""]
    meal = SimpleNamespace(
        meal_type=SimpleNamespace(value="lunch"),
        items=[SimpleNamespace(name=name) for name in names],
    )
    plan = SimpleNamespace(
        days=[SimpleNamespace(day=9, meals=[meal])],
        unparsed=[],
        date_of=lambda day: date(2026, 11, day),
    )
    run = PhotoRun("r1", None, None, 0.0)

    (entry,) = _meal_entries(run, plan, today=TODAY)

    assert entry["title"] == "밥, 우유"


def test_이름이_하나도_없는_끼니는_읽지_못한_칸으로_보낸다() -> None:
    # 원문이 번호뿐인 항목만 있는 끼니("2" · "1,5")는 이름이 전부 비어 제목이 "" 가 된다. 빈 제목
    # 급식 항목이 화면에 나가면 저장 요청 전체가 422("제목이 비어 있어요")로 막힌다 — 조용히 빼지
    # 않고, 모델이 못 읽었다고 한 칸(unparsed)과 같은 모양으로 확인을 받는다
    from types import SimpleNamespace

    from app.api.runs.photo_run import PhotoRun

    def meal(meal_type: str, *items: tuple[str, str]) -> SimpleNamespace:
        return SimpleNamespace(
            meal_type=SimpleNamespace(value=meal_type),
            items=[SimpleNamespace(name=name, raw=raw) for name, raw in items],
        )

    plan = SimpleNamespace(
        days=[
            SimpleNamespace(
                day=9, meals=[meal("lunch", ("밥", "밥")), meal("snack_pm", ("", "2"), ("", "1,5"))]
            )
        ],
        unparsed=[SimpleNamespace(day=None, meal_type=None, raw="생일식단", why="날짜 없는 열")],
        date_of=lambda day: date(2026, 11, day),
    )
    run = PhotoRun("r1", None, None, 0.0)

    entries = _meal_entries(run, plan, today=TODAY)

    assert [e["id"] for e in entries] == [
        "pe_meal_9_lunch",
        "pe_meal_unread_0",
        "pe_meal_unread_1",
    ]
    assert all(e["title"] for e in entries)  # 빈 제목 항목이 나가지 않는다
    unread = entries[2]
    assert (unread["kind"], unread["title"], unread["date"]) == ("meal", "2, 1,5", None)
    # 어느 날 어느 끼니인지 보여야 보호자가 종이에서 찾는다 (날짜를 아는 칸만)
    assert (unread["needs_review"], unread["review_reason"]) == (
        True,
        "11월 9일 오후 간식 — 이 칸은 읽지 못했어요.",
    )
    assert entries[1]["review_reason"] == "날짜 없는 열"  # 날짜 없는 칸은 이유만
    assert list(run.meals) == ["pe_meal_9_lunch"]  # 저장할 끼니로 들고 있지 않는다


def test_한_날에_같은_끼니가_두_번이면_앞의_것만_싣고_뒤는_읽지_못한_칸이다() -> None:
    # 같은 끼니가 두 번 오면 항목 id 가 겹친다(pe_meal_9_lunch). 웹이 그대로 보내면 저장 전체가
    # "같은 항목을 두 번 보냈어요" 로 막힌다 — 합치기의 같은 날 규칙처럼 앞의 것을 남긴다
    from types import SimpleNamespace

    from app.api.runs.photo_run import PhotoRun

    def meal(*raws: str) -> SimpleNamespace:
        return SimpleNamespace(
            meal_type=SimpleNamespace(value="lunch"),
            items=[SimpleNamespace(name=r, raw=r) for r in raws],
        )

    plan = SimpleNamespace(
        days=[SimpleNamespace(day=9, meals=[meal("흑미밥"), meal("현미밥", "미역국")])],
        unparsed=[],
        date_of=lambda day: date(2026, 11, day),
    )
    run = PhotoRun("r1", None, None)

    entries = _meal_entries(run, plan, today=TODAY)

    assert [(e["id"], e["title"], e["needs_review"]) for e in entries] == [
        ("pe_meal_9_lunch", "흑미밥", False),
        ("pe_meal_unread_0", "현미밥, 미역국", True),
    ]
    assert list(run.meals) == ["pe_meal_9_lunch"]


def test_모델이_못_읽었다고_한_칸도_어느_날인지_보인다() -> None:
    from types import SimpleNamespace

    from app.api.runs.photo_run import PhotoRun

    plan = SimpleNamespace(
        days=[],
        unparsed=[
            SimpleNamespace(
                day=4, meal_type=SimpleNamespace(value="lunch"), raw="빵(찐", why="잘림"
            ),
            SimpleNamespace(day=15, meal_type=None, raw="현미밥", why="두 번 읽힘"),
            SimpleNamespace(day=31, meal_type=None, raw=None, why="흐림"),  # 11월에 없는 날
        ],
        date_of=lambda day: date(2026, 11, day),
    )

    entries = _meal_entries(PhotoRun("r1", None, None), plan, today=TODAY)

    assert [e["review_reason"] for e in entries] == [
        "11월 4일 점심 — 잘림",
        "11월 15일 — 두 번 읽힘",
        "흐림",
    ]
    assert all(e["date"] is None for e in entries)  # 저장하지 않는 칸이라 날짜 칸은 비운다
