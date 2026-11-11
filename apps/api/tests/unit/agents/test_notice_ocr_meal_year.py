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
from app.rules.meal_plan_month import MealMonth

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


def _read(
    monkeypatch: pytest.MonkeyPatch, transcript: str, *year_months: str, received_at=RECEIVED
):
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
    return notice_ocr.read_photo(b"image", received_at=received_at)


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
    # 🚨 추정했다는 것을 남긴다 — 화면이 "연도가 없어서 2026년 11월로 읽었어요" 를 그린다
    assert reading.meal_month == MealMonth(11, 2026, "guessed")


def test_원문에_없는_연도는_버리고_받은_날로_정한다(monkeypatch) -> None:
    reading = _read(monkeypatch, NO_YEAR, "2022-11", "2022-11")

    assert reading.meal_plan is not None
    assert reading.meal_plan.year_month == "2026-11"
    assert reading.meal_month == MealMonth(11, 2026, "guessed")


def test_원문에_적힌_연도는_그대로_둔다(monkeypatch) -> None:
    transcript = "2025년 1월 영양식단 — 지어낸 글자 스무 자 이상"
    reading = _read(monkeypatch, transcript, "2025-01", "2025-01")

    assert reading.meal_plan is not None
    assert reading.meal_plan.year_month == "2025-01"
    assert reading.meal_month == MealMonth(1, 2025, "written")


def test_원문에_연도가_없고_앞뒤_한_달_밖이면_연도를_정하지_않는다(monkeypatch) -> None:
    # 11월에 받은 3월 급식표 — 공지 규칙(60일)이면 "내년 3월" 이 된다. 짐작하지 않는다.
    # 급식을 날짜로 내보내지 않는다 — 화면에는 "언제 급식표인지 알 수 없어요" 한 줄 (photo_run)
    reading = _read(monkeypatch, "[3]월 급식계획 — 흑미밥 미역국", "3", "3")

    assert reading.meal_plan is None
    assert reading.meal_failed is True
    assert reading.meal_month == MealMonth(3, None, "unknown")


def test_받은_날은_한국_날짜로_본다(monkeypatch) -> None:
    # UTC 11-30 15:30 = 한국 12-01 00:30. 한국 날짜로는 10월이 두 달 전이라 정하지 않는다
    # (UTC 날짜로 보면 지난달이라 2026-10 으로 정해 버린다)
    late = datetime(2026, 11, 30, 15, 30, tzinfo=UTC)

    october = _read(monkeypatch, "[10]월 급식계획 — 흑미밥", "10", received_at=late)
    january = _read(monkeypatch, "[1]월 급식계획 — 흑미밥", "1", received_at=late)

    assert october.meal_month == MealMonth(10, None, "unknown")
    assert january.meal_month == MealMonth(1, 2027, "guessed")


def test_한쪽_호출만_실패하면_성공한_쪽_급식은_살고_실패한_범위가_확인_항목이_된다(
    monkeypatch,
) -> None:
    # 전에는 한쪽만 실패해도 성공한 쪽까지 버려 표 전체가 "식단표의 표를 읽지 못했어요" 였다
    from app.providers.meal_plan.image_ocr import MissedRange

    class _HalfReader:
        def read_parts(self, image: bytes, *, mime_type: str) -> ReadParts:
            return ReadParts(outputs=(_output("11", 2),), missed=(MissedRange(16, 31),))

    monkeypatch.setattr(notice_ocr, "normalize_image", lambda data: (data, "image/png"))
    monkeypatch.setattr(
        notice_ocr.NoticeExtractor,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _Extractor(NO_YEAR)),
    )
    monkeypatch.setattr(
        notice_ocr.ImageOcrMealPlanReader,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _HalfReader()),
    )
    monkeypatch.setattr(
        notice_ocr,
        "assemble",
        lambda extract, **kwargs: SimpleNamespace(doc_kind=NoticeDocKind.MEAL_PLAN),
    )

    reading = notice_ocr.read_photo(b"image", received_at=RECEIVED)

    assert reading.meal_failed is False
    assert reading.meal_plan is not None
    assert [d.day for d in reading.meal_plan.days] == [2]
    assert [u.why[:14] for u in reading.meal_plan.unparsed] == ["16일~30일 급식은 읽지"]
