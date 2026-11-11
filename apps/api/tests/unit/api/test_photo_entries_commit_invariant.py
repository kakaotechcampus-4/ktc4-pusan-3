"""불변식 — 확인 화면에 나가는 항목은 저장 검증을 반드시 통과한다 (2026-11-11 운영 장애).

서버는 읽은 항목을 검증 없이 화면에 보내고(SSE `parsed`), 저장(`POST /photo-runs/{rid}/commit`)
때 `PhotoCommitRequest` 로 엄격히 검증한다. 한 건이라도 걸리면 저장 전체가 400 이었다 — "잘 읽었어요
24건" 을 보고 저장을 눌렀는데 "저장하지 못했어요" 가 떴다.

웹이 저장 때 보내는 본문은 확인한 항목(`needs_review` 가 거짓)에 시트의 시작 일시를 얹고
(`recordedEntry` — #952) `event_id` 를 더한 것이다 (`apps/web/src/components/photo-review.tsx` 의
`save` · `recordOf`, `apps/web/src/lib/photo-entry.ts` 의 `recordedEntry`). 보호자가 고치지 않았으면
시트의 값은 `initialPlan` 이라, 시작 항목은 `starts_at` 이 `"{date}T{HH:MM}:00+09:00"`(하루 종일이면
자정) 표기로 바뀌어 간다 — 아래 `_web_record` 가 그 모양을 만든다. 확인이 필요한 항목은 보호자가
시트에서 확인해야 저장 대상이 되고, 그때 시트는 `needs_review` 를 끄고 `review_reason` 을 지운다
(`photo-entry-sheet.tsx` 의 `submit`). 그래서 두 가지를 본다:

1. 화면 항목을 고치지 않고 그대로 되돌려 보낸 본문이 `PhotoCommitRequest` 를 통과한다
2. 확인이 필요한 항목도 보호자가 고치지 않고 확인만 눌렀을 때 `PhotoEntry` 를 통과한다

입력은 표로 만든 변형이다(새 의존성 없이). 실제 기관 문서 · 아이 정보는 쓰지 않는다.
"""

import json
import logging
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.agents.notice_ocr import PhotoReading, flag_thin_ranges, names_from_raw
from app.api.runs import photo_run
from app.api.v1.schemas.photos import MAX_COMMIT_ENTRIES, PhotoCommitRequest, PhotoEntry
from app.core.notice_extract import ItemDetail
from app.domains.daycare_meal.models import DaycareSlot
from app.domains.notice.assemble import NewNotice, NewNoticeItem
from app.domains.notice.models import (
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
    NoticeItemHandling,
    NoticeItemTarget,
)
from app.providers.meal_plan.image_ocr import (
    DEFAULT_DAY_RANGES,
    MissedRange,
    OcrOutput,
    merge_outputs,
)
from app.providers.meal_plan.schema import MealPlanJSON

KST = ZoneInfo("Asia/Seoul")
TODAY = date(2026, 11, 11)
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "meal_plan_2025_01.json"


# ── 만들기 ─────────────────────────────────────────────────────────────────


def _notice(*items: NewNoticeItem, doc_kind: NoticeDocKind = NoticeDocKind.MEAL_PLAN) -> NewNotice:
    return NewNotice(
        doc_kind=doc_kind,
        input_format=NoticeInputFormat.IMAGE,
        audience_scope=NoticeAudience.INSTITUTION,
        issued_on=None,
        received_at=datetime(2026, 11, 10, 19, 17, tzinfo=UTC),
        title="11월 급식계획",
        raw_text="지어낸 원문",
        content_hash="0" * 64,
        is_correction=False,
        items=items,
    )


BASE_ITEM = NewNoticeItem(
    handling=NoticeItemHandling.CALENDAR,
    label="행사",
    title="숲 체험",
    target_scope=NoticeItemTarget.CLASS,
    starts_at=datetime(2026, 11, 20, 10, 0, tzinfo=KST),
    ends_at=None,
    all_day=False,
    due_at=None,
    due_all_day=False,
    details=(ItemDetail(name="장소", value="뒷산"),),
    supplies=("물통",),
    source_quote="11월 20일 숲 체험",
    needs_review=False,
    review_reason=None,
)


def _item(**changes) -> NewNoticeItem:
    return replace(BASE_ITEM, **changes)


def _menu(*raws: str) -> list[dict]:
    return [{"name": r, "raw": r} for r in raws]


def _day(day: int, *meals: tuple[str, list[dict]]) -> dict:
    return {"day": day, "meals": [{"meal_type": t, "items": items} for t, items in meals]}


