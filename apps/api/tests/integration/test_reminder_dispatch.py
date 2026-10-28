"""일정 알림 발송 배치 — FCM 대신 가짜 발송기로 (#594)."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.child.models import Child
from app.domains.identity.models import AuthSession, Parent
from app.domains.notification.models import PushDevice, PushPlatform
from app.domains.notification.repository import Switches, put_switches
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType, Reminder
from app.domains.schedule.repository import claim_due_reminders, create_event, put_reminder
from app.infra.db.session import engine
from app.integrations.fcm import Message, SendResult
from app.rules.reminder import ReminderLead
from app.workers.reminder_dispatch import STALE_AFTER, run_dispatch
from tests.integration.api.conftest import link_child, seed_login

NOW = datetime.now(UTC).replace(microsecond=0)
KST = ZoneInfo("Asia/Seoul")


class FakeFcm:
    def __init__(self, results: dict[str, SendResult] | None = None, on_send=None):
        self.sent: list[tuple[str, Message]] = []
        self._results = results or {}
        self._on_send = on_send

    async def send(self, device_token: str, message: Message) -> SendResult:
        self.sent.append((device_token, message))
        if self._on_send is not None:
            await self._on_send(device_token)
        return self._results.get(device_token, SendResult.SENT)


async def _parent_with_device(session: AsyncSession, token: str) -> tuple[uuid.UUID, uuid.UUID]:
    """(보호자 id, 로그인 id) — 그 로그인에 기기 하나."""
    parent = Parent()
    session.add(parent)
    await session.flush()
    login = await seed_login(session, parent_id=parent.id, token=f"login-{token}")
    session.add(
        PushDevice(
            parent_id=parent.id, session_id=login, device_token=token, platform=PushPlatform.WEB
        )
    )
    await session.flush()
    return parent.id, login


async def _event(session: AsyncSession, child_id: uuid.UUID, starts_at: datetime) -> uuid.UUID:
    event = await create_event(
        session,
        child_id=child_id,
        title="병원",
        event_type=EventType.EPISODIC,
        starts_at=starts_at,
        ends_at=None,
        all_day=False,
        category=EventCategory.HEALTH,
        created_by=EventCreatedBy.CAREGIVER,
    )
    return event.id


async def _remind(session, child_id, event_id, parent_id, lead=ReminderLead.HOURS_1) -> None:
    await put_reminder(
        session, child_id=child_id, event_id=event_id, parent_id=parent_id, lead=lead
    )


async def test_due_reminder_rings_once_for_whoever_set_it(session: AsyncSession):
    """그 알림을 건 보호자의 기기에만, 한 번만 간다. 문구에 일정 제목은 없다."""
    owner, _ = await _parent_with_device(session, "fcm-owner")
    member, _ = await _parent_with_device(session, "fcm-member")
    child_id = await link_child(session, parent_id=owner)
    await link_child(session, parent_id=member, child_id=child_id)
    event_id = await _event(session, child_id, NOW + timedelta(hours=1))
    await _remind(session, child_id, event_id, owner)

    fcm = FakeFcm()
    result = await run_dispatch(session, now=NOW, fcm=fcm)

    assert (result.claimed, result.delivered) == (1, 1)
    [(token, message)] = fcm.sent
    assert token == "fcm-owner"
    assert message.body == "1시간 뒤에 일정이 있어요"
    assert "병원" not in message.title + message.body
    assert message.data == {
        "type": "schedule_reminder",
        "child_id": str(child_id),
        "event_id": str(event_id),
        "date": (NOW + timedelta(hours=1)).astimezone(KST).date().isoformat(),
    }
    assert message.ttl == STALE_AFTER  # 꺼진 기기에 늦게 도착하는 것도 맡는 범위와 같게

    assert (await run_dispatch(session, now=NOW + timedelta(minutes=1), fcm=fcm)).claimed == 0
    assert len(fcm.sent) == 1


async def test_reminders_that_must_not_ring_now(session: AsyncSession):
    """아직 이르거나 · 오래 지났거나 · 스위치를 껐거나 · 로그인이 만료된 기기에는 보내지 않는다."""
    parent, login = await _parent_with_device(session, "fcm-a")
    child_id = await link_child(session, parent_id=parent)
    early = await _event(session, child_id, NOW + timedelta(hours=1, minutes=1))
    stale = await _event(session, child_id, NOW + timedelta(hours=1) - STALE_AFTER)
    await _remind(session, child_id, early, parent)
    await _remind(session, child_id, stale, parent)
    fcm = FakeFcm()

    assert (await run_dispatch(session, now=NOW, fcm=fcm)).claimed == 0

    off, _ = await _parent_with_device(session, "fcm-off")
    await link_child(session, parent_id=off, child_id=child_id)
    await put_switches(
        session, parent_id=off, switches=Switches(schedule_enabled=False, medication_enabled=True)
    )
    due = await _event(session, child_id, NOW + timedelta(hours=1))
    await _remind(session, child_id, due, off)
    await _remind(session, child_id, due, parent)
    await session.execute(
        update(AuthSession).where(AuthSession.id == login).values(refresh_expires_at=NOW)
    )

    result = await run_dispatch(session, now=NOW, fcm=fcm)
    assert (result.claimed, result.no_device) == (2, 2)
    assert fcm.sent == []


async def test_unregistered_device_is_forgotten(session: AsyncSession):
    parent, _ = await _parent_with_device(session, "fcm-gone")
    login = await seed_login(session, parent_id=parent, token="login-second")
    session.add(
        PushDevice(
            parent_id=parent, session_id=login, device_token="fcm-live", platform=PushPlatform.WEB
        )
    )
    child_id = await link_child(session, parent_id=parent)
    event_id = await _event(session, child_id, NOW + timedelta(hours=1))
    await _remind(session, child_id, event_id, parent)

    fcm = FakeFcm({"fcm-gone": SendResult.UNREGISTERED})
    result = await run_dispatch(session, now=NOW, fcm=fcm)

    assert (result.delivered, result.forgotten) == (1, 1)
    tokens = await session.scalars(
        select(PushDevice.device_token)
        .where(PushDevice.parent_id == parent)
        .execution_options(populate_existing=True)
    )
    assert list(tokens) == ["fcm-live"]


async def test_device_logged_out_during_the_run_is_not_sent_to(session: AsyncSession):
    """기기 목록을 읽은 뒤 그 기기가 로그아웃하면 보내지 않는다 — 공용 기기에서 계정을 바꾼 경우다.

    보내기 직전에 기기 · 토큰 · 로그인을 다시 본다. 처음 읽은 목록을 끝까지 쓰면 이미 다른 사람이
    쓰는 기기에 앞 보호자의 알림(아이 · 일정 id)이 간다.
    """
    parent, first_login = await _parent_with_device(session, "fcm-1")
    second_login = await seed_login(session, parent_id=parent, token="login-fcm-2")
    session.add(
        PushDevice(
            parent_id=parent,
            session_id=second_login,
            device_token="fcm-2",
            platform=PushPlatform.WEB,
        )
    )
    child_id = await link_child(session, parent_id=parent)
    event_id = await _event(session, child_id, NOW + timedelta(hours=1))
    await _remind(session, child_id, event_id, parent)

    async def log_out_the_other(token: str) -> None:
        other = second_login if token == "fcm-1" else first_login
        await session.execute(delete(AuthSession).where(AuthSession.id == other))

    fcm = FakeFcm(on_send=log_out_the_other)
    result = await run_dispatch(session, now=NOW, fcm=fcm)

    assert len(fcm.sent) == 1
    assert (result.delivered, result.dropped) == (1, 1)


# 배치가 겹쳐 돌 때. 한 트랜잭션 안의 `session` 으로는 겹칠 수 없어 커밋하는 세션을 따로 연다.


@pytest.fixture
async def committed_due_reminder():
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        parent, _ = await _parent_with_device(setup, "fcm-overlap")
        child_id = await link_child(setup, parent_id=parent)
        event_id = await _event(setup, child_id, NOW + timedelta(hours=1))
        await _remind(setup, child_id, event_id, parent)
        await setup.commit()
    yield factory, parent
    async with factory() as cleanup:  # 일정 · 알림 · 로그인 · 기기는 CASCADE
        await cleanup.execute(delete(Child).where(Child.id == child_id))
        await cleanup.execute(delete(Parent).where(Parent.id == parent))
        await cleanup.commit()


async def test_overlapping_runs_claim_a_reminder_only_once(committed_due_reminder):
    """앞 배치가 맡고 커밋하기 전에 다음 배치가 돌아도, 다음 배치는 기다리지 않고 건너뛴다."""
    factory, parent = committed_due_reminder
    async with factory() as first, factory() as second:
        [claimed] = await claim_due_reminders(first, now=NOW, stale_after=STALE_AFTER)
        assert claimed.parent_id == parent
        assert await claim_due_reminders(second, now=NOW, stale_after=STALE_AFTER) == []
        await first.commit()
    async with factory() as check:
        sent_at = await check.scalar(select(Reminder.sent_at).where(Reminder.parent_id == parent))
    assert sent_at == NOW
