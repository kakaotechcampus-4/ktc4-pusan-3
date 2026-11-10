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

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
PLAN = SimpleNamespace(days=())


def _patch(monkeypatch: pytest.MonkeyPatch, *, doc_kind: NoticeDocKind, order: list[str]) -> None:
    class _Extractor:
        version = "test"

        def read_image(self, image: bytes, *, mime_type: str) -> SimpleNamespace:
            return SimpleNamespace(transcript="11월 식단표 — 지어낸 글자 스무 자 이상")

    class _MealReader:
        def read(self, image: bytes, *, mime_type: str) -> SimpleNamespace:
            order.append("read")
            return PLAN

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
    assert reading.meal_plan is PLAN


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
    assert reading.meal_plan is PLAN
    assert reading.meal_failed is False
    # 예외 종류만 남긴다 — 메시지는 로그에 싣지 않는다
    assert "RuntimeError" in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_알릴_곳이_없어도_읽는다(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    _patch(monkeypatch, doc_kind=NoticeDocKind.MEAL_PLAN, order=order)

    reading = notice_ocr.read_photo(b"image", received_at=NOW)

    assert order == ["read"]
    assert reading.meal_plan is PLAN
