"""보호자가 확정한 안전정보의 저장 경로. Agent 쓰기 경로에서는 호출하지 않는다."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.safety.models import (
    ALLERGY_SEVERITIES,
    HealthSafety,
    SafetyKind,
    SafetySeverity,
    SafetyStatus,
)


async def list_active_safety(session: AsyncSession, *, child_id: uuid.UUID) -> list[HealthSafety]:
    stmt = (
        select(HealthSafety)
        .where(HealthSafety.child_id == child_id, HealthSafety.status == SafetyStatus.ACTIVE)
        .order_by(HealthSafety.created_at.desc(), HealthSafety.id.desc())
    )
    return list((await session.scalars(stmt)).all())


async def find_safety(
    session: AsyncSession, *, child_id: uuid.UUID, safety_id: uuid.UUID
) -> HealthSafety | None:
    return await session.scalar(
        select(HealthSafety).where(HealthSafety.child_id == child_id, HealthSafety.id == safety_id)
    )


# TODO(#295): health-safety 라우터를 만들 때 Swagger 로 FE 타입(`HealthSafety`)과 필드를 대조한다.
#   FE 타입은 이 모델에 맞춰 정렬했다 — type(=kind) · 종류별 severity · management 는 text.
async def create_safety(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    kind: SafetyKind,
    label: str,
    severity: SafetySeverity | None = None,
    reactions: list[str] | None = None,
    management: str | None = None,
    notes: str | None = None,
) -> HealthSafety:
    """보호자 승인·동의·접근권한 검증을 마친 호출자만 사용한다.

    severity는 allergy면 class_0~6, 나머지는 mild~anaphylaxis만 받는다. DB CHECK도 같은 것을
    막지만, 여기서 먼저 ValueError로 돌려 호출자가 IntegrityError를 해석하지 않게 한다.
    """
    kind = SafetyKind(kind)
    if severity is not None:
        severity = SafetySeverity(severity)
        if (kind is SafetyKind.ALLERGY) != (severity in ALLERGY_SEVERITIES):
            raise ValueError(f"severity {severity} 는 kind {kind} 에 쓸 수 없다")
    row = HealthSafety(
        child_id=child_id,
        created_by=parent_id,
        kind=kind,
        label=label,
        severity=severity,
        reactions=reactions or [],
        management=management,
        notes=notes,
        status=SafetyStatus.ACTIVE,
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
            HealthSafety.status == SafetyStatus.ACTIVE,
        )
        .values(status=SafetyStatus.RETRACTED, updated_by=parent_id)
        .returning(HealthSafety)
    )
    return await session.scalar(stmt)
