"""임베딩 단계. 이 아이의 벡터가 없는 관찰을 한 번에 임베딩해 저장한다.

연결은 하지 않는다 — 연결 단계가 저장된 벡터를 읽어 따로 한다.
실패하면 아무것도 저장하지 않는다. 벡터가 비어 있으니 다음 실행이 다시 집는다.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.agents.common.llm_client import LLMError
from app.agents.curator.embedding.ports import CuratorStore, ObservationKey

logger = logging.getLogger(__name__)


class TextEmbedder(Protocol):
    """Embedder 가 만족하는 모양. 테스트는 가짜를 넣는다."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class EmbedStepResult:
    embedded: tuple[ObservationKey, ...] = ()  # 벡터를 저장한 관찰
    failed: tuple[ObservationKey, ...] = ()  # 임베딩 실패. 벡터가 비어 있는 채로 남는다
    skipped: tuple[ObservationKey, ...] = ()  # 보내지 않은 관찰 (subject 가 빈 문자열)
    # embed() 호출 횟수. 대상이 없으면 0. Embedder 안의 배치 분할 · SDK 재시도 때문에
    # 실제 HTTP 요청 수와 다를 수 있다
    embed_calls: int = 0


async def embed_pending(
    store: CuratorStore, embedder: TextEmbedder, *, child_id: UUID
) -> EmbedStepResult:
    pending = await store.list_unembedded(child_id=child_id)

    # 빈 subject 는 API 가 요청 전체를 거절한다. 섞어 보내면 같은 아이의 다른 관찰까지
    # 매번 같이 실패하므로 미리 뺀다
    skipped = tuple(item.key for item in pending if not item.subject.strip())
    targets = [item for item in pending if item.subject.strip()]
    if not targets:
        return EmbedStepResult(skipped=skipped)

    # 같은 문자열은 같은 벡터다. 한 번만 보낸다 (비슷한 표현을 묶는 건 연결 단계의 유사도)
    texts = list(dict.fromkeys(item.subject.strip() for item in targets))
    try:
        vectors = await embedder.embed(texts)
    except LLMError as exc:
        # 원문(subject)은 남기지 않는다. 개수와 에러 종류만
        logger.warning(
            "curator embedding failed child_id=%s observations=%d texts=%d error=%s",
            child_id,
            len(targets),
            len(texts),
            type(exc).__name__,
        )
        return EmbedStepResult(
            failed=tuple(item.key for item in targets), skipped=skipped, embed_calls=1
        )

    by_text = dict(zip(texts, vectors, strict=True))
    await store.save_embeddings(
        vectors={item.key: by_text[item.subject.strip()] for item in targets}
    )
    return EmbedStepResult(
        embedded=tuple(item.key for item in targets), skipped=skipped, embed_calls=1
    )
