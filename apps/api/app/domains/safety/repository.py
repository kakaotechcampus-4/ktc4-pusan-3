"""보호자가 확정한 안전정보의 저장 경로. Agent 쓰기 경로에서는 호출하지 않는다."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import Parent
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


async def list_safety_of_kinds(
    session: AsyncSession, *, child_id: uuid.UUID, kinds: tuple[SafetyKind, ...]
) -> list[HealthSafety]:
    """그 아이의 `kinds` 행을 status 와 상관없이 (#405 — Activity 의 안전 포트 계약).

    Activity 는 `allergy` · `environmental` 만 읽고 거르는 것은 호출부가 한다. 순서는
    `list_active_safety` 와 같다.
    """
    stmt = (
        select(HealthSafety)
        .where(HealthSafety.child_id == child_id, HealthSafety.kind.in_(kinds))
        .order_by(HealthSafety.created_at.desc(), HealthSafety.id.desc())
    )
    return list((await session.scalars(stmt)).all())


async def list_active_safety_with_writer(
    session: AsyncSession, *, child_id: uuid.UUID
) -> list[tuple[HealthSafety, str | None]]:
    """list_active_safety 와 같은 행 · 순서에, 적은 보호자의 부르는 이름을 붙인다.

    적은 보호자가 탈퇴했으면(created_by SET NULL) 이름은 None 이다.
    """
    stmt = (
        select(HealthSafety, Parent.nickname)
        .outerjoin(Parent, Parent.id == HealthSafety.created_by)
        .where(HealthSafety.child_id == child_id, HealthSafety.status == SafetyStatus.ACTIVE)
        .order_by(HealthSafety.created_at.desc(), HealthSafety.id.desc())
    )
    return [(row, nickname) for row, nickname in (await session.execute(stmt)).all()]


async def has_active_safety(
    session: AsyncSession, *, child_id: uuid.UUID, kind: SafetyKind, label: str
) -> bool:
    """같은 종류 · 같은 이름의 active 기록이 있는가. 내린(retracted) 기록은 세지 않는다."""
    found = await session.scalar(
        select(HealthSafety.id).where(
            HealthSafety.child_id == child_id,
            HealthSafety.kind == kind,
            HealthSafety.label == label,
            HealthSafety.status == SafetyStatus.ACTIVE,
        )
    )
    return found is not None


async def find_safety(
    session: AsyncSession, *, child_id: uuid.UUID, safety_id: uuid.UUID
) -> HealthSafety | None:
    return await session.scalar(
        select(HealthSafety).where(HealthSafety.child_id == child_id, HealthSafety.id == safety_id)
    )


async def find_active_safety_for_update(
    session: AsyncSession, *, child_id: uuid.UUID, safety_id: uuid.UUID
) -> HealthSafety | None:
    """고치기 전에 그 아이의 active 기록을 잠근다. 내렸거나 다른 아이의 기록이면 None.

    같은 기록을 두 사람이 동시에 고치면 두 번째는 첫 번째가 끝날 때까지 기다렸다가, 이미
    내려간 기록을 보고 None 을 받는다 — 한 기록에서 새 기록이 둘 갈라져 나오지 않는다.
    """
    return await session.scalar(
        select(HealthSafety)
        .where(
            HealthSafety.child_id == child_id,
            HealthSafety.id == safety_id,
            HealthSafety.status == SafetyStatus.ACTIVE,
        )
        .with_for_update()
    )


async def revise_safety(
    session: AsyncSession,
    *,
    current: HealthSafety,
    parent_id: uuid.UUID,
    severity: SafetySeverity | None,
    reactions: list[str],
    notes: str | None,
) -> HealthSafety:
    """보호자의 고치기 — 제자리에서 바꾸지 않고, 지금 기록을 내린 뒤 새 기록을 넣는다.

    이력이 남는다 (#87, 2026-10-13 PM 결정). 종류와 이름은 고칠 수 없어서 같은 아이 · 종류 ·
    이름의 기록을 시간순으로 놓으면 그 항목이 바뀌어 온 길이다. 내린 기록의 updated_by 와 새
    기록의 created_by 가 고친 보호자다. 지금 기록을 먼저 내리므로 active 가 둘이 되지 않는다
    (uq_health_safety_active_item). 호출자가 find_active_safety_for_update 로 잠근 기록을 넘긴다.
    """
    current.status = SafetyStatus.RETRACTED
    current.updated_by = parent_id
    await session.flush()
    return await create_safety(
        session,
        child_id=current.child_id,
        parent_id=parent_id,
        kind=current.kind,
        label=current.label,
        severity=severity,
        reactions=reactions,
        management=current.management,
        notes=notes,
    )


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
