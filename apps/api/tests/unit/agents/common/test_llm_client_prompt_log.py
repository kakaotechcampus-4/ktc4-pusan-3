"""호출 로그에 프롬프트 이름과 지문이 붙는다 — 본문 · 발화는 여전히 없다 (#770)."""

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents import prompt_registry  # noqa: F401  (프롬프트 등록)
from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMClient
from app.agents.common.prompt_fingerprint import prompt_fingerprint
from app.core import llm_usage


@pytest.fixture(autouse=True)
def _no_usage_queue():
    llm_usage.disable()
    yield


def _client(role: str) -> LLMClient:
    settings = AgentSettings(
        _env_file=None,
        MEMORY_API_KEY="test-key",
        MEMORY_BASE_URL="https://gw.example.invalid/v1",
        MEMORY_MODEL="memory-model",
        MEMORY_REASONING_EFFORT="none",
    )
    client = LLMClient(settings, role=role)  # type: ignore[arg-type]
    message = SimpleNamespace(content="ok", tool_calls=None)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        model="m",
    )

    class _Completions:
        async def create(self, **_params: Any) -> Any:
            return response

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    return client


async def _line(caplog: pytest.LogCaptureFixture, client: LLMClient, **kwargs: Any) -> str:
    with caplog.at_level(logging.INFO, logger="app.agents.common.llm_client"):
        await client.chat([{"role": "user", "content": "비밀 문장"}], **kwargs)
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("llm call")]
    return line


async def test_이름을_안_주면_역할_이름의_프롬프트_지문이_붙는다(caplog) -> None:
    line = await _line(caplog, _client("supervisor"))

    assert "prompt_name=supervisor" in line
    assert f"prompt_sha256={prompt_fingerprint('supervisor').short}" in line


async def test_이름을_주면_그_프롬프트의_지문이_붙는다(caplog) -> None:
    line = await _line(caplog, _client("activity"), prompt_name="activity_event_schedule")

    assert "prompt_name=activity_event_schedule" in line
    assert f"prompt_sha256={prompt_fingerprint('activity_event_schedule').short}" in line


async def test_모르는_이름이면_대시이고_호출은_정상이다(caplog) -> None:
    line = await _line(caplog, _client("supervisor"), prompt_name="없는이름")

    assert "prompt_name=- " in line
    assert "prompt_sha256=- " in line


async def test_지문을_못_만들어도_호출은_정상이다(caplog, monkeypatch) -> None:
    from app.agents.common import prompt_fingerprint as pf

    pf.logged_fingerprint.cache_clear()

    def boom(_name: str) -> Any:
        raise RuntimeError("렌더링 실패")

    monkeypatch.setattr(pf, "prompt_fingerprint", boom)
    line = await _line(caplog, _client("supervisor"))

    assert "prompt_sha256=- " in line
    pf.logged_fingerprint.cache_clear()


async def test_본문과_발화는_로그에_없다(caplog) -> None:
    line = await _line(caplog, _client("supervisor"))

    assert "비밀 문장" not in line
    from app.agents.supervisor.prompt import build_system_prompt

    assert build_system_prompt()[:20] not in line
