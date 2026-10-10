"""아이와 보호자 연결의 기본 저장·조회 경로."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import (
    Child,
    ChildGrowthLog,
    Gender,
    Invite,
    ParentChild,
    ParentChildRelation,
)
from app.domains.identity.models import Parent


async def list_children_for_parent(session: AsyncSession, *, parent_id: uuid.UUID) -> list[Child]:
    stmt = (
        select(Child)
        .join(ParentChild, ParentChild.child_id == Child.id)
        .where(ParentChild.parent_id == parent_id, Child.deleted_at.is_(None))
        .order_by(Child.created_at, Child.id)
    )
    return list((await session.scalars(stmt)).all())


async def list_child_links_for_parent(
    session: AsyncSession, *, parent_id: uuid.UUID
) -> list[tuple[Child, ParentChildRelation]]:
    """보호자에게 연결된 아이와 그 관계 — GET /me 의 재료 (#92). 보관된 아이는 뺀다."""
    stmt = (
        select(Child, ParentChild.relation)
        .join(ParentChild, ParentChild.child_id == Child.id)
        .where(ParentChild.parent_id == parent_id, Child.deleted_at.is_(None))
        .order_by(Child.created_at, Child.id)
    )
    return [(child, relation) for child, relation in (await session.execute(stmt)).all()]


async def find_child_link(
    session: AsyncSession, *, child_id: uuid.UUID, parent_id: uuid.UUID
) -> tuple[Child, ParentChildRelation] | None:
    """보호자에게 연결된 아이 하나와 그 관계 — 프로필(GET /children/{cid})의 재료.

    관계는 보는 보호자 기준이다. 같은 아이라도 엄마와 아빠가 다른 값을 받는다.
    보관된 아이는 없는 것으로 본다 (find_accessible_child 와 같은 기준).
    """
    row = (
        await session.execute(
            select(Child, ParentChild.relation)
            .join(ParentChild, ParentChild.child_id == Child.id)
            .where(
                Child.id == child_id,
                ParentChild.parent_id == parent_id,
                Child.deleted_at.is_(None),
            )
        )
    ).first()
    return None if row is None else (row[0], row[1])


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
    nickname: str | None = None,
    birth_date: date | None = None,
    gender: Gender | None = None,
) -> Child | None:
    """접근 가능한 보호자의 수정. 넘긴 칸만 바꾼다 — None 은 "그대로 둔다" 다.

    별명 · 생일 · 성별은 늘 값이 있어서 비우는 길은 없다. 역할 제한이 추가되면 호출 계층에서
    판단한다. 연결되지 않은 보호자 · 보관된 아이면 None.
    """
    values = {
        name: value
        for name, value in (("nickname", nickname), ("birth_date", birth_date), ("gender", gender))
        if value is not None
    }
    if not values:
        raise ValueError("바꿀 칸이 없다")
    return await session.scalar(
        update(Child)
        .where(
            Child.id == child_id,
            Child.deleted_at.is_(None),
            Child.id.in_(select(ParentChild.child_id).where(ParentChild.parent_id == parent_id)),
        )
        .values(**values)
        .returning(Child)
    )


async def set_relation(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    parent_id: uuid.UUID,
    relation: ParentChildRelation,
) -> bool:
    """이 보호자와 아이 사이의 관계를 바꾼다. 다른 보호자의 관계는 건드리지 않는다.

    연결 행이 없으면 False.
    """
    changed = await session.scalar(
        update(ParentChild)
        .where(ParentChild.child_id == child_id, ParentChild.parent_id == parent_id)
        .values(relation=relation)
        .returning(ParentChild.id)
    )
    return changed is not None


async def create_growth_log(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    created_by: uuid.UUID,
    measured_on: date,
    height_cm: Decimal | None,
    weight_kg: Decimal | None,
) -> ChildGrowthLog:
    """키 · 몸무게를 잰 날 한 줄. 둘 다 None 이면 DB CHECK(child_growth_log_measured)가 막는다."""
    row = ChildGrowthLog(
        child_id=child_id,
        created_by=created_by,
        measured_on=measured_on,
        height_cm=height_cm,
        weight_kg=weight_kg,
    )
    session.add(row)
    await session.flush()
    return row


async def list_growth_logs(session: AsyncSession, *, child_id: uuid.UUID) -> list[ChildGrowthLog]:
    """아이의 키 · 몸무게 기록 — 잰 날 최신순, 같은 날이면 나중에 적은 것 먼저."""
    stmt = (
        select(ChildGrowthLog)
        .where(ChildGrowthLog.child_id == child_id)
        .order_by(ChildGrowthLog.measured_on.desc(), ChildGrowthLog.id.desc())
    )
    return list((await session.scalars(stmt)).all())


async def find_growth_log(
    session: AsyncSession, *, child_id: uuid.UUID, log_id: uuid.UUID
) -> ChildGrowthLog | None:
    """그 아이의 기록 하나. 다른 아이의 기록 id 면 None — 없는 것과 같게 본다."""
    return await session.scalar(
        select(ChildGrowthLog).where(
            ChildGrowthLog.id == log_id, ChildGrowthLog.child_id == child_id
        )
    )


async def delete_growth_log(
    session: AsyncSession, *, child_id: uuid.UUID, log_id: uuid.UUID
) -> bool:
    """그 아이의 기록 하나를 지운다. 없거나 다른 아이의 기록이면 False."""
    deleted = await session.scalar(
        delete(ChildGrowthLog)
        .where(ChildGrowthLog.id == log_id, ChildGrowthLog.child_id == child_id)
        .returning(ChildGrowthLog.id)
    )
    return deleted is not None


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
