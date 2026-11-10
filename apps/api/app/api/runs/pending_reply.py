"""되묻기 맥락 보관 — run_id → PendingMemoryContext (#877 · docs/ops/stateless-v1.md 1단계)

Memory 가 저장하지 못한 조각을 임시 보관하고, 후속 입력(reply_to)에서 꺼내 Agent 에 전달한다.
표(`run_hold`, UNLOGGED)에 둔다 — 워커가 여럿이어도 다음 요청이 같은 맥락을 본다.

- 함수마다 자기 세션을 열고 바로 commit 한다. 요청 트랜잭션과 섞으면 아이 삭제(아이 행
  FOR UPDATE)가 FK 잠금(FOR KEY SHARE) 때문에 그 요청이 끝날 때까지 기다린다.
  테스트는 `_session_factory` 를 바꿔 끼운다.
- 원문 · 질문이 들어 있다. 15분 뒤 꺼낼 수 없고(`TTL`), 지우는 것은 `hold_purge` 가 1분마다 한다.
  로그에 내용을 남기지 않는다.
- 🚨 답이 오면 지우지 않고 "사용 중" 으로 둔다(#564). 이어받기 run 이 저장 전에 실패하면
  표시만 풀고, 15분은 처음 넣은 시각 그대로 센다.
- 탈퇴 · 아이 삭제는 FK CASCADE, 연결 끊기는 `run_hold.repository.delete_link_holds` 가 지운다.
- `PendingMemoryContext` 는 pydantic 으로 JSON 이 됐다가 돌아온다. agents 의
  `DomainPending.payload` 는 이미 "JSON 으로 바뀌는 값만" 이 규칙이라(`agents/common/result.py`)
  agents 코드는 고치지 않는다.
"""

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.entrypoint import PendingMemoryContext
from app.api.deps.db import background_session
from app.domains.run_hold.models import HoldKind
from app.domains.run_hold.repository import (
    claim_hold,
    drop_hold,
    get_hold,
    open_hold_keys,
    put_hold,
    release_hold,
)

log = logging.getLogger(__name__)

TTL = timedelta(minutes=15)
_KIND = HoldKind.PENDING_REPLY
_ADAPTER = TypeAdapter(PendingMemoryContext)

_session_factory: Callable[[], AsyncSession] = background_session


async def put(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    context: PendingMemoryContext,
    now: datetime | None = None,
) -> bool:
    """넣는다. 저장했으면 True.

    보호자 · 아이가 이미 지워졌으면(그 아이로 돌던 run 이 늦게 끝난 경우) False — 원문을 남기지 않는
    것이 맞는 결과라 예외를 내지 않는다. 그 밖의 DB 오류는 올린다.
    """
    payload = _ADAPTER.dump_python(context, mode="json")
    async with _session_factory() as session:
        try:
            await put_hold(
                session,
                kind=_KIND,
                key=run_id,
                parent_id=parent_id,
                child_id=child_id,
                payload=payload,
                ttl=TTL,
                now=now,
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            log.warning("run %s 의 되묻기 맥락을 넣지 않았다 — 보호자 · 아이가 없다", run_id)
            return False
    return True


async def _load(run_id: str, payload: dict | None) -> PendingMemoryContext | None:
    """행을 맥락으로 되돌린다. 못 읽으면(맥락 모양을 바꾼 배포 뒤의 옛 행) 그 행을 지우고 None.

    🚨 검증 오류의 글에는 원문 조각(input_value)이 실린다 — 예외 종류만 남기고 올리지 않는다.
       올리면 처리되지 않은 예외로 글 전체가 로그에 찍히고, claim 이 켠 "사용 중" 이 15분 남는다
       (#899 리뷰). 지금처럼 "이어서 확인할 수 없어요"(400)로 끝난다.
    """
    if payload is None:
        return None
    try:
        return _ADAPTER.validate_python(payload)
    except Exception as exc:
        log.warning("run %s 의 되묻기 맥락을 읽지 못해 지운다 — %s", run_id, type(exc).__name__)
        await drop(run_id)
        return None


async def get(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, now: datetime | None = None
) -> PendingMemoryContext | None:
    """확인만 한다. 없음 · 남의 것 · 다른 아이 · 사용 중 · 만료를 하나로 합친다(None)."""
    async with _session_factory() as session:
        payload = await get_hold(
            session, kind=_KIND, key=run_id, parent_id=parent_id, child_id=child_id, now=now
        )
    return await _load(run_id, payload)


async def claim(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, now: datetime | None = None
) -> PendingMemoryContext | None:
    """답이 왔다. 맥락을 넘기고 "사용 중" 으로 둔다 — 같은 질문에 두 번 답하면 관찰이 두 행이 된다.

    🚨 확인과 표시가 DB 한 문장이다. 같은 reply_to 로 거의 동시에 온 두 답 중 하나만 받는다 —
       워커가 달라도 같다.
    """
    async with _session_factory() as session:
        payload = await claim_hold(
            session, kind=_KIND, key=run_id, parent_id=parent_id, child_id=child_id, now=now
        )
        await session.commit()
    return await _load(run_id, payload)


async def release(run_id: str) -> None:
    """표시만 푼다. 15분은 처음 넣은 때부터 센다. 이미 지워졌으면 할 일이 없다."""
    async with _session_factory() as session:
        await release_hold(session, kind=_KIND, key=run_id)
        await session.commit()


async def drop(run_id: str) -> None:
    """이어받기 run 이 저장까지 갔다. 다시 답할 일이 없으니 지운다."""
    async with _session_factory() as session:
        await drop_hold(session, kind=_KIND, key=run_id)
        await session.commit()


async def open_runs(
    *, run_ids: Sequence[str], parent_id: uuid.UUID, child_id: uuid.UUID
) -> set[str]:
    """그중 지금 답할 수 있는 run. 대화 하루치의 `reply_open` 을 한 번에 본다."""
    if not run_ids:
        return set()
    async with _session_factory() as session:
        return await open_hold_keys(
            session, kind=_KIND, keys=run_ids, parent_id=parent_id, child_id=child_id
        )
