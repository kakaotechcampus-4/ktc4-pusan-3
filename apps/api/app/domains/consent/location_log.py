"""위치 이용 확인자료 — 위치정보법 제16조 ② · 위치 약관 제4조 ④ · 제10조 ① (#693)

러너가 좌표를 Agent 에 넘기기 직전에 남기고, 동의를 철회하거나 6개월이 지나면 지운다. 탈퇴는
보호자 행의 CASCADE 가 지운다.

🚨 남기기와 철회는 보호자 행 잠금으로 줄을 선다. 남기기는 FOR KEY SHARE, 철회는 FOR UPDATE 다.
   둘이 겹치면 늦게 온 쪽이 기다린다 — 철회가 먼저면 남기기가 꺼진 동의를 보고 위치를 쓰지 않고,
   남기기가 먼저면 철회의 삭제가 그 기록까지 지운다. 잠금이 없으면 동의를 읽은 뒤 커밋 전에
   철회가 끼어들어, 철회 뒤에도 기록이 6개월 남는다 (#693 리뷰).

모두 flush 까지만 한다. 커밋은 호출자가 한다 — 철회는 동의 행과 한 트랜잭션이어야 하고, 정리
배치는 다른 정리와 한 트랜잭션이다.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent.models import LocationPurpose, LocationUseLog
from app.domains.consent.repository import location_granted
from app.domains.identity.models import Parent

RETENTION_PERIOD = timedelta(days=184)
"""6개월. 가장 긴 6개월(7월 1일 ~ 1월 1일)에 맞춘다 — 짧은 달이 끼어도 6개월 전에 지우지 않는다."""


async def record_location_use_if_granted(
    session: AsyncSession, *, parent_id: uuid.UUID, purpose: LocationPurpose
) -> bool:
    """위치 동의가 지금도 켜져 있으면 쓴 사실을 남기고 True. 아니면 남기지 않고 False.

    False 면 호출자는 좌표를 버린다. 보호자가 그새 탈퇴했으면 행이 없어 False 다.
    🚨 좌표는 받지 않는다 — 인자에 없으니 실수로도 저장되지 않는다.
    KEY SHARE 라 철회 · 탈퇴의 FOR UPDATE 하고만 부딪힌다. 보호자 행의 보통 수정은 막지 않는다.
    """
    held = await session.scalar(
        select(Parent.id).where(Parent.id == parent_id).with_for_update(read=True, key_share=True)
    )
    if held is None or not await location_granted(session, parent_id=parent_id):
        return False
    session.add(LocationUseLog(parent_id=parent_id, purpose=purpose))
    await session.flush()
    return True


async def lock_location_uses(session: AsyncSession, *, parent_id: uuid.UUID) -> None:
    """위치 동의 철회가 철회 행을 넣기 전에 부른다.

    진행 중인 남기기가 커밋될 때까지 기다리고, 철회가 커밋될 때까지 새 남기기를 막는다.
    """
    await session.execute(select(Parent.id).where(Parent.id == parent_id).with_for_update())


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