def _output(days: list[dict], unparsed: list[dict] | None = None) -> OcrOutput:
    return OcrOutput.model_validate(
        {
            "institution_name": None,
            "year_month": "11",
            "days": days,
            "unparsed": unparsed or [],
            "notes": None,
        }
    )


def _plan(*outputs: OcrOutput, year_month: str = "2026-11", missed=()) -> MealPlanJSON:
    return names_from_raw(merge_outputs(outputs, year_month=year_month, missed=missed))


def _fixture_plan() -> MealPlanJSON:
    data = {k: v for k, v in json.loads(FIXTURE.read_text()).items() if not k.startswith("_")}
    data.pop("source", None)
    return names_from_raw(merge_outputs([OcrOutput.model_validate(data)], year_month="2025-01"))


LONG_NAME = "아주긴이름의영양가득한채소볶음밥" * 3  # 48자
# 끝에 숫자를 붙이면 알레르기 번호로 지워진다 — 한글 한 글자로 서로 다른 이름을 만든다
LONG_MENUS = [f"{LONG_NAME}{chr(0xAC00 + i)}" for i in range(120)]
MEAL_VARIANTS: dict[str, MealPlanJSON] = {
    "정답셋 2025-01": _fixture_plan(),
    "빈 이름 · 번호만 · 열량 줄": _plan(
        _output(
            [
                _day(2, ("lunch", _menu("흑미밥", "2", "총 471 kcal"))),
                _day(3, ("lunch", _menu("1,5", "2"))),
                _day(4, ("lunch", _menu("총 511 kcal")), ("snack_pm", _menu("462/17"))),
            ]
        )
    ),
    "빈 끼니 · 휴일": _plan(
        _output([_day(5, ("lunch", [])), {"day": 6, "meals": [], "note": "휴일"}])
    ),
    "31일이 없는 달": _plan(
        _output([_day(30, ("lunch", _menu("밥"))), _day(31, ("lunch", _menu("국")))])
    ),
    "같은 날 두 번": _plan(
        _output([_day(9, ("lunch", _menu("흑미밥")))]),
        _output([_day(9, ("lunch", _menu("현미밥")))]),
    ),
    "같은 끼니 두 번": _plan(_output([_day(9, ("lunch", _menu("밥")), ("lunch", _menu("국")))])),
    "메뉴 100개 넘는 칸": _plan(_output([_day(10, ("lunch", _menu(*LONG_MENUS)))])),
    "한쪽 호출 실패": _plan(
        _output([_day(2, ("lunch", _menu("밥")))]), missed=(MissedRange(16, 31),)
    ),
    # 뒤 호출이 16일 하나만 내고 정상 종료 — "16일~30일 급식 중 일부만 읽었어요" 한 줄이 더해진다
    "한 구간만 덜 읽힘": flag_thin_ranges(
        _plan(
            _output([_day(d, ("lunch", _menu("밥"))) for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)]),
            _output([_day(16, ("lunch", _menu("국")))]),
        ),
        ranges=DEFAULT_DAY_RANGES,
    )[0],
    "못 읽은 칸 원문이 비거나 김": _plan(
        _output(
            [],
            unparsed=[
                {"day": 4, "meal_type": "lunch", "raw": "   ", "why": "흐림"},
                {"day": None, "meal_type": None, "raw": "가" * 600, "why": "잘림"},
                {"day": 40, "meal_type": None, "raw": None, "why": "x" * 600},
            ],
        )
    ),
    # 괄호 안이 음식이 아니라 저장 키가 하나도 안 나오는 끼니 — 화면에 나가면 저장이 422(#908)
    "저장 키가 안 나오는 끼니": _plan(
        _output(
            [
                _day(2, ("lunch", _menu("(자율배식)"))),
                _day(3, ("lunch", _menu("(국내산)")), ("snack_pm", _menu("(1/2)"))),
                _day(4, ("lunch", _menu("(행사)", "(소)"))),
                _day(5, ("lunch", _menu("(자율배식)", "버섯솥밥"))),
            ]
        )
    ),
    # 연도는 코드가 정하지만, 사진에 "2019년 1월" 이 적혀 있으면 그 연도를 믿는다 — 저장 범위 밖이다
    "사진에 적힌 연도가 범위 밖": _plan(
        _output([_day(2, ("lunch", _menu("밥")))]), year_month="2019-01"
    ),
}

