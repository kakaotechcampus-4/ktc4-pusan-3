"""Memory store 계약 시나리오를 DbMemoryStore 로 돌린다 (#257). Postgres 필요.

시나리오는 tests/unit/agents/memory/store_contract.py. 여기서 실패하고 단위 쪽은 통과하면
두 store 가 다르게 동작하는 것이다 — 그 모듈 머리말의 규칙대로 처리한다.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.store import NoticeRow
from app.core.notice_extract import ItemDetail
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
)
from app.domains.memory.store.db_store import DbMemoryStore
from app.domains.notice.assemble import NewNotice, NewNoticeItem, content_hash
from app.domains.notice.models import (
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
    NoticeItem,
    NoticeItemHandling,
    NoticeItemTarget,
)
from app.domains.notice.repository import save_notice
from app.domains.schedule.models import EventCreatedBy, EventItem
from app.domains.schedule.repository import create_event, create_event_item
from tests.unit.agents.memory.store_contract import CASES, KST, NOW, Scenario

_MODELS = {
    "food": ObservationFood,
    "health": ObservationHealth,
    "education": ObservationEducation,
    "activity": ObservationActivity,
    "routine": ObservationRoutine,
}


class DbHarness:
    def __init__(self, session: AsyncSession, *, child_id: UUID, parent_id: UUID) -> None:
        self._session = session
        self.store = DbMemoryStore(session, child_id=child_id)
        self.context = AgentContext(
            child_id=child_id, source_writer=parent_id, now=NOW, timezone=KST, store=self.store
        )

    async def seed_event(self, draft: EventDraft) -> str:
        row = await create_event(
            self._session,
            child_id=self.context.child_id,
            title=draft.title,
            event_type=draft.event_type,
            starts_at=draft.starts_at,
            ends_at=draft.ends_at,
            all_day=draft.all_day,
            category=draft.category,
            created_by=EventCreatedBy.CAREGIVER,
        )
        return str(row.id)

    async def stored(self, domain: str, observation_id: str) -> tuple[str, str]:
        model = _MODELS[domain]
        row = await self._session.scalar(
            select(model)
            .where(model.id == UUID(observation_id))
            .execution_options(populate_existing=True)
        )
        assert row is not None
        return str(row.status), row.raw_text

    async def seed_item(self, event_id: str, item_name: str) -> str:
        row = await create_event_item(
            self._session,
            child_id=self.context.child_id,
            event_id=UUID(event_id),
            item_name=item_name,
        )
        assert row is not None
        return str(row.item_id)

    async def backdate_prepared(self, item_id: str, when: datetime) -> None:
        await self._session.execute(
            update(EventItem).where(EventItem.item_id == UUID(item_id)).values(prepared_at=when)
        )

    async def seed_notice(self, notice: NoticeRow) -> None:
        saved = await save_notice(
            self._session,
            child_id=self.context.child_id,
            uploader_parent_id=self.context.source_writer,
            notice=NewNotice(
                doc_kind=NoticeDocKind(notice.doc_kind),
                input_format=NoticeInputFormat(notice.input_format),
                audience_scope=NoticeAudience(notice.audience_scope),
                issued_on=notice.issued_on,
                received_at=notice.received_at,
                title=notice.title,
                raw_text=notice.raw_text,
                content_hash=content_hash(notice.raw_text),
                is_correction=notice.relation == "correction",
                items=tuple(
                    NewNoticeItem(
                        handling=NoticeItemHandling(i.handling),
                        label=i.label,
                        title=i.title,
                        target_scope=NoticeItemTarget(i.target_scope),
                        starts_at=i.starts_at,
                        ends_at=i.ends_at,
                        all_day=i.all_day,
                        due_at=i.due_at,
                        due_all_day=i.due_all_day,
                        details=tuple(ItemDetail(name=n, value=v) for n, v in i.details),
                        supplies=i.supplies,
                        source_quote=i.source_quote,
                        needs_review=False,
                        review_reason=None,
                    )
                    for i in notice.items
                ),
            ),
        )
        # done_at 은 저장 경로에 없다 — 받은 순서의 id 로 채운다
        for item, item_id in zip(notice.items, saved.item_ids, strict=True):
            if item.done_at is not None:
                await self._session.execute(
                    update(NoticeItem).where(NoticeItem.id == item_id).values(done_at=item.done_at)
                )
        await self._session.flush()
        self._session.expire_all()


@pytest.fixture
async def harness(session: AsyncSession) -> DbHarness:
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="계약", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return DbHarness(session, child_id=child.id, parent_id=owner.id)


@pytest.mark.parametrize(("scenario", "params"), CASES)
async def test_store_계약_DB(
    harness: DbHarness, scenario: Scenario, params: dict[str, Any]
) -> None:
    await scenario(harness, **params)


async def test_다른_아이_공지는_찾지_않는다(session: AsyncSession, harness: DbHarness) -> None:
    other = Child(
        owner_parent_id=harness.context.source_writer,
        nickname="다른아이",
        birth_date=date(2022, 1, 1),
    )
    session.add(other)
    await session.flush()
    raw_text = "다른 아이만 받은 공지입니다."
    await save_notice(
        session,
        child_id=other.id,
        uploader_parent_id=harness.context.source_writer,
        notice=NewNotice(
            doc_kind=NoticeDocKind.NEWSLETTER,
            input_format=NoticeInputFormat.TEXT,
            audience_scope=NoticeAudience.CLASS,
            issued_on=None,
            received_at=NOW,
            title="다른 아이 공지",
            raw_text=raw_text,
            content_hash=content_hash(raw_text),
            is_correction=False,
            items=(),
        ),
    )

    found = await harness.store.query_notices(child_id=harness.context.child_id, limit=5)

    assert found == []
