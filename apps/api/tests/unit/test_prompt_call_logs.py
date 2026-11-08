"""LLMClient 를 안 거치는 호출(판정기 · 급식표 OCR · 알림장 OCR)의 로그에도 지문이 붙는다 (#770).

로그 줄은 이미 있는 것을 쓴다 — 새 줄을 만들지 않는다. 본문 · 발화는 여전히 없다.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.agents import prompt_registry
from app.agents.common.config import AgentSettings
from app.agents.curator.embedding.jev import JevJudge
from app.core import llm_usage
from app.providers.meal_plan.image_ocr import ImageOcrMealPlanReader
from app.providers.notice.extractor import NoticeExtractor


@pytest.fixture(autouse=True)
def _no_usage_queue():
    llm_usage.disable()
    yield


def _stream(content: str) -> list[Any]:
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        cached_prompt_tokens=0,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )

    def chunk(choices: list, chunk_usage: Any = None) -> Any:
        return SimpleNamespace(choices=choices, usage=chunk_usage, model="m")

    def choice(text: str | None, reason: str | None) -> Any:
        return SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=reason)

    return [chunk([choice(content, None)]), chunk([choice(None, "stop")]), chunk([], usage)]


def _sha(name: str) -> str:
    return prompt_registry.all_fingerprints()[name].short


async def test_판정기_로그_줄에_지문이_붙는다(caplog) -> None:
    settings = AgentSettings(
        _env_file=None,
        CURATOR_JUDGE_API_KEY="k",
        CURATOR_JUDGE_BASE_URL="https://judge-gw.example.invalid/v1",
        CURATOR_JUDGE_MODEL="typesafe/jev-1.13",
    )
    body = {
        "model": "m",
        "answers": {"identity": {"type": "choice", "choice": "딸기"}},
        "usage": {"input_tokens": 3, "output_tokens": 0},
    }
    judge = JevJudge(
        settings, transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body))
    )

    with caplog.at_level(logging.INFO, logger="app.agents.curator.embedding.jev"):
        await judge.judge(subject="생딸기", domain="food", candidates=["딸기", "블루베리"])

    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("curator judge")]
    assert "prompt_name=curator_judge" in line
    assert f"prompt_sha256={_sha('curator_judge')}" in line
    assert "생딸기" not in line


async def test_급식표_OCR_로그_줄에_지문이_붙는다(caplog) -> None:
    plan = (
        '{"institution_name": null, "year_month": "2025-01", "days": [],'
        ' "unparsed": [], "notes": null}'
    )
    reader = ImageOcrMealPlanReader(
        base_url="https://ocr-gw.example.invalid/v1", api_key="k", model="m", day_ranges=None
    )
    completions = SimpleNamespace(create=lambda **_: _stream(plan))
    reader._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    with caplog.at_level(logging.INFO, logger="app.providers.meal_plan.image_ocr"):
        await asyncio.to_thread(reader.read, b"\x89PNG\r\n\x1a\n", mime_type="image/png")

    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("meal_ocr call")]
    assert "prompt_name=meal_ocr" in line
    assert f"prompt_sha256={_sha('meal_ocr')}" in line


async def test_알림장_OCR_로그_줄에_지문이_붙는다(caplog) -> None:
    extract = (
        '{"doc_kind": "newsletter", "audience_scope": "institution", "title": null,'
        ' "issued_text": null, "base_month_text": null, "is_correction": false,'
        ' "person_names": [], "institution_names": [], "items": [], "transcript": "안내"}'
    )
    extractor = NoticeExtractor(base_url="https://gw.example.invalid/v1", api_key="k", model="m")
    completions = SimpleNamespace(create=lambda **_: _stream(extract))
    extractor._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    with caplog.at_level(logging.INFO, logger="app.providers.notice.extractor"):
        await asyncio.to_thread(extractor.read_image, b"png", mime_type="image/png")

    [line] = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("notice_extract call")
    ]
    assert "prompt_name=notice_ocr" in line
    assert f"prompt_sha256={_sha('notice_ocr')}" in line
