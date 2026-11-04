"""Curator 판정 · 임베딩의 사용량 기록 (BM 산정).

두 호출은 LLMClient 를 거치지 않아서 따로 기록한다. 부르는 쪽이 믿는 약속만 본다.
    - 판정은 role=curator_judge, 임베딩은 role=embedding 으로 도메인 Agent 와 갈라서 남는다
    - 모델은 응답에 찍힌 실제 모델이다 (없으면 설정값)
    - 보호자 · run 은 귀속에서 온다 — Curator 는 run 이 끝난 뒤에 돌아도 그 run 의 것이다
    - 셀 수 있는 값이 없는 응답은 0 토큰 행을 만들지 않는다
"""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.agents.common.config import AgentSettings
from app.agents.curator.embedding import embedder as embedder_module
from app.agents.curator.embedding.embedder import EMBEDDING_DIM, Embedder
from app.agents.curator.embedding.jev import JevJudge
from app.core import llm_usage

JUDGE_BASE = "https://judge-gw.example.invalid/v1"
EMBED_BASE = "https://embed-gw.example.invalid:8443/v1?key=secret"


@pytest.fixture(autouse=True)
def _usage_queue() -> Iterator[None]:
    llm_usage.disable()
    llm_usage.drain(limit=10_000)
    llm_usage.enable()
    yield
    llm_usage.disable()
    llm_usage.drain(limit=10_000)


def _judge(body: dict[str, Any]) -> JevJudge:
    settings = AgentSettings(
        _env_file=None,
        CURATOR_JUDGE_API_KEY="test-key",
        CURATOR_JUDGE_BASE_URL=JUDGE_BASE,
        CURATOR_JUDGE_MODEL="typesafe/jev-1.13",
    )
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=body))
    return JevJudge(settings, transport=transport)


def _judge_body(**overrides: Any) -> dict[str, Any]:
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {"identity": {"type": "choice", "choice": "딸기"}},
        "usage": {"input_tokens": 395, "output_tokens": 0},
        **overrides,
    }


async def _ask(judge: JevJudge) -> None:
    await judge.judge(subject="생딸기", domain="food", candidates=["딸기", "블루베리"])


class _FakeEmbeddings:
    def __init__(self, *, usage: Any = "default", model: str | None = "served-embed") -> None:
        self._usage = usage
        self._model = model

    async def create(self, *, model: str, input: list[str]) -> Any:
        data = [
            SimpleNamespace(index=i, embedding=[0.0] * EMBEDDING_DIM) for i in range(len(input))
        ]
        usage = SimpleNamespace(total_tokens=len(input) * 7) if self._usage == "default" else None
        fields: dict[str, Any] = {"data": data, "usage": usage}
        if self._model is not None:
            fields["model"] = self._model
        return SimpleNamespace(**fields)


def _embedder(fake: _FakeEmbeddings) -> Embedder:
    settings = AgentSettings(
        _env_file=None,
        EMBEDDING_API_KEY="test-key",
        EMBEDDING_BASE_URL=EMBED_BASE,
        EMBEDDING_MODEL="text-embedding-3-small",
    )
    return Embedder(settings, client=SimpleNamespace(embeddings=fake))


async def test_판정_1번이_curator_judge_행_1개로_남는다() -> None:
    parent_id = uuid4()

    with llm_usage.attribute(parent_id=parent_id, run_id="run-1", source="input"):
        await _ask(_judge(_judge_body()))

    [row] = llm_usage.drain()
    assert (row.parent_id, row.run_id, row.source) == (parent_id, "run-1", "input")
    assert row.role == "curator_judge"
    assert row.model == "typesafe/jev-1.13-20260917"  # 설정값이 아니라 응답에 찍힌 모델
    assert row.provider == "judge-gw.example.invalid"
    assert (row.prompt_tokens, row.completion_tokens) == (395, 0)
    assert row.latency_ms >= 0


async def test_판정_응답에_모델이_없으면_설정한_모델로_남긴다() -> None:
    body = _judge_body()
    del body["model"]

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        await _ask(_judge(body))

    [row] = llm_usage.drain()
    assert row.model == "typesafe/jev-1.13"


async def test_판정_응답에_사용량이_없으면_기록하지_않는다() -> None:
    body = _judge_body()
    del body["usage"]

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        await _ask(_judge(body))

    assert llm_usage.drain() == []


async def test_판정_응답의_출력_토큰이_숫자가_아니어도_판정은_정상으로_돌아온다() -> None:
    """기록 때문에 이미 성공한 판정이 실패하면 안 된다."""
    body = _judge_body(usage={"input_tokens": 395, "output_tokens": "n/a"})

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        await _ask(_judge(body))

    [row] = llm_usage.drain()
    assert (row.prompt_tokens, row.completion_tokens) == (395, 0)


async def test_run_이_끝난_뒤에_끝나는_판정도_그_run_에_남는다() -> None:
    """Curator 는 commit 뒤 백그라운드 태스크로 돈다 — run 보다 늦게 끝난다."""
    parent_id = uuid4()
    judge = _judge(_judge_body())
    gate = asyncio.Event()

    async def curator() -> None:
        await gate.wait()
        await _ask(judge)

    with llm_usage.attribute(parent_id=parent_id, run_id="run-9", source="input"):
        task = asyncio.create_task(curator())

    gate.set()
    await task

    [row] = llm_usage.drain()
    assert (row.parent_id, row.run_id, row.role) == (parent_id, "run-9", "curator_judge")


async def test_임베딩_1번이_embedding_행_1개로_남고_토큰은_total_tokens_다() -> None:
    parent_id = uuid4()

    with llm_usage.attribute(parent_id=parent_id, run_id="run-1", source="input"):
        await _embedder(_FakeEmbeddings()).embed(["딸기", "레고"])

    [row] = llm_usage.drain()
    assert (row.parent_id, row.run_id) == (parent_id, "run-1")
    assert row.role == "embedding"
    assert row.model == "served-embed"
    assert row.provider == "embed-gw.example.invalid"  # 키 · 포트 · 쿼리 없이 host 만
    assert (row.prompt_tokens, row.completion_tokens) == (14, 0)


async def test_임베딩_응답에_모델이_없으면_설정한_모델로_남긴다() -> None:
    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        await _embedder(_FakeEmbeddings(model=None)).embed(["딸기"])

    [row] = llm_usage.drain()
    assert row.model == "text-embedding-3-small"


async def test_임베딩이_여러_묶음으로_나뉘면_묶음마다_한_행이다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embedder_module, "MAX_BATCH", 2)

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        await _embedder(_FakeEmbeddings()).embed(["a", "b", "c"])

    assert [r.prompt_tokens for r in llm_usage.drain()] == [14, 7]


async def test_임베딩_응답에_사용량이_없으면_기록하지_않는다() -> None:
    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        await _embedder(_FakeEmbeddings(usage=None)).embed(["딸기"])

    assert llm_usage.drain() == []
