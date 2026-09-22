"""아이와 보호자 연결의 기본 저장·조회 경로."""

import uuid
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child, ParentChild, ParentChildRelation


async def list_children_for_parent(session: AsyncSession, *, parent_id: uuid.UUID) -> list[Child]:
    stmt = (
        select(Child)
        .join(ParentChild, ParentChild.child_id == Child.id)
        .where(ParentChild.parent_id == parent_id, Child.deleted_at.is_(None))
        .order_by(Child.created_at, Child.id)
    )
    return list((await session.scalars(stmt)).all())


async def find_accessible_child(
    session: AsyncSession, *, child_id: uuid.UUID, parent_id: uuid.UUID
) -> Child | None:
    return await session.scalar(
        select(Child)
        .join(ParentChild, ParentChild.child_id == Child.id)
        .where(
            Child.id == child_id,
            ParentChild.parent_id == parent_id,
            Child.deleted_at.is_(None),
        )
    )


async def create_child(
    session: AsyncSession,
    *,
    owner_parent_id: uuid.UUID,
    nickname: str,
    birth_date: date,
    relation: ParentChildRelation,
) -> Child:
    row = Child(owner_parent_id=owner_parent_id, nickname=nickname, birth_date=birth_date)
    session.add(row)
    await session.flush()
    session.add(
        ParentChild(
            parent_id=owner_parent_id, child_id=row.id, relation=ParentChildRelation(relation)
        )
    )
    await session.flush()
    return row


async def update_child(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    nickname: str,
    birth_date: date,
) -> Child | None:
    """접근 가능한 보호자의 수정. 역할 제한이 추가되면 호출 계층에서 판단한다."""
    return await session.scalar(
        update(Child)
        .where(
            Child.id == child_id,
            Child.deleted_at.is_(None),
            Child.id.in_(select(ParentChild.child_id).where(ParentChild.parent_id == parent_id)),
        )
        .values(nickname=nickname, birth_date=birth_date)
        .returning(Child)
    )


async def archive_child(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    owner_parent_id: uuid.UUID,
    archived_at: datetime,
) -> Child | None:
    """삭제 대신 보관 처리. owner 외에는 WHERE 절에서 차단한다."""
    return await session.scalar(
        update(Child)
        .where(
            Child.id == child_id,
            Child.owner_parent_id == owner_parent_id,
            Child.deleted_at.is_(None),
        )
        .values(deleted_at=archived_at, deleted_by=owner_parent_id)
        .returning(Child)
    )


async def list_connected_parents(
    session: AsyncSession, *, child_id: uuid.UUID
) -> list[ParentChild]:
    stmt = (
        select(ParentChild)
        .where(ParentChild.child_id == child_id)
        .order_by(ParentChild.connected_at, ParentChild.id)
    )
    return list((await session.scalars(stmt)).all())
