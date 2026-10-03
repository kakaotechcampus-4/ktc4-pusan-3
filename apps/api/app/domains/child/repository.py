"""아이와 보호자 연결의 기본 저장·조회 경로."""

import uuid
from datetime import date, datetime

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child, Invite, ParentChild, ParentChildRelation
from app.domains.identity.models import Parent


async def list_children_for_parent(session: AsyncSession, *, parent_id: uuid.UUID) -> list[Child]:
    stmt = (
        select(Child)
        .join(ParentChild, ParentChild.child_id == Child.id)
        .where(ParentChild.parent_id == parent_id, Child.deleted_at.is_(None))
        .order_by(Child.created_at, Child.id)
    )
    return list((await session.scalars(stmt)).all())


async def has_child_link(session: AsyncSession, *, parent_id: uuid.UUID) -> bool:
    """이 보호자에게 parent_child 행이 있는가. 삭제(soft delete)된 아이와의 연결도 센다.

    uq_parent_child_parent_id 와 같은 기준이다 — soft delete 해도 행이 남아 새 연결을 막는다.
    """
    return bool(await session.scalar(select(exists().where(ParentChild.parent_id == parent_id))))


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


async def create_invite(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    created_by: uuid.UUID,
    code_hash: bytes,
    expires_at: datetime,
) -> Invite:
    """초대 코드 1건. 원문 코드는 받지 않는다 — 해시만 저장한다."""
    row = Invite(
        child_id=child_id, created_by=created_by, code_hash=code_hash, expires_at=expires_at
    )
    session.add(row)
    await session.flush()
    return row


async def find_invite_with_child(
    session: AsyncSession, *, code_hash: bytes
) -> tuple[Invite, Child, str | None] | None:
    """코드 해시로 초대 · 그 아이 · 초대한 보호자 별명을 한 번에 찾는다.

    삭제(soft delete)된 아이의 초대는 없는 것으로 본다. 만료 · 사용 여부는 호출하는 쪽이 가른다.
    """
    row = (
        await session.execute(
            select(Invite, Child, Parent.nickname)
            .join(Child, Child.id == Invite.child_id)
            .outerjoin(Parent, Parent.id == Invite.created_by)
            .where(Invite.code_hash == code_hash, Child.deleted_at.is_(None))
        )
    ).first()
    return None if row is None else (row[0], row[1], row[2])


async def consume_invite(
    session: AsyncSession, *, code_hash: bytes, parent_id: uuid.UUID, now: datetime
) -> Invite | None:
    """쓸 수 있는 초대를 한 번만 소비한다. 쓸 수 없으면 None.

    조건부 UPDATE 한 번이라 같은 코드로 동시에 와도 한쪽만 행을 받는다.
    만료 · 사용 여부를 이 시점에 다시 본다 — 확인 화면과 수락 사이에 바뀌었을 수 있다.
    """
    return await session.scalar(
        update(Invite)
        .where(
            Invite.code_hash == code_hash,
            Invite.used_at.is_(None),
            Invite.expires_at > now,
            Invite.child_id.in_(select(Child.id).where(Child.deleted_at.is_(None))),
        )
        .values(used_at=now, used_by=parent_id)
        .returning(Invite)
    )


async def connect_parent(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    relation: ParentChildRelation,
) -> None:
    """보호자를 아이에 member 로 잇는다. 이미 아이가 있으면 uq_parent_child_parent_id 위반."""
    session.add(ParentChild(parent_id=parent_id, child_id=child_id, relation=relation))
    await session.flush()
