"""일정 알림 발송 배치 — 서버 cron 이 1분마다 부른다 (#594)

한 번 돌 때 하는 일:
  1. 울릴 시각이 지난 10분 안인 알림을 맡아 `sent_at` 을 찍고 커밋한다 (`claim_due_reminders`).
  2. 그 알림을 건 보호자의 기기 중 일정 알림을 켰고 로그인이 살아 있는 것에 FCM 으로 보낸다.
  3. FCM 이 더는 유효하지 않다고 한 기기를 지운다.

🚨 알림은 그 알림을 건 보호자 본인에게만 간다 — 다른 보호자는 각자 건다 (reminder 는 개인 것).
🚨 로그에는 건수만 남긴다. 기기 토큰 · 문구 · 아이 · 일정 정보를 남기지 않는다.
🚨 문구에 일정 제목을 넣지 않는다 — 푸시는 Google(FCM)을 거쳐 잠금 화면에 뜬다. 넣을지는 PM 확인
   대기 중이다(#605). 정해지면 `reminder_message` 만 고친다.

  서버 cron 한 줄과 확인 방법은 apps/api/README.md "일정 알림 발송".
  로컬 한 번: cd apps/api && uv run python -m app.workers.reminder_dispatch
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alerts import configure_alerts
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.domains.notification.repository import forget_devices, schedule_push_targets
from app.domains.schedule.repository import DueReminder, claim_due_reminders
from app.infra.db.session import async_session_factory, engine
from app.integrations import discord
from app.integrations.fcm import FcmClient, Message, SendResult
from app.rules.reminder import ReminderLead

# __name__ 이 아니다 — `python -m` 으로 돌면 "__main__" 이 되어 app 로거 밖으로 빠진다
# (cleanup.py 와 같다)
log = logging.getLogger("app.workers.reminder_dispatch")

STALE_AFTER = timedelta(minutes=10)
"""울릴 시각이 이보다 오래 지난 알림은 보내지 않는다.

배치가 멈췄다 살아났을 때 밀린 알림이 한꺼번에 울리지 않게 한다."""

_KST = ZoneInfo("Asia/Seoul")

TITLE = "일정 알림"
_BODY: dict[ReminderLead, str] = {
    ReminderLead.AT_START: "지금 시작하는 일정이 있어요",
    ReminderLead.MINUTES_5: "5분 뒤에 일정이 있어요",
    ReminderLead.MINUTES_10: "10분 뒤에 일정이 있어요",
    ReminderLead.MINUTES_15: "15분 뒤에 일정이 있어요",
    ReminderLead.MINUTES_30: "30분 뒤에 일정이 있어요",
    ReminderLead.HOURS_1: "1시간 뒤에 일정이 있어요",
    ReminderLead.HOURS_2: "2시간 뒤에 일정이 있어요",
    ReminderLead.DAYS_1: "하루 뒤에 일정이 있어요",
    ReminderLead.DAYS_2: "이틀 뒤에 일정이 있어요",
    ReminderLead.WEEKS_1: "일주일 뒤에 일정이 있어요",
    ReminderLead.DAY_OF_9AM: "오늘 일정이 있어요",
    ReminderLead.DAYS_1_9AM: "내일 일정이 있어요",
    ReminderLead.DAYS_2_9AM: "모레 일정이 있어요",
    ReminderLead.WEEKS_1_9AM: "일주일 뒤에 일정이 있어요",
}


def reminder_message(reminder: DueReminder) -> Message:
    """알림 1건의 푸시. 화면은 data 로 그 아이의 그날 캘린더를 연다."""
    return Message(
        title=TITLE,
        body=_BODY[reminder.lead],
        data={
            "type": "schedule_reminder",
            "child_id": str(reminder.child_id),
            "event_id": str(reminder.event_id),
            "date": reminder.starts_at.astimezone(_KST).date().isoformat(),
        },
    )


@dataclass(frozen=True)
class DispatchResult:
    claimed: int
    """맡아서 `sent_at` 을 찍은 알림."""
    delivered: int
    """기기 한 대 이상에 보낸 알림."""
    no_device: int
    """받을 기기가 없었던 알림 — 스위치를 껐거나, 기기가 없거나, 로그인이 만료됐다."""
    forgotten: int
    """FCM 이 유효하지 않다고 해서 지운 기기."""
    failed: int
    """보내지 못한 기기 수. 다시 보내지 않는다."""


async def run_dispatch(session: AsyncSession, *, now: datetime, fcm: FcmClient) -> DispatchResult:
    """한 번 돈다. 현재 시각은 호출자가 넘긴다 — 테스트가 실제 시각에 묶이지 않게."""
    due = await claim_due_reminders(session, now=now, stale_after=STALE_AFTER)
    # 🚨 보내기 전에 커밋한다. 보내는 동안 행을 쥐고 있으면 일정 수정 · 연결 끊기가
    #    FCM 응답을 기다린다
    await session.commit()
    if not due:
        return DispatchResult(0, 0, 0, 0, 0)

    targets = await schedule_push_targets(
        session, parent_ids=list({r.parent_id for r in due}), now=now
    )
    delivered = no_device = failed = 0
    gone: list[tuple] = []
    for reminder in due:
        devices = targets.get(reminder.parent_id, [])
        if not devices:
            no_device += 1
            continue
        message = reminder_message(reminder)
        sent_any = False
        for device_id, token in devices:
            result = await fcm.send(token, message)
            if result is SendResult.SENT:
                sent_any = True
            elif result is SendResult.UNREGISTERED:
                gone.append((device_id, token))
            else:
                failed += 1
        delivered += sent_any
    await forget_devices(session, devices=gone)
    await session.commit()
    result = DispatchResult(len(due), delivered, no_device, len(gone), failed)
    log.info(
        "일정 알림 발송: 맡음 %d · 보냄 %d · 받을 기기 없음 %d · 지운 기기 %d · 실패 %d",
        *vars(result).values(),
    )
    return result


async def _main() -> None:
    if not settings.FCM_SERVICE_ACCOUNT_FILE:
        # 맡지도 않는다 — 설정을 넣으면 그때부터 지난 10분 안의 알림이 나간다
        log.warning("FCM_SERVICE_ACCOUNT_FILE 이 비어 있어 일정 알림을 보내지 않는다")
        return
    fcm = FcmClient.from_file(settings.FCM_SERVICE_ACCOUNT_FILE)
    try:
        async with async_session_factory() as session:
            await run_dispatch(session, now=datetime.now(UTC), fcm=fcm)
    finally:
        await fcm.aclose()
        await engine.dispose()


if __name__ == "__main__":
    # main.py 와 같은 순서 — configure_logging() 이 app 로거 처리기를 갈아 끼우므로 알림은 그 뒤
    configure_logging()
    if settings.alert_webhook_url:
        configure_alerts(discord.webhook_sender(settings.alert_webhook_url), env=settings.APP_ENV)
    try:
        asyncio.run(_main())
    except Exception:
        log.exception("일정 알림 발송 실패")
        raise SystemExit(1) from None
