"""급식표 OCR 의 사용량 기록 (BM 산정).

OCR 은 도메인 Agent 와 모델이 다르고 동기 클라이언트라 스레드에서 돈다.
부르는 쪽이 믿는 약속만 본다.
    - 호출 1번이 role=meal_ocr 행 1개다 — 날짜 범위를 나눠 부르면 호출 수만큼 남는다
    - 보호자 · run 은 라우트가 건 귀속(asyncio.to_thread 로 넘어온다)에서 온다
    - 출력 상한에서 잘리거나 거절된 응답도 과금되므로, 예외를 던지기 전에 기록한다
"""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.core import llm_usage
from app.providers.meal_plan.image_ocr import ImageOcrMealPlanReader

PNG_HEADER = b"\x89PNG\r\n\x1a\n"
BASE_URL = "https://ocr-gw.example.invalid:8443/v1?key=secret"
_PLAN = (
    '{"institution_name": null, "year_month": "2025-01", "days": [], "unparsed": [], "notes": null}'
)


@pytest.fixture(autouse=True)
def _usage_queue() -> Iterator[None]:
    llm_usage.disable()
    llm_usage.drain(limit=10_000)
    llm_usage.enable()
    yield
    llm_usage.disable()
    llm_usage.drain(limit=10_000)


def _response(
    *,
    content: str | None = _PLAN,
    finish_reason: str = "stop",
    model: str | None = "served-ocr-model",
    usage: Any = "default",
) -> Any:
    """스트리밍 응답 — 글자 조각 · 끝 이유 조각 · 사용량 조각 (게이트웨이가 보내는 순서).

    급식표는 스트리밍으로 받는다 (providers/streaming.py — 비스트리밍은 출력 2,000 토큰에서 잘린다).
    """
    if usage == "default":
        usage = SimpleNamespace(
            prompt_tokens=900,
            completion_tokens=400,
            cached_prompt_tokens=300,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=120),
        )

    def chunk(choices: list, chunk_usage: Any = None) -> Any:
        fields: dict[str, Any] = {"choices": choices, "usage": chunk_usage}
        if model is not None:
            fields["model"] = model
        return SimpleNamespace(**fields)

    def choice(text: str | None, reason: str | None) -> Any:
        return SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=reason)

    return [chunk([choice(content, None)]), chunk([choice(None, finish_reason)]), chunk([], usage)]


class _FakeCompletions:
    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses)

    def create(self, **_params: Any) -> Any:
        return self._responses.pop(0)


def _reader(*responses: Any, **overrides: Any) -> ImageOcrMealPlanReader:
    reader = ImageOcrMealPlanReader(
        base_url=BASE_URL,
        api_key="test-key",
        model="ocr-model",
        **{"day_ranges": None, **overrides},
    )
    reader._client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(*responses)))
    return reader


async def test_호출_1번이_meal_ocr_행_1개로_남고_보호자는_to_thread_로_넘어온다() -> None:
    parent_id = uuid4()
    reader = _reader(_response())

    with llm_usage.attribute(parent_id=parent_id, run_id="upload-1", source="meal_ocr"):
        await asyncio.to_thread(reader.read, PNG_HEADER, mime_type="image/png")

    [row] = llm_usage.drain()
    assert (row.parent_id, row.run_id, row.source) == (parent_id, "upload-1", "meal_ocr")
    assert row.role == "meal_ocr"
    assert row.model == "served-ocr-model"  # 설정값(ocr-model)이 아니라 응답에 찍힌 모델
    assert row.provider == "ocr-gw.example.invalid"  # 키 · 포트 · 쿼리 없이 host 만
    assert (row.prompt_tokens, row.cached_prompt_tokens) == (900, 300)
    assert (row.completion_tokens, row.reasoning_tokens) == (400, 120)


async def test_응답에_모델이_없으면_설정한_모델로_남긴다() -> None:
    reader = _reader(_response(model=None))

    with llm_usage.attribute(parent_id=uuid4(), run_id="upload-1", source="meal_ocr"):
        await asyncio.to_thread(reader.read, PNG_HEADER, mime_type="image/png")

    [row] = llm_usage.drain()
    assert row.model == "ocr-model"


async def test_날짜_범위를_나눠_부르면_호출마다_한_행이다() -> None:
    reader = _reader(_response(), _response(), day_ranges=((1, 15), (16, 31)))

    with llm_usage.attribute(parent_id=uuid4(), run_id="upload-1", source="meal_ocr"):
        await asyncio.to_thread(reader.read, PNG_HEADER, mime_type="image/png")

    assert len(llm_usage.drain()) == 2


async def test_출력_상한에서_잘린_응답도_예외보다_먼저_기록한다() -> None:
    """잘린 응답도 토큰이 과금된다 — 실패했다고 비용이 사라지지 않는다."""
    reader = _reader(_response(content=None, finish_reason="length"))

    with llm_usage.attribute(parent_id=uuid4(), run_id="upload-1", source="meal_ocr"):
        with pytest.raises(RuntimeError, match="잘림"):
            await asyncio.to_thread(reader.read, PNG_HEADER, mime_type="image/png")

    [row] = llm_usage.drain()
    assert (row.role, row.completion_tokens) == ("meal_ocr", 400)


async def test_추론_토큰이_숫자가_아니어도_읽기는_정상으로_돌아온다() -> None:
    """기록 때문에 이미 성공한 OCR 이 실패하면 안 된다."""
    usage = SimpleNamespace(
        prompt_tokens=900,
        completion_tokens=400,
        cached_prompt_tokens=300,
        completion_tokens_details=SimpleNamespace(reasoning_tokens="n/a"),
    )
    reader = _reader(_response(usage=usage))

    with llm_usage.attribute(parent_id=uuid4(), run_id="upload-1", source="meal_ocr"):
        await asyncio.to_thread(reader.read, PNG_HEADER, mime_type="image/png")

    [row] = llm_usage.drain()
    assert (row.completion_tokens, row.reasoning_tokens) == (400, 0)


async def test_사용량이_응답에_없으면_기록하지_않는다() -> None:
    reader = _reader(_response(usage=None))

    with llm_usage.attribute(parent_id=uuid4(), run_id="upload-1", source="meal_ocr"):
        await asyncio.to_thread(reader.read, PNG_HEADER, mime_type="image/png")

    assert llm_usage.drain() == []
