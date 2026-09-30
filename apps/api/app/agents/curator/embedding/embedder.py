# Curator 임베딩 호출. 문자열 목록을 받아 같은 순서의 벡터 목록을 돌려준다

import logging
import time
from collections.abc import Sequence
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from app.agents.common.config import AgentSettings, get_agent_settings
from app.agents.common.llm_client import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)

# observation_* · profile_affinity 의 embedding 열이 vector(1536) 이다
EMBEDDING_DIM = 1536

# 요청 한 번에 보내는 최대 개수. OpenAI 임베딩 API 의 입력 개수 상한이 2048 이다
MAX_BATCH = 2048


class Embedder:
    """embeddings.create 왕복만 책임진다. 무엇을 임베딩할지는 부르는 쪽이 정한다.

    EMBEDDING_* 는 MEMORY_* 로 대체하지 않는다. 비어 있으면 생성할 때 LLMConfigError.
    서버 부팅은 막지 않는다 — Curator 가 부를 때 만들고, 실패하면 임베딩만 건너뛴다.
    """

    def __init__(self, settings: AgentSettings | None = None, *, client: Any = None) -> None:
        s = settings or get_agent_settings()
        api_key = s.EMBEDDING_API_KEY.strip()
        base_url = s.EMBEDDING_BASE_URL.strip()
        model = s.EMBEDDING_MODEL.strip()
        missing = [
            name
            for name, value in (
                ("EMBEDDING_API_KEY", api_key),
                ("EMBEDDING_BASE_URL", base_url),
                ("EMBEDDING_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise LLMConfigError(f"{' / '.join(missing)} 가 비어 있다. apps/api/.env 를 확인한다.")

        self._model = model
        # 테스트는 embeddings.create 만 있는 가짜를 넣는다
        self._client = client or AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=s.LLM_TIMEOUT_S,
            max_retries=s.LLM_MAX_RETRIES,
        )

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """texts 와 같은 순서·같은 개수의 벡터를 돌려준다. 빈 목록이면 호출하지 않는다."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            vectors.extend(await self._embed_batch(list(texts[start : start + MAX_BATCH])))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        started = time.perf_counter()
        try:
            response = await self._client.embeddings.create(model=self._model, input=texts)
        except AuthenticationError as exc:
            raise LLMAuthError("임베딩 인증 실패. EMBEDDING_API_KEY 를 확인한다.") from exc
        except BadRequestError as exc:
            # 서버가 돌려준 사유만 담는다. 입력 문자열(아이 관찰)은 싣지 않는다
            raise LLMBadRequestError(f"임베딩 요청 거절: {exc}") from exc
        except RateLimitError as exc:
            raise LLMRateLimitError("임베딩 rate limit 초과.") from exc
        except APIConnectionError as exc:
            raise LLMUnavailableError("임베딩 연결 실패 또는 타임아웃.") from exc
        except APIError as exc:
            raise LLMError(f"임베딩 호출 실패: {exc}") from exc

        # 응답 순서가 입력 순서라는 보장은 index 로만 한다
        data = sorted(response.data, key=lambda item: item.index)
        if len(data) != len(texts):
            raise LLMError(f"임베딩 개수가 다르다. 보낸 {len(texts)}개, 받은 {len(data)}개.")
        vectors = [list(item.embedding) for item in data]
        wrong = {len(v) for v in vectors if len(v) != EMBEDDING_DIM}
        if wrong:
            # 저장 전에 막는다. 모델을 바꾸면 여기서 먼저 걸린다
            raise LLMError(
                f"임베딩 차원이 {sorted(wrong)} 이다. vector({EMBEDDING_DIM}) 열에 넣을 수 없다. "
                "EMBEDDING_MODEL 을 확인한다."
            )

        logger.info(
            "embedding call model=%s inputs=%d tokens=%s latency_ms=%d",
            self._model,
            len(texts),
            getattr(response.usage, "total_tokens", None),
            int((time.perf_counter() - started) * 1000),
        )
        return vectors
