"""일정 알림 — #594.

알림은 보호자 개인 것이다. 일정마다 보호자 한 명에 1개, 다른 보호자의 알림은 보이지 않는다.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import idempotency
from app.domains.child.models import Child
from app.domains.child.parents import unlink_parent
from app.domains.identity.models import Parent
from app.domains.schedule import repository as schedule_repo
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType, Reminder
from app.domains.schedule.repository import create_event, put_reminder
from app.infra.db.session import engine
from app.rules.reminder import ReminderLead
from tests.integration.api.conftest import (
    Bearer,
    backend_pid,
    issue_bearer,
    link_child,
    seed_login,
    wait_for_lock_wait,
)

START = datetime(2099, 3, 10, 2, tzinfo=UTC)
TIMED = {
    "title": "병원",
    "starts_at": "2099-03-10T11:00:00+09:00",
    "ends_at": None,
    "all_day": False,
    "event_type": "episodic",
    "category": "health",
}
DAY = "2099-03-10"


@pytest.fixture
async def member(session: AsyncSession, cid: uuid.UUID) -> Bearer:
    """같은 아이를 함께 보는 두 번째 보호자."""
    member = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=member[1], child_id=cid)
    return member


async def _submit(client: AsyncClient, cid: uuid.UUID, headers: dict, **extra) -> dict:
    idempotency.clear()
    resp = await client.post(
        f"/api/v1/children/{cid}/events",
        json={"event": TIMED, "items": [], **extra},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["event"]


async def _patch(client: AsyncClient, cid: uuid.UUID, eid: str, headers: dict, body: dict):
    idempotency.clear()
    return await client.patch(
        f"/api/v1/children/{cid}/events/{eid}",
        json={"items": [], **body},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )


async def _my_reminder(client: AsyncClient, cid: uuid.UUID, headers: dict) -> dict | None:
    resp = await client.get(f"/api/v1/children/{cid}/calendar/{DAY}", headers=headers)
    [event] = resp.json()["events"]
    return event["reminder"]


async def test_reminder_rings_only_for_whoever_set_it(
    db_client: AsyncClient, bearer: Bearer, member: Bearer, cid: uuid.UUID
):
    """제출한 보호자에게만 걸리고, 다른 보호자는 각자 건다. 서로의 알림은 보이지 않는다."""
    owner_headers, _ = bearer
    member_headers, _ = member
    event = await _submit(db_client, cid, owner_headers, reminder={"lead": "1h"})
    assert event["reminder"] == {"lead": "1h"}
    assert await _my_reminder(db_client, cid, member_headers) is None

    url = f"/api/v1/children/{cid}/events/{event['id']}/reminder"
    resp = await db_client.put(url, json={"lead": "10m"}, headers=member_headers)
    assert resp.json() == {"reminder": {"lead": "10m"}}
    assert await _my_reminder(db_client, cid, owner_headers) == {"lead": "1h"}

    assert (await db_client.delete(url, headers=member_headers)).status_code == 204
    assert await _my_reminder(db_client, cid, member_headers) is None
    assert await _my_reminder(db_client, cid, owner_headers) == {"lead": "1h"}


@pytest.mark.parametrize(
    ("event", "lead"),
    [
        (TIMED, "day_of_9am"),  # 종일 일정의 값
        ({**TIMED, "starts_at": "2026-01-01T11:00:00+09:00"}, "1h"),  # 이미 지난 시각
    ],
)
async def test_unfit_reminder_is_refused_with_the_event(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, event, lead
):
    """알림이 맞지 않으면 일정도 넣지 않는다 — 한 번에 제출한 것이다."""
    headers, _ = bearer
    idempotency.clear()
    resp = await db_client.post(
        f"/api/v1/children/{cid}/events",
        json={"event": event, "items": [], "reminder": {"lead": lead}},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_reminder"
    assert await session.scalar(select(func.count()).select_from(Reminder)) == 0


async def test_editing_the_event_moves_or_drops_reminders(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    member: Bearer,
    cid: uuid.UUID,
):
    owner_headers, _ = bearer
    member_headers, _ = member
    event = await _submit(db_client, cid, owner_headers, reminder={"lead": "1h"})
    eid = event["id"]
    url = f"/api/v1/children/{cid}/events/{eid}/reminder"
    await db_client.put(url, json={"lead": "10m"}, headers=member_headers)
    await session.execute(update(Reminder).values(sent_at=START))

    # 시작을 옮기면 알림은 그대로 따라가고, 새 시각에 다시 울린다. 칸을 빼면 손대지 않는다.
    moved = {**TIMED, "starts_at": "2099-03-10T15:00:00+09:00"}
    resp = await _patch(db_client, cid, eid, owner_headers, {"event": moved})
    assert resp.json()["event"]["reminder"] == {"lead": "1h"}
    rows = (await session.scalars(select(Reminder).execution_options(populate_existing=True))).all()
    assert len(rows) == 2 and all(r.sent_at is None for r in rows)

    # 종일로 바꾸면 두 보호자의 알림이 모두 지워진다. 고친 사람은 같은 요청으로 새로 건다.
    all_day = {**moved, "starts_at": "2099-03-10T00:00:00+09:00", "all_day": True}
    resp = await _patch(
        db_client, cid, eid, owner_headers, {"event": all_day, "reminder": {"lead": "1d_9am"}}
    )
    assert resp.json()["event"]["reminder"] == {"lead": "1d_9am"}
    assert await _my_reminder(db_client, cid, member_headers) is None

    # null 이면 지운다
    resp = await _patch(db_client, cid, eid, owner_headers, {"event": all_day, "reminder": None})
    assert resp.json()["event"]["reminder"] is None


# 다른 요청과 겹칠 때 (#427). 두 요청이 실제로 겹쳐야 해서 한 트랜잭션 안의 `session` 으로는
# 재현할 수 없다. 커밋하는 세션을 따로 열고, 끝나면 만든 보호자 · 아이를 지운다 (나머지는 CASCADE).
# API 를 부를 때는 `db_client` 가 아니라 `client` 다 — 핸들러도 자기 세션으로 커밋해야 겹친다.

OWNER_TOKEN, MEMBER_TOKEN = "test-token-race-owner", "test-token-race-member"


@pytest.fixture
async def committed_event():
    """(세션 공장, 아이 id, 함께 보는 보호자 id, 일정 id) — 실제로 커밋된 값.

    두 보호자는 `OWNER_TOKEN` · `MEMBER_TOKEN` 으로 로그인돼 있다.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        owner, member = Parent(), Parent()
        setup.add_all([owner, member])
        await setup.flush()
        await seed_login(setup, parent_id=owner.id, token=OWNER_TOKEN)
        await seed_login(setup, parent_id=member.id, token=MEMBER_TOKEN)
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
        await wait_for_lock_wait(blocker=await backend_pid(unlinker))  # 저장이 연결 행에서 기다린다
        await unlinker.commit()

    assert await asyncio.wait_for(writing, timeout=5) is None
    async with factory() as check:
        left = await check.scalar(
            select(func.count()).select_from(Reminder).where(Reminder.parent_id == member)
        )
    assert left == 0


