"""되묻기 맥락 보관 — run_id → PendingMemoryContext

Memory가 저장하지 못한 조각을 임시 보관하고, 후속 입력(reply_to)에서 꺼내 Agent에 전달한다.

프로세스 메모리 기반: 재시작/멀티 워커 간 공유 불가.
추후 DB·Redis 전환 시 put/get/claim/release/drop만 교체.
RunChannel과 분리하며, 원문·질문을 포함하므로 15분만 보관하고 로그는 남기지 않는다.
🚨 넣을 때 지울 때를 예약한다(#561). 꺼내는 쪽이 만료를 걸러도 지우지 않으면 원문은 메모리에 남는다.
🚨 답이 오면 지우지 않고 "사용 중" 으로 둔다(#564). 이어받기 run 이 저장 전에 실패하면 표시만 풀고,
   15분은 처음 넣은 시각 그대로 센다. 지웠다가 다시 넣으면 실패할 때마다 15분이 새로 시작된다.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, replace

from app.agents.entrypoint import PendingMemoryContext

TTL_SECONDS = 900.0


@dataclass(frozen=True)
class _Entry:
    parent_id: uuid.UUID
    child_id: uuid.UUID
    context: PendingMemoryContext
    stored_at: float
    # 답을 받아 이어받기 run 이 쓰는 중이다. 같은 질문에 온 다음 답은 받지 않는다
    in_use: bool = False


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
    _pending[run_id] = _Entry(
        parent_id=parent_id, child_id=child_id, context=context, stored_at=moment
    )
    asyncio.get_running_loop().call_later(TTL_SECONDS, _expire, run_id)


def _expire(run_id: str) -> None:
    """예약한 때가 되면 지운다. 사용 중이어도 지운다 — run 은 맥락을 이미 받아 두었다.
    이미 지워졌으면(저장 뒤 drop · 탈퇴 · 연결 끊기) 할 일이 없다.

    🚨 예약은 조금 일찍 돌 수 있다(이벤트 루프의 시계 해상도). 아직 만료 전이면 남은 시간만큼
       다시 예약한다 — 그냥 넘어가면 그 항목을 지울 예약이 더는 없다.
    """
    entry = _pending.get(run_id)
    if entry is None:
        return
    left = entry.stored_at + TTL_SECONDS - time.monotonic()
    if left > 0:
        asyncio.get_running_loop().call_later(left, _expire, run_id)
        return
    del _pending[run_id]


def get(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, now: float | None = None
) -> PendingMemoryContext | None:
    """임자·아이·만료·사용 중을 한 곳에서 본다. 하나라도 안 맞으면 None(이유는 밖에 알리지 않음).

    사용 중이면 없는 것과 같다 — 답할 수 있는 질문이 아니다.
    """
    entry = _pending.get(run_id)
    if entry is None or entry.in_use:
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


def claim(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, now: float | None = None
) -> PendingMemoryContext | None:
    """답이 왔다. 맥락을 넘기고 "사용 중" 으로 둔다 — 같은 질문에 두 번 답하면 관찰이 두 행이 된다.

    🚨 확인과 표시 사이에 await 가 없다. 같은 reply_to 로 거의 동시에 온 두 답 중 하나만 받는다.
    """
    context = get(run_id=run_id, parent_id=parent_id, child_id=child_id, now=now)
    if context is not None:
        _pending[run_id] = replace(_pending[run_id], in_use=True)
    return context


def release(run_id: str) -> None:
    """표시만 푼다. 시각은 그대로라 15분은 처음 넣은 때부터 센다. 이미 지워졌으면 할 일이 없다."""
    entry = _pending.get(run_id)
    if entry is not None:
        _pending[run_id] = replace(entry, in_use=False)


def drop(run_id: str) -> None:
    """이어받기 run 이 저장까지 갔다. 다시 답할 일이 없으니 지운다."""
    _pending.pop(run_id, None)


def forget_parent(*, parent_id: uuid.UUID, child_id: uuid.UUID | None = None) -> None:
    """그 보호자의 것을 바로 지운다 — 탈퇴 · 연결 끊기 (#561). child_id 를 주면 그 아이 것만."""
    for run_id, entry in list(_pending.items()):
        if entry.parent_id == parent_id and child_id in (None, entry.child_id):
            del _pending[run_id]


def forget_child(child_id: uuid.UUID) -> None:
    """그 아이의 것을 보호자와 상관없이 바로 지운다 — 등록 보호자의 탈퇴로 아이가 지워질 때
    (#561)."""
    for run_id, entry in list(_pending.items()):
        if entry.child_id == child_id:
            del _pending[run_id]


def clear() -> None:
    """전부 비운다. 테스트가 서로 새지 않게 쓰는 것이고, 서버 코드는 부르지 않는다."""
    _pending.clear()
