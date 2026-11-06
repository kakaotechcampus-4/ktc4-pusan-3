"""복약 알림 발송 — FCM 대신 가짜 발송기로 (#749).

어떤 회차를 맡는지(창 · 체크 · 멈춤 · 구독 시각)는 `medication/test_medication_notice_claim.py` 가
본다. 여기는 누구의 어느 기기로 무엇이 가는지와, 일정 알림과 한 실행에서 서로 막지 않는지를 본다.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationSource,
    TimingAnchor,
)
from app.domains.medication.repository import (
    NewDose,
    add_course,
    add_subscription,
    claim_due_doses,
    list_doses,
)
from app.domains.notification.repository import Switches, put_switches
from app.infra.db.session import engine
from app.workers import reminder_dispatch
from app.workers.medication_dispatch import STALE_AFTER, run_medication_dispatch
from tests.integration.api.conftest import link_child
from tests.integration.test_reminder_dispatch import FakeFcm, _parent_with_device

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 27, 9, 35, tzinfo=KST).astimezone(UTC)


async def _due_course(session: AsyncSession, child_id: uuid.UUID, *subscribers: uuid.UUID):
    """2분 전에 먹일 회차 하나인 코스. `subscribers` 는 하루 전에 알림을 켰다."""
    at = (NOW - timedelta(minutes=2)).astimezone(KST)
    course = await add_course(
        session,
        child_id=child_id,
        title="아목시실린",
        starts_on=at.date(),
        ends_on=at.date(),
        source=MedicationSource.UTTERANCE,
        created_by=MedicationCreatedBy.CAREGIVER,
        doses=[NewDose(at.date(), TimingAnchor.FIXED, 0, at.time().replace(second=0))],
    )
    for parent_id in subscribers:
        await add_subscription(
            session, schedule_id=course.id, parent_id=parent_id, now=NOW - timedelta(days=1)
        )
    [dose] = await list_doses(session, schedule_ids=[course.id])
    return dose


async def test_구독한_보호자의_기기에만_회차마다_한_번_간다(session: AsyncSession):
    """약 이름은 싣지 않는다. 구독하지 않은 보호자는 같은 아이를 봐도 받지 않는다."""
    owner, _ = await _parent_with_device(session, "fcm-owner")
    helper, _ = await _parent_with_device(session, "fcm-helper")
    bystander, _ = await _parent_with_device(session, "fcm-bystander")
    child_id = await link_child(session, parent_id=owner)
    for parent in (helper, bystander):
        await link_child(session, parent_id=parent, child_id=child_id)
    await _due_course(session, child_id, owner, helper)

    fcm = FakeFcm()
    result = await run_medication_dispatch(session, now=NOW, fcm=fcm)

    assert (result.claimed, result.recipients, result.delivered) == (1, 2, 2)
    assert sorted(token for token, _ in fcm.sent) == ["fcm-helper", "fcm-owner"]
    message = fcm.sent[0][1]
    assert (message.title, message.body) == (
        "복약 알림",
        "등록한 복약 시각이에요. 복약 기록을 확인해 주세요.",
    )
    assert "아목시실린" not in message.body
    assert message.data == {
        "type": "medication_reminder",
        "child_id": str(child_id),
        "date": "2026-10-27",
    }
    assert message.ttl == STALE_AFTER
    assert message.android_channel == "medication"

    again = await run_medication_dispatch(session, now=NOW + timedelta(minutes=1), fcm=fcm)
    assert again.claimed == 0
    assert len(fcm.sent) == 2


async def test_복약_알림_스위치를_끈_보호자는_받지_않는다(session: AsyncSession):
    """일정 알림 스위치와는 따로다 — 일정 알림만 끈 보호자는 받는다."""
    off, _ = await _parent_with_device(session, "fcm-off")
    schedule_off, _ = await _parent_with_device(session, "fcm-schedule-off")
    child_id = await link_child(session, parent_id=off)
    await link_child(session, parent_id=schedule_off, child_id=child_id)
    await put_switches(session, parent_id=off, switches=Switches(True, False))
    await put_switches(session, parent_id=schedule_off, switches=Switches(False, True))
    await _due_course(session, child_id, off, schedule_off)

    fcm = FakeFcm()
    result = await run_medication_dispatch(session, now=NOW, fcm=fcm)

    assert [token for token, _ in fcm.sent] == ["fcm-schedule-off"]
    assert (result.delivered, result.no_device) == (1, 1)


async def test_보내는_사이_체크되면_남은_보호자에게는_보내지_않는다(session: AsyncSession):
    """먼저 받은 보호자가 바로 체크하면 그 뒤 기기는 직전 확인에서 빠진다."""
    first, _ = await _parent_with_device(session, "fcm-1")
    second, _ = await _parent_with_device(session, "fcm-2")
    child_id = await link_child(session, parent_id=first)
    await link_child(session, parent_id=second, child_id=child_id)
    dose = await _due_course(session, child_id, first, second)

    async def check_it(_token: str) -> None:
        await session.execute(
            update(MedicationDose).where(MedicationDose.id == dose.id).values(checked_at=NOW)
        )

    fcm = FakeFcm(on_send=check_it)
    result = await run_medication_dispatch(session, now=NOW, fcm=fcm)

    assert len(fcm.sent) == 1
    assert (result.delivered, result.dropped) == (1, 1)


async def test_일정_알림이_실패해도_복약_알림은_돌고_실행은_실패로_끝난다(monkeypatch):
    ran: list[str] = []

    async def schedule_fails(session, *, now, fcm):
        raise RuntimeError("일정 쪽 실패")

    async def medication(session, *, now, fcm):
        ran.append("복약")

    class Fcm:
        async def aclose(self):
            pass

    class Engine:
        async def dispose(self):
            pass

    @asynccontextmanager
    async def no_session():
        yield None

    monkeypatch.setattr(reminder_dispatch.settings, "FCM_SERVICE_ACCOUNT_FILE", "key.json")
    monkeypatch.setattr(reminder_dispatch.FcmClient, "from_file", lambda _path: Fcm())
    monkeypatch.setattr(reminder_dispatch, "async_session_factory", no_session)
    monkeypatch.setattr(reminder_dispatch, "engine", Engine())
    monkeypatch.setattr(
        reminder_dispatch, "_RUNS", (("일정 알림", schedule_fails), ("복약 알림", medication))
    )

    with pytest.raises(SystemExit) as exited:
        await reminder_dispatch._main()

    assert ran == ["복약"]
    assert exited.value.code == 1


# 배치가 겹쳐 돌 때. 한 트랜잭션 안의 `session` 으로는 겹칠 수 없어 커밋하는 세션을 따로 연다.


@pytest.fixture
async def committed_due_dose():
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        parent, _ = await _parent_with_device(setup, "fcm-overlap")
        child_id = await link_child(setup, parent_id=parent)
        dose = await _due_course(setup, child_id, parent)
        await setup.commit()
    yield factory, dose
    async with factory() as cleanup:  # 코스 · 회차 · 구독 · 로그인 · 기기는 CASCADE
        await cleanup.execute(delete(Child).where(Child.id == child_id))
        await cleanup.execute(delete(Parent).where(Parent.id == parent))
        await cleanup.commit()


async def test_겹쳐_돌아도_한_회차는_한_배치만_맡는다(committed_due_dose):
    """앞 배치가 맡고 커밋하기 전에 다음 배치가 돌아도, 다음 배치는 기다리지 않고 건너뛴다."""
    factory, dose = committed_due_dose
    async with factory() as first, factory() as second:
        [claimed] = await claim_due_doses(first, now=NOW, stale_after=STALE_AFTER)
        assert claimed.dose_id == dose.id
        assert await claim_due_doses(second, now=NOW, stale_after=STALE_AFTER) == []
