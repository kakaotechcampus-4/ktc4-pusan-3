"""식단표 읽기 — 두 호출(1~15일 · 16~31일) 중 한쪽만 실패해도 성공한 쪽 급식은 산다.

전에는 한쪽 호출이 시간 초과 · 출력 잘림 · 형식 오류로 실패하면 성공한 쪽까지 버려 한 달 표
전체가 "식단표의 표를 읽지 못했어요" 였다. 이제 실패한 반쪽만 "○일~○일은 읽지 못했어요"
확인 항목 하나가 된다.
"""

import httpx
import pytest
from openai import APITimeoutError

from app.providers.meal_plan.image_ocr import (
    ImageOcrMealPlanReader,
    MissedRange,
    OcrOutput,
    ReadDeadlineExceeded,
    merge_outputs,
)

PNG = b"\x89PNG\r\n\x1a\n"


def _output(day: int) -> OcrOutput:
    return OcrOutput.model_validate(
        {
            "institution_name": None,
            "year_month": "11",
            "days": [
                {
                    "day": day,
                    "meals": [
                        {"meal_type": "lunch", "items": [{"name": "흑미밥", "raw": "흑미밥"}]}
                    ],
                }
            ],
            "unparsed": [],
            "notes": None,
        }
    )


def _timeout() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("POST", "http://model.test"))


def _reader(monkeypatch: pytest.MonkeyPatch, outcomes: dict) -> ImageOcrMealPlanReader:
    reader = ImageOcrMealPlanReader(base_url="http://model.test", api_key="k", max_calls=2)
    calls: list = []

    def call(image: bytes, mime_type: str, day_range):
        calls.append(day_range)
        result = outcomes[day_range]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(reader, "_call", call)
    reader.seen_ranges = calls  # type: ignore[attr-defined]
    return reader


@pytest.mark.parametrize(
    ("error", "timed_out"),
    [
        (RuntimeError("응답이 출력 상한에서 잘림"), False),
        (RuntimeError("모델 응답을 급식표로 파싱 실패"), False),
        (_timeout(), False),
        (ReadDeadlineExceeded("읽기 기한이 지나 응답 받기를 멈춤"), True),
    ],
)
def test_뒤쪽만_실패하면_앞쪽은_살고_실패한_범위를_남긴다(monkeypatch, error, timed_out) -> None:
    reader = _reader(monkeypatch, {(1, 15): _output(2), (16, 31): error})

    parts = reader.read_parts(PNG, mime_type="image/png")

    assert [d.day for p in parts.outputs for d in p.days] == [2]
    assert parts.missed == (MissedRange(16, 31, timed_out=timed_out),)


def test_앞쪽이_실패해도_뒤쪽을_부른다(monkeypatch) -> None:
    reader = _reader(monkeypatch, {(1, 15): _timeout(), (16, 31): _output(17)})

    parts = reader.read_parts(PNG, mime_type="image/png")

    assert reader.seen_ranges == [(1, 15), (16, 31)]  # type: ignore[attr-defined]
    assert [d.day for p in parts.outputs for d in p.days] == [17]
    assert parts.missed == (MissedRange(1, 15, timed_out=False),)


def test_둘_다_실패하면_지금처럼_실패한다(monkeypatch) -> None:
    first = RuntimeError("앞")
    reader = _reader(monkeypatch, {(1, 15): first, (16, 31): _timeout()})

    with pytest.raises(RuntimeError) as caught:
        reader.read_parts(PNG, mime_type="image/png")

    assert caught.value is first


def test_기한이_앞에서_지나면_뒤쪽은_부르지_않고_기한_초과로_끝난다(monkeypatch) -> None:
    # 기한이 지났으면 뒤쪽 호출도 시작하지 않는다(`_call` 이 막는다) — 둘 다 실패라 기한 초과다
    reader = ImageOcrMealPlanReader(
        base_url="http://model.test", api_key="k", max_calls=2, deadline=0.0
    )

    with pytest.raises(ReadDeadlineExceeded):
        reader.read_parts(PNG, mime_type="image/png")
    assert reader.calls == 0


def test_실패한_범위는_그_달의_말일까지로_확인_항목_하나가_된다() -> None:
    plan = merge_outputs(
        [_output(2)], year_month="2026-11", missed=(MissedRange(16, 31, timed_out=False),)
    )

    assert [d.day for d in plan.days] == [2]
    (cell,) = plan.unparsed
    assert (cell.day, cell.raw) == (None, None)
    assert cell.why.startswith("16일~30일 급식은 읽지 못했어요")


def test_시간이_모자라_못_읽은_범위는_다시_찍으라고_하지_않는다() -> None:
    plan = merge_outputs(
        [_output(2)], year_month="2026-11", missed=(MissedRange(16, 31, timed_out=True),)
    )

    (cell,) = plan.unparsed
    assert "시간" in cell.why and "찍어" not in cell.why
