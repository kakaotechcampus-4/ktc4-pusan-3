"""러너와 05 추천 엔드포인트가 같이 쓰는 Agent 포트 — 짧은 DB 세션으로 연다 (#257 · #249).

도메인 Agent 의 기억 포트는 호출마다, 추천 저장은 Agent 결과 하나마다 세션 하나다. 요청 세션이나
run 세션을 같이 타면 동시에 도는 task 들이 AsyncSession 을 함께 써서 깨진다.
기록 건수도 호출마다 세션 하나다.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from app.agents import entrypoint
from app.api.deps.db import background_session

# 그 Agent 의 "쌓인 기록 N건" 을 셀 관찰 종류 (루트 §2). 04 블록(#227)과 05(#249)가 같은 수를
# 말하게 한 곳에 둔다. 표에 없는 Agent 는 0건이다
RECORD_KINDS: dict[str, tuple[str, ...]] = {
    "food": ("observation_food",),
    "activity": ("observation_activity",),
}


def domain_store_opener(child_id: UUID) -> entrypoint.StoreOpener:
    from app.domains.memory.store.db_store import DbMemoryStore

    @asynccontextmanager
    async def domain_store() -> AsyncIterator[DbMemoryStore]:
        # 읽기만 한다. Memory 는 도메인 단계 전에 commit 되므로 방금 저장한 관찰도 보인다
        async with background_session() as read_session:
            yield DbMemoryStore(read_session, child_id=child_id)

    return domain_store


def suggestion_saver(
    child_id: UUID, *, on_commit: Callable[[], None] | None = None
) -> entrypoint.SaveSuggestions:
    from app.domains.suggestion.repository import save_agent_suggestions

    async def save_suggestions(batch: entrypoint.SuggestionBatch) -> tuple[str, ...]:
        # Agent 결과 하나마다 짧은 세션 · 트랜잭션 하나다(트랜잭션 회의 표)
        async with background_session() as save_session:
            ids = await save_agent_suggestions(
                save_session,
                child_id=child_id,
                run_id=batch.run_id,
                payloads=[draft.to_payload() for draft in batch.drafts],
                now=datetime.now(UTC),
            )
            await save_session.commit()
            # commit 이 돌아온 뒤에만 알린다. 결과를 모르면 표시가 없어 failed 다(결정 4)
            if on_commit is not None:
                on_commit()
        return tuple(str(row_id) for row_id in ids)

    return save_suggestions


def record_counter(child_id: UUID) -> entrypoint.CountRecords:
    from app.domains.suggestion.repository import count_child_records

    async def count_records(agent: str) -> int:
        # 읽기만 한다. 결과 하나마다 짧은 세션 — run 세션을 도메인 task 들과 같이 타지 않는다
        async with background_session() as session:
            return await count_child_records(
                session, child_id=child_id, kinds=RECORD_KINDS.get(agent, ())
            )

    return count_records
