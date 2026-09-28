"""보호자가 확정한 안전정보의 저장 경로. Agent 쓰기 경로에서는 호출하지 않는다."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.safety.models import HealthSafety, SafetyKind, SafetySeverity, SafetyState


async def list_active_safety(session: AsyncSession, *, child_id: uuid.UUID) -> list[HealthSafety]:
    stmt = (
        select(HealthSafety)
        .where(HealthSafety.child_id == child_id, HealthSafety.state == SafetyState.ACTIVE)
        .order_by(HealthSafety.created_at.desc(), HealthSafety.id.desc())
    )
    return list((await session.scalars(stmt)).all())


async def find_safety(
    session: AsyncSession, *, child_id: uuid.UUID, safety_id: uuid.UUID
) -> HealthSafety | None:
    return await session.scalar(
        select(HealthSafety).where(HealthSafety.child_id == child_id, HealthSafety.id == safety_id)
    )


async def create_safety(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    kind: SafetyKind,
    label: str,
    aliases: list[str] | None = None,
    category: str | None = None,
    severity: SafetySeverity | None = None,
    reactions: list[str] | None = None,
    management: dict | None = None,
    notes: str | None = None,
) -> HealthSafety:
    """보호자 승인·동의·접근권한 검증을 마친 호출자만 사용한다."""
    row = HealthSafety(
        child_id=child_id,
        created_by=parent_id,
        kind=SafetyKind(kind),
        label=label,
        aliases=aliases or [],
        category=category,
        severity=SafetySeverity(severity) if severity is not None else None,
        reactions=reactions or [],
        management=management or {},
        notes=notes,
        state=SafetyState.ACTIVE,
    )
    session.add(row)
    await session.flush()
    return row


async def retract_safety(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    safety_id: uuid.UUID,
    parent_id: uuid.UUID,
) -> HealthSafety | None:
    """DELETE API는 물리 삭제가 아니라 회수다. 이미 회수됐으면 None."""
    stmt = (
        update(HealthSafety)
        .where(
            HealthSafety.child_id == child_id,
            HealthSafety.id == safety_id,
            HealthSafety.state == SafetyState.ACTIVE,
        )
        .values(state=SafetyState.RETRACTED, updated_by=parent_id)
        .returning(HealthSafety)
    )
    return await session.scalar(stmt)
