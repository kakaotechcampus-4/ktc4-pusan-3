"""사진 읽기 — 급식표 한 구간이 조용히 덜 읽혔으면 한 줄로 알린다 (2026-11-11 실측).

뒤 호출(16~31일)이 16일 하나만 내고 정상 종료(`stop`)했다. 오류가 아니라서 "읽지 못했어요" 줄이
나가지 않았고, 표의 21일치 중 11일치만 아무 표시 없이 화면에 나가 저장됐다. 이제 그 구간을 평일 수와
견줘(`rules/meal_plan_coverage`) 못 읽은 칸 통로로 "16일~30일 급식 중 일부만 읽었어요" 한 줄을 낸다.
🚨 저장은 막지 않는다 — 읽은 급식은 그대로 저장 대상이다.

날짜 · 메뉴는 지어낸 것이다. 실제 기관 문서는 쓰지 않는다.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agents import notice_ocr
from app.domains.notice.models import NoticeDocKind
from app.providers.meal_plan.image_ocr import DEFAULT_DAY_RANGES, MissedRange, OcrOutput, ReadParts
from app.rules.meal_plan_coverage import ThinRange

RECEIVED = datetime(2026, 11, 10, 19, 17, tzinfo=UTC)  # 한국 2026-11-11
NOV = "2026년 11월 급식계획 — 지어낸 글자 스무 자 이상"
NOV_FRONT = (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)  # 평일 10일
NOV_BACK = (16, 17, 18, 19, 20, 23, 24, 25, 26, 27, 30)  # 평일 11일


def _lunch(day: int) -> dict:
    return {"day": day, "meals": [{"meal_type": "lunch", "items": [{"name": "밥", "raw": "밥"}]}]}


def _output(days: list[dict], unparsed: list[dict] | None = None, ym: str = "2026-11"):
    return OcrOutput.model_validate(
        {
            "institution_name": None,
            "year_month": ym,
            "days": days,
            "unparsed": unparsed or [],
            "notes": None,
        }
    )


def _read(monkeypatch: pytest.MonkeyPatch, parts: ReadParts, transcript: str = NOV):
    class _Reader:
        def read_parts(self, image: bytes, *, mime_type: str) -> ReadParts:
            return parts

    monkeypatch.setattr(notice_ocr, "normalize_image", lambda data: (data, "image/png"))
    monkeypatch.setattr(
        notice_ocr.NoticeExtractor,
        "from_settings",
        classmethod(
            lambda cls, settings, **kwargs: SimpleNamespace(
                version="test",
                read_image=lambda image, *, mime_type: SimpleNamespace(transcript=transcript),
            )
        ),
    )
    monkeypatch.setattr(
        notice_ocr.ImageOcrMealPlanReader,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _Reader()),
    )
    monkeypatch.setattr(
        notice_ocr,
        "assemble",
        lambda extract, **kwargs: SimpleNamespace(doc_kind=NoticeDocKind.MEAL_PLAN),
    )
    return notice_ocr.read_photo(b"image", received_at=RECEIVED)


def _whys(reading) -> list[str]:
    return [cell.why for cell in reading.meal_plan.unparsed]


def test_뒤_구간이_하루만_읽혔으면_한_줄을_내고_읽은_급식은_그대로_둔다(monkeypatch) -> None:
    parts = ReadParts(
        outputs=(_output([_lunch(d) for d in NOV_FRONT]), _output([_lunch(16)])),
        ranges=DEFAULT_DAY_RANGES,
    )

    reading = _read(monkeypatch, parts)

    assert reading.meal_failed is False
    assert [d.day for d in reading.meal_plan.days] == [*NOV_FRONT, 16]  # 저장 대상 그대로
    assert _whys(reading) == ["16일~30일 급식 중 일부만 읽었어요. 사진과 비교해 주세요."]
    assert reading.meal_thin == (ThinRange(16, 30, 1, 11),)


def test_정상_한_달은_줄이_없다(monkeypatch) -> None:
    parts = ReadParts(
        outputs=(
            _output([_lunch(d) for d in NOV_FRONT]),
            _output([_lunch(d) for d in NOV_BACK]),
        ),
        ranges=DEFAULT_DAY_RANGES,
    )

    reading = _read(monkeypatch, parts)

    assert _whys(reading) == []
    assert reading.meal_thin == ()


def test_휴일로_적힌_날과_못_읽은_칸의_날은_읽은_날이다(monkeypatch) -> None:
    # 2025-08 방학: 앞 구간 평일 11일 중 급식은 5일. 모델이 방학 날을 휴일(note)로 냈고, 한 날은
    # 못 읽은 칸으로 냈다 — 화면에 흔적이 있거나 모델이 그 날을 답했다. 조용히 빠진 날이 아니다
    holidays = [{"day": d, "meals": [], "note": "방학"} for d in (4, 5, 6, 7)]
    parts = ReadParts(
        outputs=(
            _output(
                [_lunch(d) for d in (1, 11, 12, 13, 14)] + holidays,
                unparsed=[{"day": 8, "meal_type": "lunch", "raw": None, "why": "흐림"}],
                ym="2025-08",
            ),
            _output([_lunch(d) for d in (18, 19, 20, 21, 22, 25, 26, 27, 28, 29)], ym="2025-08"),
        ),
        ranges=DEFAULT_DAY_RANGES,
    )

    reading = _read(monkeypatch, parts, transcript="2025년 8월 급식계획 — 지어낸 글자 스무 자")

    assert reading.meal_thin == ()
    assert _whys(reading) == ["흐림"]


def test_끼니도_휴일_표시도_없는_날은_읽은_날로_세지_않는다(monkeypatch) -> None:
    # 날짜만 내고 끼니를 비운 날은 화면에 아무것도 안 나간다 — 조용히 빠진 것과 같다
    empty = [{"day": d, "meals": []} for d in NOV_BACK[1:]]
    parts = ReadParts(
        outputs=(_output([_lunch(d) for d in NOV_FRONT]), _output([_lunch(16), *empty])),
        ranges=DEFAULT_DAY_RANGES,
    )

    reading = _read(monkeypatch, parts)

    assert reading.meal_thin == (ThinRange(16, 30, 1, 11),)


def test_한_주치_식단표는_줄이_없다(monkeypatch) -> None:
    parts = ReadParts(
        outputs=(_output([_lunch(d) for d in (9, 10, 11, 12, 13)]), _output([])),
        ranges=DEFAULT_DAY_RANGES,
    )

    reading = _read(monkeypatch, parts)

    assert _whys(reading) == []


def test_한_구간에서_하나도_못_찾으면_찾지_못했다고_알린다(monkeypatch) -> None:
    # 1~15일만 있는 급식표일 수도 있다 — "일부만 읽었어요" 가 아니라 날짜가 있는지 보라고 한다
    parts = ReadParts(
        outputs=(_output([_lunch(d) for d in NOV_FRONT]), _output([])),
        ranges=DEFAULT_DAY_RANGES,
    )

    reading = _read(monkeypatch, parts)

    assert _whys(reading) == [
        "16일~30일 급식은 찾지 못했어요. 사진에 이 날짜가 있는지 확인해 주세요."
    ]


def test_한_구간이_통째로_실패했으면_읽지_못했어요_한_줄만_남는다(monkeypatch) -> None:
    parts = ReadParts(
        outputs=(_output([_lunch(d) for d in NOV_FRONT]),),
        missed=(MissedRange(16, 31),),
        ranges=DEFAULT_DAY_RANGES,
    )

    reading = _read(monkeypatch, parts)

    assert _whys(reading) == ["16일~30일 급식은 읽지 못했어요. 더 밝게 다시 찍어 주세요."]
    assert reading.meal_thin == ()
