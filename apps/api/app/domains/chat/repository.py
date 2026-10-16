"""대화 기록(`chat_turn`)의 DB 접근 함수 (#228).

쓴 보호자 본인만 보는 개인 기록이다. 모든 함수가 보호자 · 아이 둘 다로 거른다.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.chat.models import ChatTurn
from app.domains.child.models import Child, ParentChild
from app.domains.identity.models import Parent


async def find_turn_date(
    session: AsyncSession, *, parent_id: uuid.UUID, child_id: uuid.UUID, run_id: str
) -> date | None:
    """그 run 의 대화 날짜. 되묻기에 답한 run 이 질문 턴의 날짜를 따를 때 쓴다. 없으면 None."""
    return await session.scalar(
        select(ChatTurn.conversation_date).where(
            ChatTurn.parent_id == parent_id,
            ChatTurn.child_id == child_id,
            ChatTurn.run_id == run_id,
        )
    )


async def add_turn(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    run_id: str,
    conversation_date: date,
    raw_text: str,
    reply_to: str | None,
    replies: list,
    created_at: datetime,
) -> None:
    """대화 한 턴을 쓴다. 보호자가 이 아이와 연결돼 있지 않으면 쓰지 않는다.

    🚨 보호자 → 아이 → 연결 행을 FOR KEY SHARE 로 잡은 뒤 연결을 확인한다 — 일기(`put_diary`,
       #427)와 같은 이유 · 같은 순서다. run 은 접수 뒤 최대 60초를 뒤에서 돌아서, 그 사이에 연결
       끊기(`unlink_parent`)가 연결과 대화를 지우고 커밋하면 여기서 쓴 행이 끊긴 보호자에게 남는다.
       잡아 두면 끊기가 먼저면 연결 행이 없어 쓰지 않고, 여기가 먼저면 끊기가 커밋을 기다렸다가
       이 행까지 지운다. 탈퇴 · 아이 삭제가 먼저면 보호자 · 아이 행이 없어 역시 쓰지 않는다.
    """
    key_share = {"read": True, "key_share": True}
    await session.execute(
        select(Parent.id).where(Parent.id == parent_id).with_for_update(**key_share)
    )
    await session.execute(select(Child.id).where(Child.id == child_id).with_for_update(**key_share))
    linked = await session.scalar(
        select(ParentChild.id)
        .where(ParentChild.child_id == child_id, ParentChild.parent_id == parent_id)
        .with_for_update(**key_share)
    )
    if linked is None:
        return
    session.add(
        ChatTurn(
            parent_id=parent_id,
            child_id=child_id,
            run_id=run_id,
            conversation_date=conversation_date,
            raw_text=raw_text,
            reply_to=reply_to,
            replies=replies,
            created_at=created_at,
        )
    )
    await session.flush()


@dataclass(frozen=True)
class TurnCursor:
    """하루 안에서 이어 받을 자리 — 지난 페이지의 가장 이른 턴. 이보다 이른 턴부터 준다."""

    created_at: datetime
    id: uuid.UUID


async def page_turns(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    day: date,
    cursor: TurnCursor | None,
    limit: int,
) -> tuple[list[ChatTurn], TurnCursor | None]:
    """그날의 턴을 최근 것부터 `limit` 개 끊어 오래된 순으로 준다. 더 이른 턴이 남았으면 커서도.

    대화 화면은 아래(최근)부터 그리고 위로 올리면 이전 것을 붙인다 — 그래서 최근 쪽부터 자르고,
    한 페이지 안은 화면에 그리는 순서(오래된 순)로 준다.
    """
    stmt = select(ChatTurn).where(
        ChatTurn.parent_id == parent_id,
        ChatTurn.child_id == child_id,
        ChatTurn.conversation_date == day,
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(ChatTurn.created_at, ChatTurn.id) < tuple_(cursor.created_at, cursor.id)
        )
    stmt = stmt.order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc()).limit(limit + 1)
    rows = list((await session.scalars(stmt)).all())
    page = rows[:limit]
    more = TurnCursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
    return list(reversed(page)), more


async def count_turns(
    session: AsyncSession, *, parent_id: uuid.UUID, child_id: uuid.UUID, day: date
) -> int:
    """그날 내가 나눈 대화 수. 캘린더 하루 화면의 "이날 나눈 대화 N개" 다."""
    return await session.scalar(
        select(func.count())
        .select_from(ChatTurn)
        .where(
            ChatTurn.parent_id == parent_id,
            ChatTurn.child_id == child_id,
            ChatTurn.conversation_date == day,
        )
    )


async def previous_date(
    session: AsyncSession, *, parent_id: uuid.UUID, child_id: uuid.UUID, before: date
) -> date | None:
    """`before` 보다 앞에서 대화가 있었던 가장 늦은 날. 없으면 None — 대화 없는 날은 건너뛴다."""
    return await session.scalar(
        select(func.max(ChatTurn.conversation_date)).where(
            ChatTurn.parent_id == parent_id,
            ChatTurn.child_id == child_id,
            ChatTurn.conversation_date < before,
        )
    )
