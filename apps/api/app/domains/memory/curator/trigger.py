"""Curator 백그라운드 트리거.

관찰 저장 트랜잭션이 커밋된 뒤, 별도 세션에서 Curator 를 돌린다.
실패해도 관찰은 이미 저장돼 있고, 벡터·연결이 안 된 관찰은
다음 실행(list_unembedded / list_unlinked)이 자동으로 집는다.

호출 시점: Memory 가 commit 된 직후. 러너(api/runs/runner.py `_curator_trigger`)가 이 함수를 감싼
콜백을 만들어 entrypoint 로 넘기고, pipeline 이 (child_id, 기준일) 로 부른다.
"""

import asyncio
import logging
from datetime import date
from uuid import UUID

from sqlalchemy import select, text

from app.agents.curator.embedding.embed_step import TextEmbedder
from app.agents.curator.embedding.judge import IdentityJudge
from app.agents.curator.embedding.linker import link_observations
from app.domains.child.models import Child
from app.domains.memory.curator.db_store import DbCuratorStore
from app.domains.memory.curator.recompute import recompute_after_linking
from app.infra.db.session import async_session_factory

logger = logging.getLogger(__name__)

_CURATOR_LOCK_NS = 1
"""pg_advisory_xact_lock(int4, int4) 의 첫 번째 인자.
다른 기능이 advisory lock 을 쓸 때 다른 값을 준다."""

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
            # 같은 아이의 동시 curator 실행을 직렬화한다.
            # xact_lock 이라 트랜잭션이 끝나면 자동으로 풀린다.
            # 두 int4 오버로드 — ns 로 curator 잠금과 다른 기능의 잠금이 겹치지 않는다.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :key)"),
                {"ns": _CURATOR_LOCK_NS, "key": child_id.int % (2**31)},
            )
            # 잠금 대기 중 바뀐 생일도 반영한다. PATCH 자체를 잠그는 것은 아니다.
            birth_date = (
                await session.execute(
                    select(Child.birth_date).where(Child.id == child_id, Child.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
            if birth_date is None:
                # 대기 중 삭제된 아이는 정상 종료한다. 세션 종료 시 잠금도 해제된다.
                return
            store = DbCuratorStore(session)
            result = await link_observations(
                store, embedder, judge, child_id=child_id, birth_date=birth_date
            )

            if result.affected_profile_ids:
                await recompute_after_linking(session, result=result, today=today)

            await session.commit()
        except Exception as exc:
            await session.rollback()
            # logger.exception 은 traceback 을 찍는데, 그 안에 subject(관찰 대상 이름)가
            # 포함될 수 있다. 에러 타입만 남긴다 (CLAUDE.md §2 개인정보).
            logger.error(
                "curator 실행 실패 child_id=%s error=%s",
                child_id,
                type(exc).__name__,
            )


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
