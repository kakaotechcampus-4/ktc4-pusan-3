"""식단표 읽기의 기한 — 지나면 새 호출을 시작하지 않고, 받던 응답도 끊고 연결을 닫는다."""

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.providers.meal_plan import image_ocr
from app.providers.meal_plan.image_ocr import ImageOcrMealPlanReader, ReadDeadlineExceeded

PNG = b"\x89PNG\r\n\x1a\n"


class _Stream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self) -> None:
        self.closed = True


class _Completions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: Any) -> _Stream:
        self.calls += 1
        return _Stream([])


def _reader(deadline: float | None) -> tuple[ImageOcrMealPlanReader, _Completions]:
    reader = ImageOcrMealPlanReader(
        base_url="http://model.test", api_key="k", model="m", max_calls=2, deadline=deadline
    )
    completions = _Completions()
    reader._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return reader, completions


def test_기한이_이미_지났으면_모델을_부르지_않는다() -> None:
    reader, completions = _reader(time.monotonic() - 1)

    with pytest.raises(ReadDeadlineExceeded):
        reader.read(PNG, mime_type="image/png")

    assert completions.calls == 0
    assert reader.calls == 0


def test_받던_응답이_기한을_넘으면_멈추고_연결을_닫는다(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _Stream(["첫 조각", "둘째 조각", "셋째 조각"])
    clock = iter([0.0, 5.0, 11.0])
    monkeypatch.setattr(image_ocr.time, "monotonic", lambda: next(clock))
    got: list[str] = []

    with pytest.raises(ReadDeadlineExceeded):
        for chunk in image_ocr._until(stream, 10.0):
            got.append(chunk)

    assert got == ["첫 조각", "둘째 조각"]
    assert stream.closed is True


def test_기한_안에_끝나도_연결을_닫는다() -> None:
    stream = _Stream(["하나"])

    assert list(image_ocr._until(stream, time.monotonic() + 60)) == ["하나"]
    assert stream.closed is True


def test_기한이_없으면_스트림을_그대로_쓴다() -> None:
    reader, _ = _reader(None)
    stream = _Stream([])

    assert reader._within_deadline(stream) is stream


def test_기한_초과는_모델_호출_실패로도_잡힌다() -> None:
    # 부르는 쪽이 따로 안 잡아도 처리 안 된 예외가 되지 않게 RuntimeError 의 자식으로 둔다
    assert issubclass(ReadDeadlineExceeded, RuntimeError)