NOTICE_VARIANTS: dict[str, NewNoticeItem] = {
    "멀쩡한 일정": BASE_ITEM,
    # 웹이 시트의 시작을 얹어 보내는 모양(#952) — 읽은 끝이 같이 가는 경우 · 하루 종일 여러 날
    "시각과 끝을 읽은 일정": _item(ends_at=datetime(2026, 11, 20, 11, 30, tzinfo=KST)),
    "하루 종일 여러 날": _item(
        starts_at=datetime(2026, 11, 20, tzinfo=KST),
        ends_at=datetime(2026, 11, 22, 23, 59, tzinfo=KST),
        all_day=True,
    ),
    "빈 제목": _item(title="   "),
    "500자 넘는 제목": _item(title="가" * 600),
    "범위 밖 연도 시작": _item(starts_at=datetime(2019, 3, 1, tzinfo=KST)),
    "범위 밖 연도 마감": _item(
        handling=NoticeItemHandling.TODO, starts_at=None, due_at=datetime(2035, 1, 1, tzinfo=KST)
    ),
    "빈 준비물": _item(supplies=("물통", "", "  ")),
    "너무 긴 준비물": _item(supplies=("가" * 150,)),
    "준비물 100개 넘음": _item(supplies=tuple(f"준비물{i}" for i in range(130))),
}


def _entries(reading: PhotoReading) -> tuple[photo_run.PhotoRun, list[dict]]:
    run = photo_run.open_photo_run(run_id="r1", parent_id=uuid.uuid4(), child_id=uuid.uuid4())
    entries = photo_run.build_entries(run, reading, today=TODAY)
    # 화면은 SSE 로 JSON 을 받고 JSON 으로 되돌려 보낸다
    return run, json.loads(json.dumps(entries, ensure_ascii=False))


def _date_role(entry: dict) -> str:
    """web `entryDateRole`."""
    if entry.get("starts_at"):
        return "start"
    if entry.get("due_at"):
        return "due"
    return "start" if entry["kind"] == "event" else "due"


def _web_record(entry: dict) -> dict:
    """web `recordedEntry(entry, initialPlan(entry))` — 시트를 고치지 않았을 때 저장 본문의 항목.

    `photo-entry.ts` 의 `canBecomeEvent` · `readStartTime` · `withSeoulDateTime` ·
    `carriedEndsAt` 를 그대로 옮긴 것이다. 🚨 웹의 그 함수들을 바꾸면 이것도 같이 본다.
    """
    if entry["kind"] in ("meal", "info") or _date_role(entry) != "start":
        return entry
    all_day = entry.get("all_day", True)  # initialPlan: `entry.all_day ?? true`
    day = entry.get("date")
    if not day:
        return entry  # planDate 가 비면 planStartsAt 이 null — 읽은 값 그대로
    read_start = entry.get("starts_at")
    if all_day:
        starts = f"{day}T00:00:00+09:00"
    else:
        if not read_start:
            return entry  # 하루 종일이 아닌데 시각이 없으면 null
        hm = datetime.fromisoformat(read_start).astimezone(KST).strftime("%H:%M")
        starts = f"{day}T{hm}:00+09:00"
    ends = entry.get("ends_at")
    carried = None
    if (
        ends
        and read_start
        and day == datetime.fromisoformat(read_start).astimezone(KST).date().isoformat()
    ):
        if datetime.fromisoformat(ends) > datetime.fromisoformat(starts):
            carried = ends  # plan.allDay 는 initialPlan 이라 읽은 all_day 와 같다
    return {**entry, "starts_at": starts, "ends_at": carried, "all_day": all_day}


def _assert_commit_passes(run: photo_run.PhotoRun, entries: list[dict]) -> None:
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "같은 id 가 두 번이면 저장이 422"

    checked = [{**_web_record(e), "event_id": None} for e in entries if not e["needs_review"]]
    body = PhotoCommitRequest.model_validate(
        {"lane": "document", "entries": checked, "attach_to_calendar": False}
    )
    # 확인이 필요한 급식도 보호자가 확인하면 저장 대상이다 — run.meals 에 남은 끼니는 모두 키가 있다
    for entry in entries:
        if entry["id"] in run.meals:
            assert photo_run.meal_row(run.meals[entry["id"]], title=entry["title"], day=None)
    for entry in body.entries:
        if entry.id in run.meals:
            # 메뉴 키가 하나도 안 남으면 commit 이 422("메뉴를 적어 주세요")다
            assert photo_run.meal_row(run.meals[entry.id], title=entry.title, day=entry.date)
        elif entry.id in run.items:
            # commit 이 하는 그대로 고친 값을 올린다 (#952) — 시작이 끝보다 늦으면 안 된다
            saved = photo_run.apply_edits(
                run.items[entry.id],
                title=entry.title,
                day=entry.date,
                supplies=entry.items,
                starts_at=entry.starts_at,
                ends_at=entry.ends_at,
                all_day=entry.all_day,
            )
            if saved.starts_at is not None and saved.ends_at is not None:
                assert saved.starts_at <= saved.ends_at
        else:
            assert entry.id.startswith(("pe_meal_unread_", "pe_unread_"))

    # 보호자가 확인 필요 항목을 고치지 않고 확인만 눌러도 저장 검증을 통과한다 (시트의 submit)
    for entry in entries:
        PhotoEntry.model_validate(
            {
                **_web_record({**entry, "needs_review": False, "review_reason": None}),
                "event_id": None,
            }
        )
    assert len(checked) <= MAX_COMMIT_ENTRIES


