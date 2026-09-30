"""임베딩 호출 단위 테스트. 키 없이 가짜 클라이언트로 돈다.

부르는 쪽(Curator)이 믿는 약속만 본다.
    - 입력과 같은 순서 · 같은 개수의 벡터가 온다
    - 1536 차원이 아니면 저장 전에 막힌다
    - 실패는 LLMError 계열로 온다 — 부르는 쪽이 이것만 잡으면 관찰 저장에 영향이 없다
    - EMBEDDING_* 가 비어도 MEMORY_* 로 대체하지 않는다
"""

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMUnavailableError,
)
from app.agents.curator.embedding import embedder as embedder_module
from app.agents.curator.embedding.embedder import EMBEDDING_DIM, Embedder

_REQUEST = httpx.Request("POST", "https://example.invalid/v1/embeddings")


def _settings(**overrides: str) -> AgentSettings:
    values = {
        "EMBEDDING_API_KEY": "test-key",
        "EMBEDDING_BASE_URL": "https://example.invalid/v1",
        "EMBEDDING_MODEL": "text-embedding-3-small",
        **overrides,
    }
    return AgentSettings(_env_file=None, **values)


def _vector(seed: float, dim: int = EMBEDDING_DIM) -> list[float]:
    return [seed] * dim


class FakeEmbeddings:
    """embeddings.create 만 흉내 낸다. 받은 입력을 기록한다."""

    def __init__(
        self,
        *,
        dim: int = EMBEDDING_DIM,
        reverse: bool = False,
        drop: int = 0,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._dim = dim
        self._reverse = reverse
        self._drop = drop
        self._error = error

    async def create(self, *, model: str, input: list[str]) -> Any:
        self.calls.append(list(input))
        if self._error is not None:
            raise self._error
        # 입력 i 번째에는 값이 i 인 벡터를 돌려줘서 순서를 확인할 수 있게 한다
        data = [
            SimpleNamespace(index=i, embedding=_vector(float(i), self._dim))
            for i in range(len(input) - self._drop)
        ]
        if self._reverse:
            data.reverse()
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=len(input)))


def _embedder(fake: FakeEmbeddings, **overrides: str) -> Embedder:
    return Embedder(_settings(**overrides), client=SimpleNamespace(embeddings=fake))


async def test_입력_순서대로_벡터를_돌려준다() -> None:
    fake = FakeEmbeddings(reverse=True)  # 응답 순서가 뒤집혀 와도
    vectors = await _embedder(fake).embed(["딸기", "레고", "한글 자모"])

    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]
    assert all(len(v) == EMBEDDING_DIM for v in vectors)
    assert fake.calls == [["딸기", "레고", "한글 자모"]]  # 요청 한 번으로 보낸다


async def test_빈_목록이면_호출하지_않는다() -> None:
    fake = FakeEmbeddings()
    assert await _embedder(fake).embed([]) == []
    assert fake.calls == []


async def test_상한을_넘으면_나눠_보낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedder_module, "MAX_BATCH", 2)
    fake = FakeEmbeddings()
    vectors = await _embedder(fake).embed(["a", "b", "c"])

    assert fake.calls == [["a", "b"], ["c"]]
    assert len(vectors) == 3


async def test_차원이_다르면_막는다() -> None:
    with pytest.raises(LLMError, match="차원"):
        await _embedder(FakeEmbeddings(dim=3072)).embed(["딸기"])


async def test_개수가_다르면_막는다() -> None:
    with pytest.raises(LLMError, match="개수"):
        await _embedder(FakeEmbeddings(drop=1)).embed(["딸기", "레고"])


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            openai.AuthenticationError(
                "bad key", response=httpx.Response(401, request=_REQUEST), body=None
            ),
            LLMAuthError,
        ),
        (
            openai.RateLimitError(
                "slow", response=httpx.Response(429, request=_REQUEST), body=None
            ),
            LLMRateLimitError,
        ),
        (openai.APIConnectionError(request=_REQUEST), LLMUnavailableError),
    ],
)
async def test_SDK_예외는_LLMError_계열로_바꾼다(error: Exception, expected: type) -> None:
    with pytest.raises(expected):
        await _embedder(FakeEmbeddings(error=error)).embed(["딸기"])


@pytest.mark.parametrize("key", ["EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL"])
def test_설정이_비면_만들_때_막는다(key: str) -> None:
    with pytest.raises(LLMConfigError, match=key):
        Embedder(_settings(**{key: "  "}), client=SimpleNamespace(embeddings=FakeEmbeddings()))


def test_MEMORY_설정으로_대체하지_않는다() -> None:
    settings = AgentSettings(
        _env_file=None,
        MEMORY_API_KEY="memory-key",
        MEMORY_BASE_URL="https://example.invalid/v1",
        MEMORY_MODEL="chat-model",
    )
    with pytest.raises(LLMConfigError, match="EMBEDDING_API_KEY"):
        Embedder(settings)
