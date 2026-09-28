"""되묻기 맥락 보관 — run_id → PendingMemoryContext

Memory가 저장하지 못한 조각을 임시 보관하고, 후속 입력(reply_to)에서 꺼내 Agent에 전달한다.

프로세스 메모리 기반: 재시작/멀티 워커 간 공유 불가.
추후 DB·Redis 전환 시 put/get/consume만 교체.
RunChannel과 분리하며, 원문·질문을 포함하므로 15분만 보관하고 로그는 남기지 않는다.
"""

import time
import uuid
from dataclasses import dataclass

from app.agents.entrypoint import PendingMemoryContext

TTL_SECONDS = 900.0


@dataclass(frozen=True)
class _Entry:
    parent_id: uuid.UUID
    child_id: uuid.UUID
    context: PendingMemoryContext
    stored_at: float


_pending: dict[str, _Entry] = {}


def put(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    context: PendingMemoryContext,
    now: float | None = None,
) -> None:
    moment = time.monotonic() if now is None else now
    # 서버가 따로 치우지 않는다. 넣을 때마다 만료된 것을 치워 원문이 메모리에 남지 않게 한다
    sweep(now=moment)
    _pending[run_id] = _Entry(
        parent_id=parent_id, child_id=child_id, context=context, stored_at=moment
    )


def get(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, now: float | None = None
) -> PendingMemoryContext | None:
    """임자·아이·만료를 한 곳에서 본다. 하나라도 안 맞으면 None(이유는 밖에 알리지 않음)."""
    entry = _pending.get(run_id)
    if entry is None:
        return None
    if entry.parent_id != parent_id or entry.child_id != child_id:
        return None
    moment = time.monotonic() if now is None else now
    if moment - entry.stored_at > TTL_SECONDS:
        return None
    return entry.context


def consume(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, now: float | None = None
) -> PendingMemoryContext | None:
    """한 번만 쓴다. 같은 질문에 두 번 답하면 관찰이 두 행이 된다."""
    context = get(run_id=run_id, parent_id=parent_id, child_id=child_id, now=now)
    if context is not None:
        del _pending[run_id]
    return context


def sweep(ttl_seconds: float = TTL_SECONDS, *, now: float | None = None) -> int:
    """만료된 것을 치우고 개수를 돌려준다. 안 치우면 답하지 않은 질문이 쌓인다.

    지울 것을 먼저 모으고 나서 지운다.
    """
    moment = time.monotonic() if now is None else now
    expired = [
        run_id for run_id, entry in _pending.items() if moment - entry.stored_at > ttl_seconds
    ]
    for run_id in expired:
        del _pending[run_id]
    return len(expired)


def clear() -> None:
    """전부 비운다. 테스트가 서로 새지 않게 쓰는 것이고, 서버 코드는 부르지 않는다."""
    _pending.clear()
