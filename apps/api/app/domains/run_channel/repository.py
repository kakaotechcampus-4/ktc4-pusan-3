"""run_channel · run_event 읽기 · 쓰기. 세션을 받아 실행만 하고 commit 하지 않는다."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.run_channel.models import RunChannelRow, RunEvent


async def open_channel(
    session: AsyncSession, *, run_id: str, parent_id: uuid.UUID, expires_at: datetime
) -> None:
    session.add(RunChannelRow(run_id=run_id, parent_id=parent_id, expires_at=expires_at))
    await session.flush()


async def append_events(
    session: AsyncSession, *, run_id: str, events: Sequence[tuple[int, str, dict]]
) -> None:
    session.add_all(
        RunEvent(run_id=run_id, seq=seq, name=name, payload=payload)
        for seq, name, payload in events
    )
    await session.flush()


async def mark_channel(session: AsyncSession, *, run_id: str, **values) -> None:
    """ended_with · closed · expires_at 중 준 것만 바꾼다."""
    await session.execute(
        update(RunChannelRow).where(RunChannelRow.run_id == run_id).values(**values)
    )


async def get_channel(
    session: AsyncSession, *, run_id: str, now: datetime | None = None
) -> RunChannelRow | None:
    """만료 전 채널. 만료된 것은 없는 것과 같다 — 지우는 것은 hold_purge 몫이다."""
    return await session.scalar(
        select(RunChannelRow).where(
            RunChannelRow.run_id == run_id,
            RunChannelRow.expires_at > (func.now() if now is None else now),
        )
    )


async def events_from(session: AsyncSession, *, run_id: str, seq: int) -> list[RunEvent]:
    rows = await session.scalars(
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.seq >= seq)
        .order_by(RunEvent.seq)
    )
    return list(rows.all())


async def delete_expired_channels(session: AsyncSession, *, now: datetime | None = None) -> int:
    result = await session.execute(
        delete(RunChannelRow).where(
            RunChannelRow.expires_at <= (func.now() if now is None else now)
        )
    )
    return result.rowcount or 0
