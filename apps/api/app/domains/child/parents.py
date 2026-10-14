"""아이에 연결된 보호자 — 목록 · 연결 끊기 (#336).

🚨 연결 끊기는 DB 가 알아서 지워 주지 않는다. 개인 기록의 CASCADE 는 보호자 행이 지워질 때(탈퇴)만
   동작하고, 연결 끊기는 parent_child 행만 지워 보호자 행이 남는다. 그래서 그 보호자가 이 아이에 쓴
   개인 기록은 여기서 직접 지운다 (노션 삭제 정책 정본 §9 · 재형님 결정 10-06).
   - 지운다: 일기(diary_entry) · 이 아이 일정에 건 개인 알림(reminder)
   - 남긴다: 관찰 · 건강 · 함께 보는 사진 · 고치기 이력과 그 "적은 사람" 칸 — 아이의 공동 기록이다
   대화 기록(#228)은 표가 생기면 unlink_parent 에 지우기를 더한다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import ParentChild, ParentChildRelation
from app.domains.identity.models import Parent
from app.domains.schedule.models import DiaryEntry, Event, Reminder


@dataclass(frozen=True)
class ConnectedParent:
    parent_id: uuid.UUID
    nickname: str | None
    relation: ParentChildRelation
    connected_at: datetime


async def list_parents_with_nickname(
    session: AsyncSession, *, child_id: uuid.UUID
) -> list[ConnectedParent]:
    """연결된 순서대로. 별명은 parent 에 있어 한 번에 JOIN 으로 읽는다."""
    stmt = (
        select(
            ParentChild.parent_id, Parent.nickname, ParentChild.relation, ParentChild.connected_at
        )
        .join(Parent, Parent.id == ParentChild.parent_id)
        .where(ParentChild.child_id == child_id)
        .order_by(ParentChild.connected_at, ParentChild.id)
    )
    return [ConnectedParent(*row) for row in (await session.execute(stmt)).all()]


async def unlink_parent(
    session: AsyncSession, *, child_id: uuid.UUID, parent_id: uuid.UUID
) -> bool:
    """연결과 그 보호자의 이 아이 개인 기록을 지운다. 연결이 없었으면 False.

    호출자의 트랜잭션 안에서 돈다 — 여기서 commit 하지 않는다. 하나라도 실패하면 전부 되돌아간다.
    🚨 등록 보호자인지는 보지 않는다. 막는 것은 호출자(라우터)의 몫이다.
    """
    unlinked = await session.scalar(
        delete(ParentChild)
        .where(ParentChild.child_id == child_id, ParentChild.parent_id == parent_id)
        .returning(ParentChild.id)
    )
    if unlinked is None:
        return False
    await session.execute(
        delete(DiaryEntry).where(
            DiaryEntry.child_id == child_id, DiaryEntry.author_parent_id == parent_id
        )
    )
    # reminder 에는 아이 칸이 없다 — 이 아이의 일정에 걸린 것만 고른다.
    await session.execute(
        delete(Reminder).where(
            Reminder.parent_id == parent_id,
            Reminder.event_id.in_(select(Event.id).where(Event.child_id == child_id)),
        )
    )
    return True
