"""위치 이용 확인자료 — 위치정보법 제16조 ② · 위치 약관 제4조 ④ · 제10조 ① (#693)

좌표를 Agent 에 넘길 때 남기고, 동의를 철회하거나 6개월이 지나면 지운다. 탈퇴는 보호자 행의
CASCADE 가 지운다.

셋 다 flush 만 한다. 커밋은 호출자가 한다 — 철회는 동의 행과 한 트랜잭션이어야 하고, 정리 배치는
다른 정리와 한 트랜잭션이다.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent.models import LocationPurpose, LocationUseLog

RETENTION_PERIOD = timedelta(days=184)
"""6개월. 가장 긴 6개월(7월 1일 ~ 1월 1일)에 맞춘다 — 짧은 달이 끼어도 6개월 전에 지우지 않는다."""


async def record_location_use(
    session: AsyncSession, *, parent_id: uuid.UUID, purpose: LocationPurpose
) -> None:
    """위치를 쓴 사실을 남긴다. 🚨 좌표는 받지 않는다 — 인자에 없으니 실수로도 저장되지 않는다."""
    session.add(LocationUseLog(parent_id=parent_id, purpose=purpose))
    await session.flush()


async def erase_location_logs(session: AsyncSession, *, parent_id: uuid.UUID) -> None:
    """위치 동의를 철회한 보호자의 기록을 지운다 (위치정보법 제24조 ④)."""
    await session.execute(delete(LocationUseLog).where(LocationUseLog.parent_id == parent_id))
    await session.flush()


async def purge_expired_location_logs(session: AsyncSession, *, now: datetime) -> int:
    """보관 기간이 끝난 기록을 지우고 지운 행 수를 돌려준다.

    현재 시각은 호출자가 넘긴다 — purge_expired_retentions 와 같은 이유.
    """
    result = await session.execute(
        delete(LocationUseLog).where(LocationUseLog.used_at <= now - RETENTION_PERIOD)
    )
    await session.flush()
    return result.rowcount
