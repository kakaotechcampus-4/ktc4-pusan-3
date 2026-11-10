"""사진 읽기 — 급식표의 연도는 코드가 정한다 (루트 CLAUDE.md §2 · §3, 2026-11-11 운영 장애).

연도 없는 급식표("[11]월 급식계획")에서 모델이 `year_month` 를 "11" 로 내면 형식 검사에 걸려
표 전체가 버려졌고, 형식은 맞지만 지어낸 연도("2022-11")를 내면 날짜 전부가 틀렸다
(범위 밖이면 저장이 400).
월은 모델이 읽은 것을, 연도는 사진 글자에 적혀 있을 때만 믿고 아니면 받은 날로 정한다.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agents import notice_ocr
from app.domains.notice.models import NoticeDocKind
from app.providers.meal_plan.image_ocr import OcrOutput, ReadParts

# 한국 2026-11-11 04:17 — UTC 로는 11-10 이다. 받은 날은 한국 날짜로 본다
RECEIVED = datetime(2026, 11, 10, 19, 17, tzinfo=UTC)


def _output(year_month: str, first_day: int) -> OcrOutput:
    return OcrOutput.model_validate(
        {
            "institution_name": None,
            "year_month": year_month,
            "days": [
                {
                    "day": first_day,
                    "meals": [
                        {"meal_type": "lunch", "items": [{"name": "흑미밥", "raw": "흑미밥"}]}
                    ],
                }
            ],
            "unparsed": [],
            "notes": None,
        }
    )


class _Extractor:
    version = "test"

    def __init__(self, transcript: str) -> None:
        self._transcript = transcript

    def read_image(self, image: bytes, *, mime_type: str) -> SimpleNamespace:
        return SimpleNamespace(transcript=self._transcript)


class _MealReader:
    def __init__(self, *year_months: str) -> None:
        self._year_months = year_months

    def read_parts(self, image: bytes, *, mime_type: str) -> ReadParts:
        return ReadParts(
            outputs=tuple(
                _output(ym, day) for ym, day in zip(self._year_months, (2, 17), strict=False)
            )
        )


def _read(monkeypatch: pytest.MonkeyPatch, transcript: str, *year_months: str):
    monkeypatch.setattr(notice_ocr, "normalize_image", lambda data: (data, "image/png"))
    monkeypatch.setattr(
        notice_ocr.NoticeExtractor,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _Extractor(transcript)),
    )
    monkeypatch.setattr(
        notice_ocr.ImageOcrMealPlanReader,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _MealReader(*year_months)),
    )
    monkeypatch.setattr(
        notice_ocr,
        "assemble",
        lambda extract, **kwargs: SimpleNamespace(doc_kind=NoticeDocKind.MEAL_PLAN),
    )
    return notice_ocr.read_photo(b"image", received_at=RECEIVED)


NO_YEAR = "[11]월 급 식 계 획 — 흑미밥 팽이버섯된장국 총 471 kcal"


def test_모델이_월만_내도_표가_살고_받은_날의_연도가_된다(monkeypatch) -> None:
    reading = _read(monkeypatch, NO_YEAR, "11", "11")

    assert reading.meal_failed is False
    assert reading.meal_plan is not None
    assert reading.meal_plan.year_month == "2026-11"
    assert [str(reading.meal_plan.date_of(d.day)) for d in reading.meal_plan.days] == [
        "2026-11-02",
        "2026-11-17",
    ]


def test_원문에_없는_연도는_버리고_받은_날로_정한다(monkeypatch) -> None:
    reading = _read(monkeypatch, NO_YEAR, "2022-11", "2022-11")

    assert reading.meal_plan is not None
    assert reading.meal_plan.year_month == "2026-11"


def test_원문에_적힌_연도는_그대로_둔다(monkeypatch) -> None:
    transcript = "2025년 1월 영양식단 — 지어낸 글자 스무 자 이상"
    reading = _read(monkeypatch, transcript, "2025-01", "2025-01")

    assert reading.meal_plan is not None
    assert reading.meal_plan.year_month == "2025-01"


@pytest.mark.parametrize("year_months", [("급식", "급식"), ("11", "12")])
def test_월을_정할_수_없으면_표를_못_읽은_것이다(monkeypatch, year_months) -> None:
    # 공지 항목은 살리고, 화면에는 "식단표의 표를 읽지 못했어요" 가 선다 (photo_run.build_entries)
    reading = _read(monkeypatch, NO_YEAR, *year_months)

    assert reading.meal_plan is None
    assert reading.meal_failed is True
    assert reading.meal_timed_out is False
