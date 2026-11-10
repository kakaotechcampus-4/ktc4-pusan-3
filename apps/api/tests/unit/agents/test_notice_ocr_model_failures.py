"""사진 읽기 — 모델 호출이 SDK 오류로 실패해도 처리 안 된 예외가 되지 않는다.

SDK 오류(시간 초과 · 429 · 인증)는 RuntimeError 가 아니다. RuntimeError 만 잡던 때에는
식단표 호출 하나가 실패해도 읽은 공지까지 버리고 internal_error 로 끝났다.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from app.agents import notice_ocr
from app.domains.notice.models import NoticeDocKind
from app.providers.meal_plan.image_ocr import ReadDeadlineExceeded

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _timeout() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("POST", "http://model.test"))


class _Extractor:
    version = "test"

    def __init__(self, *, fails: bool) -> None:
        self._fails = fails

    def read_image(self, image: bytes, *, mime_type: str) -> SimpleNamespace:
        if self._fails:
            raise _timeout()
        return SimpleNamespace(transcript="11월 식단표 — 지어낸 글자 스무 자 이상")


class _MealReader:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def read(self, image: bytes, *, mime_type: str) -> None:
        raise self._error


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    notice_fails: bool,
    meal_error: Exception | None = None,
    seen: dict | None = None,
) -> None:
    def meal_reader(cls, settings, **kwargs):
        if seen is not None:
            seen.update(kwargs)
        return _MealReader(meal_error or _timeout())

    monkeypatch.setattr(notice_ocr, "normalize_image", lambda data: (data, "image/png"))
    monkeypatch.setattr(
        notice_ocr.NoticeExtractor,
        "from_settings",
        classmethod(lambda cls, settings, **kwargs: _Extractor(fails=notice_fails)),
    )
    monkeypatch.setattr(
        notice_ocr.ImageOcrMealPlanReader,
        "from_settings",
        classmethod(meal_reader),
    )
    monkeypatch.setattr(
        notice_ocr,
        "assemble",
        lambda extract, **kwargs: SimpleNamespace(doc_kind=NoticeDocKind.MEAL_PLAN),
    )


def test_공지_읽기가_시간_초과면_읽지_못했어요_다(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, notice_fails=True)

    with pytest.raises(notice_ocr.UnreadablePhoto):
        notice_ocr.read_photo(b"image", received_at=NOW)


def test_식단표_호출만_실패하면_공지_항목은_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, notice_fails=False)

    reading = notice_ocr.read_photo(b"image", received_at=NOW)

    assert reading.meal_failed is True
    assert reading.meal_plan is None
    assert reading.notice.doc_kind is NoticeDocKind.MEAL_PLAN


def test_식단표_읽기가_기한을_넘으면_공지는_남고_시간_탓으로_표시한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}
    _patch(monkeypatch, notice_fails=False, meal_error=ReadDeadlineExceeded("기한"), seen=seen)

    reading = notice_ocr.read_photo(b"image", received_at=NOW, meal_deadline=123.0)

    assert reading.meal_failed is True
    assert reading.meal_timed_out is True
    assert reading.notice.doc_kind is NoticeDocKind.MEAL_PLAN
    # 받은 기한을 식단표 읽기에 그대로 넘긴다
    assert seen["deadline"] == 123.0


def test_모델_호출_실패는_시간_탓으로_표시하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, notice_fails=False)

    reading = notice_ocr.read_photo(b"image", received_at=NOW)

    assert reading.meal_failed is True
    assert reading.meal_timed_out is False
