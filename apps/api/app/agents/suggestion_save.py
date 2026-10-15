"""도메인 Agent 결과 하나의 추천을 저장하는 자리.

pipeline 은 Agent 결과가 나오는 대로 추천 묶음을 저장 함수에 넘기고, 저장(commit)이 끝나
돌아온 id 를 화면 이벤트(`AgentResult.suggestion_ids`)에 싣는다. 저장 → 카드 순서다
(공통규약 §3) — 카드의 승인 · 거절 · 피드백은 저장된 행의 id 로 동작한다.

저장 함수는 commit 콜백처럼 러너가 만들어 넘긴다(트랜잭션 회의 결정 2). DB 세션이 러너에
있어서다. 묶음 하나를 트랜잭션 하나로 저장 · commit 하고 id 를 drafts 순서로 돌려준다.
실패하면 예외를 낸다. 근거 검증 · `draft` · `+24h` 는 BE(`domains/suggestion`)가 정한다.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.agents.common.suggestion import SuggestionDraft


@dataclass(frozen=True)
class SuggestionBatch:
    """Agent 결과 하나의 추천. 이 단위로 트랜잭션 하나다."""

    run_id: str | None  # 05 의 홈 · 04 제안 요청은 run 이 없다
    agent: str
    task_type: str | None
    drafts: tuple[SuggestionDraft, ...]


SaveSuggestions = Callable[[SuggestionBatch], Awaitable[tuple[str, ...]]]


@dataclass
class InMemorySuggestions:
    """테스트 · eval 용 저장 함수. 받은 묶음을 쌓고 추천마다 새 id 를 돌려준다."""

    batches: list[SuggestionBatch] = field(default_factory=list)

    async def __call__(self, batch: SuggestionBatch) -> tuple[str, ...]:
        self.batches.append(batch)
        return tuple(str(uuid.uuid4()) for _ in batch.drafts)
