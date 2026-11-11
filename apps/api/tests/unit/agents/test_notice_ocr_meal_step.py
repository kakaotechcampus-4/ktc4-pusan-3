"""사진 읽기 — 급식표로 판정되면 표 읽기 전에 한 번 알린다 (진행 문구를 바꾸려고).

알리는 쪽(agents)은 받는 쪽이 무엇을 하는지 모른다. 받는 쪽이 터져도 읽기는 계속된다 —
진행 문구 때문에 읽기가 실패하면 안 된다. 모델은 부르지 않는다.
"""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agents import notice_ocr
from app.domains.notice.models import NoticeDocKind
from app.providers.meal_plan.image_ocr import OcrOutput, ReadParts

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
# 표 읽기가 낸 한 장. 읽기 결과로 만든 식단이 나왔는지는 이 값(11월 · 2일 · 흑미밥)으로 본다
OUTPUT = OcrOutput.model_validate(
    {
        "institution_name": None,
        "year_month": "2026-11",
        "days": [
            {
                "day": 2,
                "meals": [{"meal_type": "lunch", "items": [{"name": "흑미밥", "raw": "흑미밥"}]}],
            }
        ],
        "unparsed": [],
        "notes": None,
    }
)


def _assert_plan_from_reader(reading: notice_ocr.PhotoReading) -> None:
    plan = reading.meal_plan
    assert plan is not None
    assert plan.year_month == "2026-11"
    assert [str(plan.date_of(d.day)) for d in plan.days] == ["2026-11-02"]
    assert [i.name for d in plan.days for m in d.meals for i in m.items] == ["흑미밥"]


def _patch(monkeypatch: pytest.MonkeyPatch, *, doc_kind: NoticeDocKind, order: list[str]) -> None:
    class _Extractor:
        version = "test"

        def read_image(self, image: bytes, *, mime_type: str) -> SimpleNamespace:
            return SimpleNamespace(transcript="11월 식단표 — 지어낸 글자 스무 자 이상")

    class _MealReader:
        def read_parts(self, image: bytes, *, mime_type: str) -> ReadParts:
            order.append("read")
            return ReadParts(outputs=(OUTPUT,))

    monkeypatch.setattr(notice_ocr, "normalize_image", lambda data: (data, "image/png"))
    monkeypatch.setattr(
        notice_ocr.NoticeExtractor,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _Extractor()),
    )
    monkeypatch.setattr(
        notice_ocr.ImageOcrMealPlanReader,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _MealReader()),
    )
    monkeypatch.setattr(
        notice_ocr, "assemble", lambda extract, **kwargs: SimpleNamespace(doc_kind=doc_kind)
    )


def test_급식표면_표_읽기_전에_한_번_알린다(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    _patch(monkeypatch, doc_kind=NoticeDocKind.MEAL_PLAN, order=order)

    reading = notice_ocr.read_photo(
        b"image", received_at=NOW, on_meal_plan=lambda: order.append("told")
    )

    assert order == ["told", "read"]
    _assert_plan_from_reader(reading)


def test_공지면_알리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    _patch(monkeypatch, doc_kind=NoticeDocKind.NEWSLETTER, order=order)

    reading = notice_ocr.read_photo(
        b"image", received_at=NOW, on_meal_plan=lambda: order.append("told")
    )

    assert order == []
    assert reading.meal_plan is None


def test_알리는_쪽이_터져도_표는_그대로_읽는다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    order: list[str] = []
    _patch(monkeypatch, doc_kind=NoticeDocKind.MEAL_PLAN, order=order)

    def boom() -> None:
        raise RuntimeError("synthetic-secret")

    with caplog.at_level(logging.WARNING, logger="app.agents.notice_ocr"):
        reading = notice_ocr.read_photo(b"image", received_at=NOW, on_meal_plan=boom)

    assert order == ["read"]
    _assert_plan_from_reader(reading)
    assert reading.meal_failed is False
    # 예외 종류만 남긴다 — 메시지는 로그에 싣지 않는다
    assert "RuntimeError" in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_알릴_곳이_없어도_읽는다(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    _patch(monkeypatch, doc_kind=NoticeDocKind.MEAL_PLAN, order=order)

    reading = notice_ocr.read_photo(b"image", received_at=NOW)

    assert order == ["read"]
    _assert_plan_from_reader(reading)
