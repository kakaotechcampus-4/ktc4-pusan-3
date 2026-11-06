"""일정 알림 발송 배치 — 서버 cron 이 1분마다 부른다 (#594). 복약 알림(#749)도 여기서 함께 돈다.

한 번 돌 때 하는 일:
  1. 울릴 시각이 지난 10분 안인 알림을 맡아 `sent_at` 을 찍고 커밋한다 (`claim_due_reminders`).
  2. 그 알림을 건 보호자의 기기 중 일정 알림을 켰고 로그인이 살아 있는 것에 FCM 으로 보낸다.
     기기마다 보내기 직전에 한 번 더 본다 — 그사이 로그아웃한 공용 기기로 가지 않게.
  3. FCM 이 더는 유효하지 않다고 한 기기를 지운다.

🚨 알림은 그 알림을 건 보호자 본인에게만 간다 — 다른 보호자는 각자 건다 (reminder 는 개인 것).
🚨 로그에는 건수만 남긴다. 기기 토큰 · 문구 · 아이 · 일정 정보를 남기지 않는다.
🚨 문구에 일정 제목을 싣는다 — 알림만 보고 무슨 일정인지 알게 (10-29 PM 결정, #605). 푸시는
   Google(FCM)을 거쳐 잠금 화면에 뜨고, 처리방침의 Google 전달 항목에 알림 배달을 적는다(#166).
   복약 알림은 약 이름을 싣지 않는다(#577) — 약 이름은 늘 건강 정보라 일정 제목과 다르게 둔다.

복약 알림은 `medication_dispatch.run_medication_dispatch` 가 같은 실행 안에서 일정 알림 다음에 돈다.
cron 은 이 한 줄 그대로다. 둘은 세션을 따로 쓰고, 한쪽이 실패해도 다른 쪽은 돈다(`_main`).

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
from app.domains.notification.repository import (
    forget_devices,
    schedule_push_targets,
    still_schedule_target,
)
from app.domains.schedule.repository import DueReminder, claim_due_reminders
from app.infra.db.session import async_session_factory, engine
from app.integrations import discord
from app.integrations.fcm import FcmClient, Message, SendResult
from app.rules.reminder import ReminderLead
from app.workers.medication_dispatch import run_medication_dispatch

# __name__ 이 아니다 — `python -m` 으로 돌면 "__main__" 이 되어 app 로거 밖으로 빠진다
# (cleanup.py 와 같다)
log = logging.getLogger("app.workers.reminder_dispatch")

STALE_AFTER = timedelta(minutes=10)
"""울릴 시각이 이보다 오래 지난 알림은 보내지 않는다.

