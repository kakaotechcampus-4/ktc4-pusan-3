"""Curator 백그라운드 트리거.

관찰 저장 트랜잭션이 커밋된 뒤, 별도 세션에서 Curator 를 돌린다.
실패해도 관찰은 이미 저장돼 있고, 벡터·연결이 안 된 관찰은
다음 실행(list_unembedded / list_unlinked)이 자동으로 집는다.

호출 시점: pipeline.handle_input 에서 Memory Agent 완료 후.
현재는 InMemoryStore 라 아직 연결되지 않았다 — DB 저장소가 붙으면 활성화한다.
"""

import asyncio
import logging
from datetime import date
from uuid import UUID

from app.agents.curator.embedding.embed_step import TextEmbedder
from app.agents.curator.embedding.judge import IdentityJudge
from app.agents.curator.embedding.linker import link_observations
from app.domains.memory.curator.db_store import DbCuratorStore
from app.domains.memory.curator.recompute import recompute_after_linking
from app.infra.db.session import async_session_factory

logger = logging.getLogger(__name__)

# asyncio 는 태스크를 약하게만 잡는다. 여기 잡아두지 않으면 GC 가 도중에 거둬
# Curator 가 조용히 사라진다 (runner.py 주석 참고).
_background_tasks: set[asyncio.Task[None]] = set()


async def _run_curator(
    child_id: UUID,
    today: date,
    embedder: TextEmbedder,
    judge: IdentityJudge | None,
) -> None:
    """별도 세션·트랜잭션에서 Curator 를 돌리고 recompute 한다."""
    async with async_session_factory() as session:
        try:
            store = DbCuratorStore(session)
            result = await link_observations(store, embedder, judge, child_id=child_id)

            if result.affected_profile_ids:
                await recompute_after_linking(session, result=result, today=today)

            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("curator 실행 실패 child_id=%s", child_id)


def trigger_curator_background(
    child_id: UUID,
    today: date,
    embedder: TextEmbedder,
    judge: IdentityJudge | None,
) -> asyncio.Task[None]:
    """백그라운드 태스크로 Curator 를 띄운다. 기다리지 않는다.

    관찰 저장 트랜잭션이 커밋된 뒤에 불러야 한다 —
    같은 트랜잭션에서 부르면 실패가 관찰 저장까지 되돌린다.
    """
    task = asyncio.create_task(
        _run_curator(child_id, today, embedder, judge),
        name=f"curator:{child_id}",
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
