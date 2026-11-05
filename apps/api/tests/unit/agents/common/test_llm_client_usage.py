"""LLMClient 의 사용량 기록 (BM 산정 — 보호자 · run · 모델별 토큰).

키 없이 가짜 chat.completions 로 돈다. 부르는 쪽이 믿는 약속만 본다.
    - 호출 1번이 기록 1행이다 — role 은 도메인 Agent 별이고, 모델은 응답에 찍힌 실제 모델이다
    - 보호자 · run 은 귀속(core/llm_usage)에서 온다. 클라이언트는 그것을 모른다
    - 로그 줄에 run · 캐시 토큰이 있다 (원문은 없다)
    - 기록이 안 되어도 호출은 정상으로 돌아온다
"""

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMClient, LLMError
from app.core import llm_usage


@pytest.fixture(autouse=True)
def _usage_queue() -> Iterator[None]:
    llm_usage.disable()
    llm_usage.drain(limit=10_000)
    llm_usage.enable()
    yield
    llm_usage.disable()
    llm_usage.drain(limit=10_000)


def _settings() -> AgentSettings:
    # Memory 와 Growth 의 모델이 다르다 — 도메인 Agent 별로 모델이 갈리는 모양
    return AgentSettings(
        _env_file=None,
        MEMORY_API_KEY="test-key",
        MEMORY_BASE_URL="https://memory-gw.example.invalid/v1",
        MEMORY_MODEL="memory-model",
        MEMORY_REASONING_EFFORT="none",
        GROWTH_BASE_URL="https://growth-gw.example.invalid:8443/v1?key=secret",
        GROWTH_MODEL="growth-model",
    )


def _response(*, model: str | None = "served-growth-model", usage: Any = "default") -> Any:
    if usage == "default":
        usage = SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            prompt_tokens_details=SimpleNamespace(cached_tokens=20),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=10),
        )
    message = SimpleNamespace(content="ok", tool_calls=None)
    fields: dict[str, Any] = {"choices": [SimpleNamespace(message=message)], "usage": usage}
    if model is not None:
        fields["model"] = model
    return SimpleNamespace(**fields)


class _FakeCompletions:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def create(self, **_params: Any) -> Any:
        return self._response


def _client(role: str, response: Any) -> LLMClient:
    client = LLMClient(_settings(), role=role)  # type: ignore[arg-type]
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(response)))
    return client


async def test_호출_1번이_기록_1행이고_역할과_실제_모델과_토큰이_담긴다() -> None:
    parent_id = uuid4()
    client = _client("growth", _response())

    with llm_usage.attribute(parent_id=parent_id, run_id="run-1", source="input"):
        await client.chat([{"role": "user", "content": "hi"}])

    [row] = llm_usage.drain()
    assert (row.parent_id, row.run_id, row.source) == (parent_id, "run-1", "input")
    assert row.role == "growth"
    assert row.model == "served-growth-model"  # 설정값(growth-model)이 아니라 응답에 찍힌 모델
    assert row.provider == "growth-gw.example.invalid"  # 키 · 포트 · 쿼리 없이 host 만
    assert (row.prompt_tokens, row.cached_prompt_tokens) == (120, 20)
    assert (row.completion_tokens, row.reasoning_tokens) == (30, 10)
    assert row.latency_ms >= 0


async def test_응답에_모델이_없으면_설정한_모델로_남긴다() -> None:
    client = _client("growth", _response(model=None))

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        await client.chat([{"role": "user", "content": "hi"}])

    [row] = llm_usage.drain()
    assert row.model == "growth-model"


async def test_역할이_다르면_모델과_제공처도_따로_남는다() -> None:
    memory = _client("memory", _response(model="served-memory-model"))
    growth = _client("growth", _response(model="served-growth-model"))

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        await memory.chat([{"role": "user", "content": "hi"}])
        await growth.chat([{"role": "user", "content": "hi"}])

    rows = llm_usage.drain()
    assert [(r.role, r.model, r.provider) for r in rows] == [
        ("memory", "served-memory-model", "memory-gw.example.invalid"),
        ("growth", "served-growth-model", "growth-gw.example.invalid"),
    ]


async def test_사용량이_응답에_없으면_기록하지_않는다() -> None:
    """usage=None — 셀 수 있는 값이 없다. 0 토큰 행을 만들어 평균을 흐리지 않는다."""
    client = _client("growth", _response(usage=None))

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        response = await client.chat([{"role": "user", "content": "hi"}])

    assert response.usage == {}
    assert llm_usage.drain() == []


async def test_제공처가_토큰을_None_으로_줘도_호출은_정상이고_0으로_남는다() -> None:
    usage = SimpleNamespace(prompt_tokens=None, completion_tokens=30)
    client = _client("growth", _response(usage=usage))

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        response = await client.chat([{"role": "user", "content": "hi"}])

    assert response.message.content == "ok"
    [row] = llm_usage.drain()
    assert (row.prompt_tokens, row.completion_tokens) == (0, 30)


async def test_귀속이_없어도_호출은_정상으로_돌아온다(caplog: pytest.LogCaptureFixture) -> None:
    client = _client("growth", _response())

    with caplog.at_level(logging.WARNING, logger="app.core.llm_usage"):
        response = await client.chat([{"role": "user", "content": "hi"}])

    assert response.message.content == "ok"
    assert llm_usage.drain() == []
    assert len(caplog.records) == 1  # 귀속이 빠진 지점을 알리는 경고 한 줄


async def test_로그_줄에_run_과_창구와_캐시_토큰이_있다(caplog: pytest.LogCaptureFixture) -> None:
    client = _client("growth", _response())

    with caplog.at_level(logging.INFO, logger="app.agents.common.llm_client"):
        with llm_usage.attribute(parent_id=uuid4(), run_id="run-42", source="suggest"):
            await client.chat([{"role": "user", "content": "비밀 문장"}])

    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("llm call")]
    assert "run=run-42" in line
    assert "source=suggest" in line
    assert "cached_prompt_tokens=20" in line
    assert "model=served-growth-model" in line
    assert "비밀 문장" not in line  # 발화 원문은 로그에 없다


async def test_귀속이_없으면_로그_줄의_run_은_대시다(caplog: pytest.LogCaptureFixture) -> None:
    client = _client("growth", _response())

    with caplog.at_level(logging.INFO, logger="app.agents.common.llm_client"):
        await client.chat([{"role": "user", "content": "hi"}])

    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("llm call")]
    assert "run=- " in line
    assert "source=- " in line


def _broken_response(*, choices: list[Any]) -> Any:
    """콘텐츠 필터 등으로 choices 가 비거나 message 가 null 로 오는 응답 — 사용량은 들어 있다."""
    usage = SimpleNamespace(prompt_tokens=500, completion_tokens=0)
    return SimpleNamespace(choices=choices, usage=usage, model="served-growth-model")


@pytest.mark.parametrize(
    "response",
    [
        _broken_response(choices=[]),
        _broken_response(choices=[SimpleNamespace(message=None)]),
    ],
    ids=["choices_비어_있음", "message_null"],
)
async def test_message_가_없는_응답도_토큰은_이미_썼으니_기록하고_LLMError_로_끝난다(
    response: Any,
) -> None:
    client = _client("growth", response)

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        with pytest.raises(LLMError, match="message"):
            await client.chat([{"role": "user", "content": "hi"}])

    [row] = llm_usage.drain()
    assert (row.role, row.model, row.prompt_tokens) == ("growth", "served-growth-model", 500)