# ── 불변식 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", list(MEAL_VARIANTS))
def test_급식표_변형의_화면_항목은_저장_검증을_통과한다(name: str) -> None:
    reading = PhotoReading(notice=_notice(BASE_ITEM), meal_plan=MEAL_VARIANTS[name])

    run, entries = _entries(reading)

    _assert_commit_passes(run, entries)


@pytest.mark.parametrize("name", list(NOTICE_VARIANTS))
def test_공지_항목_변형의_화면_항목은_저장_검증을_통과한다(name: str) -> None:
    reading = PhotoReading(
        notice=_notice(NOTICE_VARIANTS[name], doc_kind=NoticeDocKind.NEWSLETTER), meal_plan=None
    )

    run, entries = _entries(reading)

    _assert_commit_passes(run, entries)


def test_식단표를_못_읽은_결과도_통과한다() -> None:
    reading = PhotoReading(notice=_notice(BASE_ITEM), meal_plan=None, meal_failed=True)

    _assert_commit_passes(*_entries(reading))


def test_모든_변형을_한_장에_섞어도_통과한다() -> None:
    items = tuple(NOTICE_VARIANTS.values())
    for plan in MEAL_VARIANTS.values():
        _assert_commit_passes(*_entries(PhotoReading(notice=_notice(*items), meal_plan=plan)))


def test_공지_항목이_아주_많아도_확인한_항목은_한_번에_저장할_수_있는_수까지다() -> None:
    items = tuple(_item(title=f"행사 {i}") for i in range(MAX_COMMIT_ENTRIES + 30))
    reading = PhotoReading(notice=_notice(*items), meal_plan=MEAL_VARIANTS["정답셋 2025-01"])

    _assert_commit_passes(*_entries(reading))


# ── 걸린 항목을 어떻게 내보내는가 ──────────────────────────────────────────


def _one(item: NewNoticeItem) -> dict:
    _, entries = _entries(
        PhotoReading(notice=_notice(item, doc_kind=NoticeDocKind.NEWSLETTER), meal_plan=None)
    )
    (entry,) = entries
    return entry


def test_범위_밖_날짜는_비우고_확인을_받는다() -> None:
    entry = _one(NOTICE_VARIANTS["범위 밖 연도 시작"])

    assert (entry["date"], entry["needs_review"]) == (None, True)
    assert "날짜" in entry["review_reason"]


def test_빈_제목은_확인을_받는다() -> None:
    entry = _one(NOTICE_VARIANTS["빈 제목"])

    assert entry["needs_review"] is True
    assert entry["title"].strip()


def test_긴_제목은_잘라서_확인을_받는다() -> None:
    entry = _one(NOTICE_VARIANTS["500자 넘는 제목"])

    assert len(entry["title"]) <= 500 and entry["needs_review"] is True


def test_빈_준비물은_조용히_빼고_멀쩡한_준비물은_남긴다() -> None:
    entry = _one(NOTICE_VARIANTS["빈 준비물"])

    assert entry["items"] == ["물통"]
    assert entry["needs_review"] is False  # 빈 칸을 빼는 것은 내용을 바꾸지 않는다


def test_긴_준비물은_잘라서_확인을_받는다() -> None:
    entry = _one(NOTICE_VARIANTS["너무 긴 준비물"])

    assert len(entry["items"][0]) <= 100 and entry["needs_review"] is True


