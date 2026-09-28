"""관찰에서 파생된 성향의 API 조회와 보호자용 라벨 수정."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState


async def list_affinities(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    domain: MemoryDomain | None = None,
    state: ProfileState | None = None,
) -> list[ProfileAffinity]:
    stmt = select(ProfileAffinity).where(ProfileAffinity.child_id == child_id)
    if domain is not None:
        stmt = stmt.where(ProfileAffinity.domain == MemoryDomain(domain))
    if state is not None:
        stmt = stmt.where(ProfileAffinity.state == ProfileState(state))
    else:
        stmt = stmt.where(ProfileAffinity.state != ProfileState.ARCHIVED)
    stmt = stmt.order_by(ProfileAffinity.last_observed_on.desc(), ProfileAffinity.id.desc())
    return list((await session.scalars(stmt)).all())


async def find_affinity(
    session: AsyncSession, *, child_id: uuid.UUID, affinity_id: uuid.UUID
) -> ProfileAffinity | None:
    return await session.scalar(
        select(ProfileAffinity).where(
            ProfileAffinity.child_id == child_id, ProfileAffinity.id == affinity_id
        )
    )


async def rename_affinity(
    session: AsyncSession, *, child_id: uuid.UUID, affinity_id: uuid.UUID, merge_key: str
) -> ProfileAffinity | None:
    return await session.scalar(
        update(ProfileAffinity)
        .where(ProfileAffinity.child_id == child_id, ProfileAffinity.id == affinity_id)
        .values(merge_key=merge_key)
        .returning(ProfileAffinity)
    )
