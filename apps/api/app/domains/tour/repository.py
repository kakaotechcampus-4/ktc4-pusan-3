"""화면 가이드를 본 기록 읽기 · 남기기 · 지우기 (#696)."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import Parent
from app.domains.tour.models import TourSeen


async def list_seen(session: AsyncSession, *, parent_id: uuid.UUID) -> list[str]:
    """본 가이드 키. 처음 본 순서대로."""
    rows = await session.scalars(
        select(TourSeen.tour_key)
        .where(TourSeen.parent_id == parent_id)
        .order_by(TourSeen.seen_at, TourSeen.tour_key)
    )
    return list(rows)


async def mark_seen(session: AsyncSession, *, parent_id: uuid.UUID, tour_key: str) -> bool:
    """본 것으로 남긴다. 이미 있으면 아무것도 바꾸지 않는다. 보호자가 그사이 탈퇴했으면 False.

    🚨 보호자 행을 FOR KEY SHARE 로 잡은 뒤 쓴다. 인증 확인은 잠그지 않아서, 그 뒤에 탈퇴가
       커밋하면 없는 보호자를 가리키는 행을 넣다 FK 오류(500)가 난다
       (`notification.put_switches` 와 같다).
    """
    locked = await session.scalar(
        select(Parent.id).where(Parent.id == parent_id).with_for_update(read=True, key_share=True)
    )
    if locked is None:
        return False
    await session.execute(
        pg_insert(TourSeen)
        .values(parent_id=parent_id, tour_key=tour_key)
        .on_conflict_do_nothing(index_elements=[TourSeen.parent_id, TourSeen.tour_key])
    )
    return True


async def clear_seen(session: AsyncSession, *, parent_id: uuid.UUID) -> None:
    """이 보호자의 기록을 전부 지운다. 없으면 아무것도 하지 않는다."""
    await session.execute(delete(TourSeen).where(TourSeen.parent_id == parent_id))
