import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notice.assemble import NewNotice, NewNoticeItem, same_notice
from app.domains.notice.models import Notice, NoticeItem, NoticeRelation

# 사진을 다시 찍어 올리는 일은 받은 지 얼마 안 된 공지에서 일어난다
RECENT_NOTICES = 30


@dataclass(frozen=True)
class SavedNotice:
    notice_id: uuid.UUID
    created: bool  # False 면 같은 공지가 이미 있었다
    item_ids: tuple[uuid.UUID, ...] = ()  # 받은 항목 순서대로의 id. 이미 있던 항목이면 그 id


def _first_same(rows: Sequence, raw_text: str) -> uuid.UUID | None:
    return next((row.id for row in rows if same_notice(row.raw_text, raw_text)), None)


async def _reread_of(session: AsyncSession, child_id: uuid.UUID, notice: NewNotice):
    """같은 공지를 다시 읽은 행. 정정 공지는 비슷해도 새 공지다."""
    if notice.is_correction:
        return None
    rows = await session.execute(
        select(Notice.id, Notice.raw_text)
        .where(Notice.child_id == child_id)
        .order_by(Notice.received_at.desc())
        .limit(RECENT_NOTICES)
    )
    # 긴 글끼리 비교하면 CPU 를 오래 쓴다. run 채널과 같은 프로세스라 SSE 를 막지 않게 넘긴다
    return await asyncio.to_thread(_first_same, rows.all(), notice.raw_text)


def _item_key(handling, title, starts_at, due_at) -> tuple:
    """같은 항목인지 가르는 열쇠. 보호자가 제목을 고쳤으면 다른 항목이다."""
    return (handling, title, starts_at, due_at)


async def _put_items(
    session: AsyncSession, notice_id: uuid.UUID, items: Sequence[NewNoticeItem]
) -> tuple[uuid.UUID, ...]:
    """항목을 넣고 받은 순서대로 id 를 돌려준다. 이미 있는 항목은 넣지 않고 그 id 를 돌려준다.

    같은 공지를 나눠 저장하면(앞에서 일부, 다시 읽어 나머지) 뒤의 항목이 조용히 사라졌다
    (#726 리뷰).
    """
    rows = await session.execute(
        select(
            NoticeItem.id,
            NoticeItem.handling,
            NoticeItem.title,
            NoticeItem.starts_at,
            NoticeItem.due_at,
        ).where(NoticeItem.notice_id == notice_id)
    )
    saved = {_item_key(r.handling, r.title, r.starts_at, r.due_at): r.id for r in rows}
    placed: list[NoticeItem | uuid.UUID] = []
    for item in items:
        if known := saved.get(_item_key(item.handling, item.title, item.starts_at, item.due_at)):
            placed.append(known)
            continue
        placed.append(
            NoticeItem(
                notice_id=notice_id,
                handling=item.handling,
                label=item.label,
                title=item.title,
                target_scope=item.target_scope,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                all_day=item.all_day,
                due_at=item.due_at,
                due_all_day=item.due_all_day,
                details=[detail.model_dump() for detail in item.details],
                supplies=list(item.supplies),
                source_quote=item.source_quote,
            )
        )
    session.add_all(row for row in placed if isinstance(row, NoticeItem))
    await session.flush()
    return tuple(row.id if isinstance(row, NoticeItem) else row for row in placed)


async def save_notice(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    uploader_parent_id: uuid.UUID | None,
    notice: NewNotice,
) -> SavedNotice:
    """원문 1건과 항목 N건을 넣는다. 커밋은 부르는 쪽이 한다.

    공동 양육자가 같은 공지를 또 올려도 한 행이다 — 항목이 두 벌이 되면 할 일이 두 번 뜬다.
    이미 있는 공지면 원문은 그대로 두고, 그 공지에 없던 항목만 더한다.
    """
    notice_id = await _reread_of(session, child_id, notice)
    created = False
    if notice_id is None:
        stmt = (
            insert(Notice)
            .values(
                child_id=child_id,
                uploader_parent_id=uploader_parent_id,
                doc_kind=notice.doc_kind,
                input_format=notice.input_format,
                audience_scope=notice.audience_scope,
                issued_on=notice.issued_on,
                received_at=notice.received_at,
                title=notice.title,
                raw_text=notice.raw_text,
                content_hash=notice.content_hash,
                # 어느 공지를 고치는지는 아직 모른다. 정정이라는 사실만 남긴다
                relation=NoticeRelation.CORRECTION if notice.is_correction else None,
                extractor_version=notice.extractor_version,
            )
            .on_conflict_do_nothing(constraint="uq_notice_child_content_hash")
            .returning(Notice.id)
        )
        notice_id = (await session.execute(stmt)).scalar_one_or_none()
        created = notice_id is not None
        if notice_id is None:
            notice_id = await session.scalar(
                select(Notice.id).where(
                    Notice.child_id == child_id, Notice.content_hash == notice.content_hash
                )
            )
            assert notice_id is not None  # 충돌했으니 있다
    item_ids = await _put_items(session, notice_id, notice.items)
    return SavedNotice(notice_id, created=created, item_ids=item_ids)


async def link_related(
    session: AsyncSession,
    *,
    notice_id: uuid.UUID,
    related_notice_id: uuid.UUID,
    relation: NoticeRelation,
) -> None:
    """정정 · 후속 공지를 앞 공지에 잇는다. 같은 아이의 공지인지는 부르는 쪽이 확인한다."""
    row = await session.get(Notice, notice_id)
    if row is not None:
        row.related_notice_id = related_notice_id
        row.relation = relation
        await session.flush()
