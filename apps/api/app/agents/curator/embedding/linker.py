"""Curator 임베딩 파트의 입구. 임베딩 단계와 연결 단계를 차례로 돌리고 결과를 합친다.

    from app.agents.curator.embedding import Embedder, link_observations

    result = await link_observations(store, Embedder(), child_id=child_id)
    result.affected_profile_ids  # 이번에 근거가 늘어난 Profile — 승격·감쇠가 다시 볼 대상

부르는 쪽이 정할 것
    - 언제 부르는가: 관찰이 commit 된 뒤. 같은 트랜잭션에서 부르면 실패가 관찰 저장까지 되돌린다
    - 동시 실행: 같은 아이에 대해 두 번 동시에 돌지 않게 막는다 (DB 구현의 몫)
    - Embedder(): EMBEDDING_* 가 비면 LLMConfigError 를 낸다. 잡아서 건너뛸지는 부르는 쪽이 정한다

저장소 오류와 LLMError 가 아닌 예외는 잡지 않고 올린다.

지금 검증된 것은 인메모리 저장소 기준의 처리 흐름이다. DB 에 붙일 때 남은 것
    - 동시 실행 제어: 같은 아이의 두 실행이 같은 대상으로 candidate 를 두 번 만들 수 있다
    - 저장 중 실패: 벡터 저장 · 연결 · candidate 생성 사이에서 멈췄을 때의 트랜잭션 경계
    - API 대기 중 subject 변경: 임베딩을 기다리는 사이 관찰이 수정되면 옛 subject 의 벡터가
      새 subject 에 저장될 수 있다. 저장할 때 subject 가 그대로인지 확인해야 한다
"""

import logging
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from app.agents.curator.embedding.embed_step import TextEmbedder, embed_pending
from app.agents.curator.embedding.link_step import (
    DEFAULT_THRESHOLD,
    LinkOutcome,
    check_threshold,
    link_pending,
)
from app.agents.curator.embedding.ports import CuratorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkResult:
    """관찰마다 결과가 하나씩 있다.

    순서는 연결 단계 결과(저장된 순서) → 임베딩 실패 → 빈 subject.
    두 단계의 대상은 겹치지 않는다 — 벡터가 없는 관찰은 연결 대상이 아니다.
    """

    child_id: UUID
    outcomes: tuple[LinkOutcome, ...] = ()
    # embed() 호출 횟수. Embedder 안의 배치 분할 · SDK 재시도 때문에
    # 실제 HTTP 요청 수와 다를 수 있다
    embed_calls: int = 0

    @property
    def affected_profile_ids(self) -> tuple[str, ...]:
        """이번에 연결되거나 새로 생긴 Profile. 중복 없이 처음 나온 순서."""
        return tuple(dict.fromkeys(o.affinity_id for o in self.outcomes if o.affinity_id))

    @property
    def created_profile_ids(self) -> tuple[str, ...]:
        return tuple(o.affinity_id for o in self.outcomes if o.created and o.affinity_id)

    @property
    def held(self) -> tuple[LinkOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == "held")


async def link_observations(
    store: CuratorStore,
    embedder: TextEmbedder,
    *,
    child_id: UUID,
    threshold: float = DEFAULT_THRESHOLD,
) -> LinkResult:
    # 임베딩 API 를 부르고 저장한 뒤에 거절되지 않게 맨 앞에서 본다
    check_threshold(threshold)

    embedded = await embed_pending(store, embedder, child_id=child_id)
    # 임베딩이 실패해도 돌린다 — 지난 실행에서 벡터만 저장되고 연결 전에 멈춘 관찰이 있다
    linked = await link_pending(store, child_id=child_id, threshold=threshold)

    outcomes = (
        *linked.outcomes,
        *(LinkOutcome(key, "held", reason="embedding_failed") for key in embedded.failed),
        *(LinkOutcome(key, "held", reason="empty_subject") for key in embedded.skipped),
    )
    result = LinkResult(child_id, outcomes, embed_calls=embedded.embed_calls)

    statuses = Counter(o.status for o in outcomes)
    reasons = Counter(o.reason for o in outcomes if o.reason)
    # 원문(subject)은 남기지 않는다. 건수만
    logger.info(
        "curator link child_id=%s linked=%d created=%d held=%d reasons=%s embed_calls=%d",
        child_id,
        statuses["linked"],
        statuses["created"],
        statuses["held"],
        dict(reasons),
        embedded.embed_calls,
    )
    return result
