"""급식표 한 구간이 덜 읽혔다는 줄 — 화면 항목 · 서버 로그 (2026-11-11 실측).

줄 자체는 읽기(`agents/notice_ocr.flag_thin_ranges`)가 못 읽은 칸으로 더한다. 여기서는 화면에
"확인이 필요해요" 한 줄로 나가고, 읽은 급식은 그대로 저장 대상이며, 서버 로그에 WARNING 한 줄이
남는지(run id · 구간 · 읽은 날 수 · 평일 수만, 메뉴 글자 없음) 본다.

메뉴는 지어낸 것이다. 실제 기관 문서는 쓰지 않는다.
"""

import json
import logging
import uuid
from datetime import UTC, date, datetime

from app.agents.notice_ocr import PhotoReading, flag_thin_ranges, names_from_raw
from app.api.runs import photo_run
from app.domains.notice.assemble import NewNotice
from app.domains.notice.models import NoticeAudience, NoticeDocKind, NoticeInputFormat
from app.providers.meal_plan.image_ocr import DEFAULT_DAY_RANGES, OcrOutput, merge_outputs
from app.rules.meal_plan_coverage import ThinRange

TODAY = date(2026, 11, 11)
MENU = "지어낸흑미밥"
LUNCH = [{"meal_type": "lunch", "items": [{"name": MENU, "raw": MENU}]}]
NOTICE = NewNotice(
    doc_kind=NoticeDocKind.MEAL_PLAN,
    input_format=NoticeInputFormat.IMAGE,
    audience_scope=NoticeAudience.INSTITUTION,
    issued_on=None,
    received_at=datetime(2026, 11, 10, 19, 17, tzinfo=UTC),
    title="11월 급식계획",
    raw_text="지어낸 원문",
    content_hash="0" * 64,
    is_correction=False,
    items=(),
)


def _output(*days: int) -> OcrOutput:
    return OcrOutput.model_validate(
        {
            "institution_name": None,
            "year_month": "11",
            "days": [{"day": d, "meals": LUNCH} for d in days],
            "unparsed": [],
            "notes": None,
        }
    )


def _reading() -> PhotoReading:
    plan = names_from_raw(
        merge_outputs(
            [_output(2, 3, 4, 5, 6, 9, 10, 11, 12, 13), _output(16)], year_month="2026-11"
        )
    )
    plan, thin = flag_thin_ranges(plan, ranges=DEFAULT_DAY_RANGES)
    return PhotoReading(notice=NOTICE, meal_plan=plan, meal_thin=thin)


def test_덜_읽힌_구간은_확인_한_줄이고_읽은_급식은_저장_대상이다() -> None:
    run = photo_run.open_photo_run(run_id="r1", parent_id=uuid.uuid4(), child_id=uuid.uuid4())

    entries = photo_run.build_entries(run, _reading(), today=TODAY)

    meals = [e for e in entries if e["id"] in run.meals]
    assert len(meals) == 11 and not any(e["needs_review"] for e in meals)
    (line,) = [e for e in entries if e["id"].startswith("pe_meal_unread_")]
    assert line["needs_review"] is True and line["date"] is None
    assert line["review_reason"] == "16일~30일 급식 중 일부만 읽었어요. 사진과 비교해 주세요."


def test_덜_읽힌_구간은_서버_로그에_숫자만_남긴다(caplog) -> None:
    run = photo_run.open_photo_run(run_id="r-thin", parent_id=uuid.uuid4(), child_id=uuid.uuid4())

    with caplog.at_level(logging.WARNING, logger=photo_run.__name__):
        photo_run.build_entries(run, _reading(), today=TODAY)

    (record,) = [r for r in caplog.records if "덜 읽혔" in r.getMessage()]
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "r-thin" in message and "16일~30일" in message
    assert "읽은 날 1" in message and "평일 11" in message
    assert MENU not in message


def test_덜_읽힌_구간은_run_을_보관했다_꺼내도_남는다() -> None:
    # 사진 run 은 저장을 누를 때까지 보관함(JSON)에 있다 — 꺼낸 값도 같은 읽기여야 한다
    run = photo_run.open_photo_run(run_id="r1", parent_id=uuid.uuid4(), child_id=uuid.uuid4())
    run.reading = _reading()

    loaded = photo_run._load(json.loads(json.dumps(photo_run._dump(run))))

    assert loaded.reading.meal_thin == (ThinRange(16, 30, 1, 11),)