배치가 멈췄다 살아났을 때 밀린 알림이 한꺼번에 울리지 않게 한다. 푸시의 TTL 도 이 값이다 —
기기가 꺼져 있다가 늦게 켜져도 이보다 늦게 도착하지 않는다."""

_KST = ZoneInfo("Asia/Seoul")

TITLE = "일정 알림"
ANDROID_CHANNEL = "schedule"
"""앱 셸이 만들어 둔 "일정 알림" 채널 id. 앱과 같은 값이어야 한다."""
_WHEN: dict[ReminderLead, str] = {
    ReminderLead.AT_START: "지금",
    ReminderLead.MINUTES_5: "5분 뒤",
    ReminderLead.MINUTES_10: "10분 뒤",
    ReminderLead.MINUTES_15: "15분 뒤",
    ReminderLead.MINUTES_30: "30분 뒤",
    ReminderLead.HOURS_1: "1시간 뒤",
    ReminderLead.HOURS_2: "2시간 뒤",
    ReminderLead.DAYS_1: "하루 뒤",
    ReminderLead.DAYS_2: "이틀 뒤",
    ReminderLead.WEEKS_1: "일주일 뒤",
    ReminderLead.DAY_OF_9AM: "오늘",
    ReminderLead.DAYS_1_9AM: "내일",
    ReminderLead.DAYS_2_9AM: "모레",
    ReminderLead.WEEKS_1_9AM: "일주일 뒤",
}
"""본문 앞머리 — "10분 뒤: 소아과 예방접종" 처럼 일정 제목 앞에 붙는다."""


def reminder_message(reminder: DueReminder) -> Message:
    """알림 1건의 푸시. 화면은 data 로 그 아이의 그날 캘린더를 연다."""
    when = _WHEN[reminder.lead]
    # 제목 검사가 서버에 아직 없어 빈 제목 일정이 있다 — "1시간 뒤: " 로 끝나지 않게 시점만 싣는다
    title = reminder.title.strip()
    return Message(
        title=TITLE,
        body=f"{when}: {title}" if title else when,
        data={
            "type": "schedule_reminder",
            "child_id": str(reminder.child_id),
            "event_id": str(reminder.event_id),
            "date": reminder.starts_at.astimezone(_KST).date().isoformat(),
        },
        ttl=STALE_AFTER,
        android_channel=ANDROID_CHANNEL,
    )


@dataclass(frozen=True)
class DispatchResult:
    claimed: int
    """맡아서 `sent_at` 을 찍은 알림."""
    delivered: int
    """기기 한 대 이상에 보낸 알림."""
    no_device: int
    """받을 기기가 없었던 알림 — 스위치를 껐거나, 기기가 없거나, 로그인이 만료됐다."""
    dropped: int
    """보내기 직전에 다시 보니 빠진 기기 — 그사이 로그아웃 · 계정 변경 · 스위치 끄기."""
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
        return DispatchResult(0, 0, 0, 0, 0, 0)

    targets = await schedule_push_targets(
        session, parent_ids=list({r.parent_id for r in due}), now=now
    )
    delivered = no_device = dropped = failed = 0
    gone: list[tuple] = []
    for reminder in due:
        devices = targets.get(reminder.parent_id, [])
        if not devices:
            no_device += 1
            continue
        message = reminder_message(reminder)
        sent_any = False
        for device_id, token in devices:
            if not await still_schedule_target(
                session,
                parent_id=reminder.parent_id,
                device_id=device_id,
                device_token=token,
                now=now,
            ):
                dropped += 1
                continue
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
    result = DispatchResult(len(due), delivered, no_device, dropped, len(gone), failed)
    log.info(
        "일정 알림 발송: 맡음 %d · 보냄 %d · 받을 기기 없음 %d · 직전에 빠진 기기 %d"
        " · 지운 기기 %d · 실패 %d",
        *vars(result).values(),
    )
    return result


_RUNS = (
    ("일정 알림 발송 실패", run_dispatch),
    ("복약 알림 발송 실패", run_medication_dispatch),
)
"""(실패했을 때의 로그 글귀, 발송) — 한 실행에서 차례로 돈다.

🚨 한쪽이 실패해도 다른 쪽은 돈다 — 세션도 따로 연다(실패한 세션은 다시 쓸 수 없다). 실패는
   로그(ERROR → Discord)로 알리고 실행은 1 로 끝난다. 글귀를 통째로 둔 것은 Discord 카드가 `%s` 를
   채우지 않고 글귀(record.msg)만 싣기 때문이다 — 어느 쪽이 실패했는지 카드에서 보이게."""


async def _main() -> None:
    if not settings.FCM_SERVICE_ACCOUNT_FILE:
        # 맡지도 않는다 — 설정을 넣으면 그때부터 지난 10분 안의 알림이 나간다
        log.warning("FCM_SERVICE_ACCOUNT_FILE 이 비어 있어 일정 · 복약 알림을 보내지 않는다")
        return
    fcm = FcmClient.from_file(settings.FCM_SERVICE_ACCOUNT_FILE)
    failed = False
    try:
        for failure, run in _RUNS:
            try:
                async with async_session_factory() as session:
                    await run(session, now=datetime.now(UTC), fcm=fcm)
            except Exception:
                log.exception(failure)
                failed = True
    finally:
        await fcm.aclose()
        await engine.dispose()
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    # main.py 와 같은 순서 — configure_logging() 이 app 로거 처리기를 갈아 끼우므로 알림은 그 뒤
    configure_logging()
    if settings.alert_webhook_url:
        configure_alerts(discord.webhook_sender(settings.alert_webhook_url), env=settings.APP_ENV)
    try:
        asyncio.run(_main())
    except Exception:  # FCM 키 읽기처럼 두 발송 밖에서 난 실패
        log.exception("알림 발송 배치 실패")
        raise SystemExit(1) from None
