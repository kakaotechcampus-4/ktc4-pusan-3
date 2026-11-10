"""rate_event 읽기 · 쓰기. 세션을 받아 실행만 하고 commit 하지 않는다.

🚨 확인과 기록이 한 트랜잭션이고, 통마다 advisory lock 을 잡는다 — 워커 여럿에 같은 통의 요청이
   동시에 와도 하나씩 세서 한도를 넘지 않는다. 잠금은 트랜잭션이 끝날 때 풀린다. 통을 정렬해 잡아
   서로를 기다리는 일이 없다.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.rate_event.models import RateEvent


async def take(
    session: AsyncSession,
    *,
    limits: Sequence[tuple[str, int]],
    since: datetime | None,
    at: datetime,
) -> bool:
    """통마다 `since` 뒤의 사건이 한도 미만이면 모든 통에 한 건씩 남기고 True. 하나라도 차면
    아무것도 남기지 않고 False. `since` 가 None 이면 그 통의 사건을 전부 센다."""
    for key, _ in sorted(limits):
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))
    for key, limit in limits:
        counted = select(func.count()).select_from(RateEvent).where(RateEvent.key == key)
        if since is not None:
            counted = counted.where(RateEvent.at > since)
        if await session.scalar(counted) >= limit:
            return False
    session.add_all(RateEvent(key=key, at=at) for key, _ in limits)
    await session.flush()
    return True


async def give_back(session: AsyncSession, *, keys: Sequence[str], at: datetime) -> None:
    """`take` 가 그 시각에 남긴 한 건을 통마다 지운다 — 같은 시각의 다른 건까지 지우지 않는다."""
    for key in keys:
        one = (
            select(RateEvent.id)
            .where(RateEvent.key == key, RateEvent.at == at)
            .order_by(RateEvent.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        await session.execute(delete(RateEvent).where(RateEvent.id == one))


async def delete_events_before(session: AsyncSession, *, before: datetime) -> int:
    result = await session.execute(delete(RateEvent).where(RateEvent.at < before))
    return result.rowcount or 0


async def delete_events_with_prefix(session: AsyncSession, *, prefix: str) -> None:
    await session.execute(delete(RateEvent).where(RateEvent.key.startswith(prefix)))