def test_메뉴가_너무_많은_끼니는_메뉴_경계에서_자르고_확인을_받는다() -> None:
    run, entries = _entries(
        PhotoReading(notice=_notice(), meal_plan=MEAL_VARIANTS["메뉴 100개 넘는 칸"])
    )

    (entry,) = entries
    assert entry["needs_review"] is True and len(entry["title"]) <= 500
    # 메뉴 이름 한가운데서 자르면 반쪽 이름이 저장 키가 된다 — 남은 조각은 모두 온전한 메뉴다
    assert all(part in LONG_MENUS for part in entry["title"].split(", "))


def test_범위_밖_날짜의_급식은_저장_대상에서_빠지고_읽지_못한_칸으로_보인다() -> None:
    # 날짜를 비운 급식을 확인하면 commit 이 run 의 날짜(범위 밖)로 저장한다
    # — 그래서 끼니로 들지 않는다
    run, entries = _entries(
        PhotoReading(notice=_notice(), meal_plan=MEAL_VARIANTS["사진에 적힌 연도가 범위 밖"])
    )

    (entry,) = entries
    assert entry["id"].startswith("pe_meal_unread_")
    assert (entry["date"], entry["needs_review"]) == (None, True)
    assert run.meals == {}
    assert "날짜" in entry["review_reason"]


def test_걸린_항목은_id_와_칸_이름만_로그에_남긴다(caplog) -> None:
    secret = "아이이름이들어간제목" * 60
    with caplog.at_level(logging.WARNING, logger="app.api.runs.photo_run"):
        _one(_item(title=secret))

    (record,) = [r for r in caplog.records if "저장 검사" in r.getMessage()]
    message = record.getMessage()
    assert "pe_0" in message and "title" in message
    assert "아이이름" not in message  # 원문 · 제목 글자는 남기지 않는다 (§2 개인정보)


# ── 연도 (rules/meal_plan_month) ───────────────────────────────────────────


def test_연도를_추정한_급식표도_통과한다() -> None:
    from app.rules.meal_plan_month import MealMonth

    reading = PhotoReading(
        notice=_notice(BASE_ITEM),
        meal_plan=MEAL_VARIANTS["빈 이름 · 번호만 · 열량 줄"],
        meal_month=MealMonth(11, 2026, "guessed"),
    )

    _assert_commit_passes(*_entries(reading))


def test_연도를_몰라_급식을_내지_않은_결과도_통과하고_급식은_저장_대상이_아니다() -> None:
    from app.rules.meal_plan_month import MealMonth

    reading = PhotoReading(
        notice=_notice(BASE_ITEM),
        meal_plan=None,
        meal_failed=True,
        meal_month=MealMonth(3, None, "unknown"),
    )

    run, entries = _entries(reading)

    _assert_commit_passes(run, entries)
    assert run.meals == {}
    assert [e["id"] for e in entries if e["kind"] == "meal"] == ["pe_meal_unread_0"]


def test_저장_키가_안_나오는_끼니는_원문과_함께_읽지_못한_칸으로_보인다() -> None:
    run, entries = _entries(
        PhotoReading(notice=_notice(), meal_plan=MEAL_VARIANTS["저장 키가 안 나오는 끼니"])
    )

    unread = [e for e in entries if e["id"].startswith("pe_meal_unread_")]
    assert sorted(e["title"] for e in unread) == ["(1/2)", "(국내산)", "(자율배식)", "(행사), (소)"]
    assert all(e["needs_review"] and e["date"] is None for e in unread)
    # 진짜 메뉴가 같이 있는 끼니는 평소처럼 저장 대상이다
    assert list(run.meals) == ["pe_meal_5_lunch"]
    (saved,) = [e for e in entries if e["id"] == "pe_meal_5_lunch"]
    assert saved["needs_review"] is False
    assert photo_run.meal_row(run.meals["pe_meal_5_lunch"], title=saved["title"], day=None)


def test_화면에_나가기_직전에도_저장_키가_없는_급식은_읽지_못한_칸으로_돌린다() -> None:
    # `_meal_entries` 를 거치지 않고 들어온 급식 항목 — `_servable` 이 마지막으로 막는다
    run = photo_run.open_photo_run(run_id="r1", parent_id=uuid.uuid4(), child_id=uuid.uuid4())
    run.meals["pe_meal_2_lunch"] = photo_run.MealSlot(date(2026, 11, 2), DaycareSlot.LUNCH)
    entry = {
        "id": "pe_meal_2_lunch",
        "kind": "meal",
        "title": "(자율배식)",
        "date": "2026-11-02",
        "all_day": True,
        "items": [],
        "needs_review": False,
        "past": False,
    }

    (out,) = photo_run._servable(run, [entry])

    assert out["id"].startswith("pe_meal_unread_") and out["needs_review"] is True
    assert run.meals == {}