async def test_switching_to_all_day_in_flight_refuses_a_timed_reminder(
    client: AsyncClient, committed_event
):
    """종일로 바꾸는 수정이 커밋하기 전에 시간 있는 일정 값으로 알림을 걸면, 바뀐 일정으로 본다."""
    factory, child_id, member, event_id = committed_event

    async with factory() as editor:
        await schedule_repo.update_event(
            editor,
            child_id=child_id,
            event_id=event_id,
            title="병원",
            event_type=EventType.EPISODIC,
            starts_at=START,
            ends_at=None,
            all_day=True,
            category=EventCategory.HEALTH,
        )
        putting = asyncio.create_task(
            client.put(
                f"/api/v1/children/{child_id}/events/{event_id}/reminder",
                json={"lead": "1h"},
                headers={"Authorization": f"Bearer {MEMBER_TOKEN}"},
            )
        )
        await wait_for_lock_wait(blocker=await backend_pid(editor))  # 저장이 일정 행에서 기다린다
        await editor.commit()

    resp = await asyncio.wait_for(putting, timeout=5)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_reminder"
    async with factory() as check:
        left = await check.scalar(
            select(func.count()).select_from(Reminder).where(Reminder.event_id == event_id)
        )
    assert left == 0


async def test_editing_while_being_unlinked_does_not_deadlock(
    client: AsyncClient, committed_event, monkeypatch
):
    """수정이 알림을 고친 뒤 연결 끊기가 들어와도 서로 기다리다 실패하지 않는다.

    끊기는 연결 → 알림 순으로 지운다. 수정이 알림을 먼저 잡고 연결을 나중에 잡으면 교착이 된다.
    """
    factory, child_id, member, event_id = committed_event
    async with factory() as setup:
        await put_reminder(
            setup, child_id=child_id, event_id=event_id, parent_id=member, lead=ReminderLead.HOURS_1
        )
        await setup.commit()

    # 수정이 일정 · 알림을 고친 직후(준비물 교체)에 멈춰 세우고, 그 사이에 끊기를 넣는다
    reached, release = asyncio.Event(), asyncio.Event()
    replace_items = schedule_repo.replace_event_items

    async def paused(*args, **kwargs):
        reached.set()
        await release.wait()
        return await replace_items(*args, **kwargs)

    monkeypatch.setattr(schedule_repo, "replace_event_items", paused)
    idempotency.clear()
    patching = asyncio.create_task(
        client.patch(
            f"/api/v1/children/{child_id}/events/{event_id}",
            json={
                "event": {**TIMED, "starts_at": "2099-03-10T15:00:00+09:00"},
                "items": [],
                "reminder": {"lead": "10m"},
            },
            headers={"Authorization": f"Bearer {MEMBER_TOKEN}", "Idempotency-Key": "race"},
        )
    )
    await asyncio.wait_for(reached.wait(), timeout=5)

    async with factory() as unlinker:
        unlinker_pid = await backend_pid(unlinker)

        async def unlink():
            assert await unlink_parent(unlinker, child_id=child_id, parent_id=member)
            await unlinker.commit()

        unlinking = asyncio.create_task(unlink())
        await wait_for_lock_wait(waiter=unlinker_pid)  # 끊기가 수정이 쥔 행에서 기다린다
        release.set()

        resp = await asyncio.wait_for(patching, timeout=5)
        await asyncio.wait_for(unlinking, timeout=5)
    assert resp.status_code == 200, resp.text
    async with factory() as check:
        left = await check.scalar(
            select(func.count()).select_from(Reminder).where(Reminder.parent_id == member)
        )
    assert left == 0
