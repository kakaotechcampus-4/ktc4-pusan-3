"""공지 읽기의 사용량 기록 — 급식표 OCR 과 같은 약속 (test_meal_plan_image_ocr_usage.py)."""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.core import llm_usage
from app.providers.notice.extractor import NoticeExtractor

_EXTRACT = (
    '{"doc_kind": "newsletter", "audience_scope": "institution", "title": null,'
    ' "issued_text": null, "base_month_text": null, "is_correction": false,'
    ' "person_names": [], "institution_names": [], "items": [], "transcript": "안내"}'
)


@pytest.fixture(autouse=True)
def _usage_queue() -> Iterator[None]:
    llm_usage.disable()
    llm_usage.drain(limit=10_000)
    llm_usage.enable()
    yield
    llm_usage.disable()
    llm_usage.drain(limit=10_000)


def _stream(*, content: str | None = _EXTRACT, finish_reason: str = "stop") -> list[Any]:
    usage = SimpleNamespace(
        prompt_tokens=700,
        completion_tokens=300,
        cached_prompt_tokens=100,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=50),
    )

    def chunk(choices: list, chunk_usage: Any = None) -> Any:
        return SimpleNamespace(choices=choices, usage=chunk_usage, model="served-notice-model")

    def choice(text: str | None, reason: str | None) -> Any:
        return SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=reason)

    return [chunk([choice(content, None)]), chunk([choice(None, finish_reason)]), chunk([], usage)]


def _extractor(stream: list[Any]) -> NoticeExtractor:
    extractor = NoticeExtractor(
        base_url="https://gw.example.invalid/v1", api_key="test-key", model="notice-model"
    )
    completions = SimpleNamespace(create=lambda **_: stream)
    extractor._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return extractor


async def test_호출_1번이_notice_ocr_행_1개다() -> None:
    parent_id = uuid4()
    extractor = _extractor(_stream())

    with llm_usage.attribute(parent_id=parent_id, run_id="photo-1", source="meal_ocr"):
        await asyncio.to_thread(extractor.read_image, b"png", mime_type="image/png")

    [row] = llm_usage.drain()
    assert (row.role, row.model, row.parent_id) == ("notice_ocr", "served-notice-model", parent_id)
    assert (row.prompt_tokens, row.completion_tokens, row.reasoning_tokens) == (700, 300, 50)


async def test_잘린_응답도_예외보다_먼저_기록한다() -> None:
    extractor = _extractor(_stream(content=None, finish_reason="length"))

    with llm_usage.attribute(parent_id=uuid4(), run_id="photo-1", source="meal_ocr"):
        with pytest.raises(RuntimeError, match="잘림"):
            await asyncio.to_thread(extractor.read_image, b"png", mime_type="image/png")

    [row] = llm_usage.drain()
    assert row.completion_tokens == 300
