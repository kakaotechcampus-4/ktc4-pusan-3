"""일정 알림 — #594.

알림은 보호자 개인 것이다. 일정마다 보호자 한 명에 1개, 다른 보호자의 알림은 보이지 않는다.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domains.child.models import Child
from app.domains.child.parents import unlink_parent
from app.domains.identity.models import Parent
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType, Reminder
from app.domains.schedule.repository import create_event, put_reminder
from app.infra.db.session import engine
from app.rules.reminder import ReminderLead
from tests.integration.api.conftest import link_child

START = datetime(2099, 3, 10, 2, tzinfo=UTC)


# 연결 끊기와 겹칠 때 (#427). 두 요청이 실제로 겹쳐야 해서 한 트랜잭션 안의 `session` 으로는
# 재현할 수 없다. 커밋하는 세션을 따로 열고, 끝나면 만든 보호자 · 아이를 지운다 (나머지는 CASCADE).


@pytest.fixture
async def committed_event():
    """(세션 공장, 아이 id, 함께 보는 보호자 id, 일정 id) — 실제로 커밋된 값."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        owner, member = Parent(), Parent()
        setup.add_all([owner, member])
        await setup.flush()
        child_id = await link_child(setup, parent_id=owner.id)
        await link_child(setup, parent_id=member.id, child_id=child_id)
        event = await create_event(
            setup,
            child_id=child_id,
            title="병원",
            event_type=EventType.EPISODIC,
            starts_at=START,
            ends_at=None,
            all_day=False,
            category=EventCategory.HEALTH,
            created_by=EventCreatedBy.CAREGIVER,
        )
        await setup.commit()
    yield factory, child_id, member.id, event.id
    async with factory() as cleanup:
        await cleanup.execute(delete(Child).where(Child.id == child_id))
        await cleanup.execute(delete(Parent).where(Parent.id.in_([owner.id, member.id])))
        await cleanup.commit()


async def test_unlink_in_flight_leaves_no_reminder_of_the_unlinked_parent(committed_event):
    """끊기가 연결과 알림을 지우고 커밋하기 전에 알림 저장이 들어오면, 저장은 None 이다."""
    factory, child_id, member, event_id = committed_event

    async def write_reminder():
        async with factory() as writer:
            row = await put_reminder(
                writer,
                child_id=child_id,
                event_id=event_id,
                parent_id=member,
                lead=ReminderLead.HOURS_1,
            )
            await writer.commit()
            return row

    async with factory() as unlinker:
        assert await unlink_parent(unlinker, child_id=child_id, parent_id=member)
        writing = asyncio.create_task(write_reminder())
        await asyncio.sleep(0.3)  # 저장이 연결 행 잠금에서 기다리게 둔다
        await unlinker.commit()

    assert await asyncio.wait_for(writing, timeout=5) is None
    async with factory() as check:
        left = await check.scalar(
            select(func.count()).select_from(Reminder).where(Reminder.parent_id == member)
        )
    assert left == 0
