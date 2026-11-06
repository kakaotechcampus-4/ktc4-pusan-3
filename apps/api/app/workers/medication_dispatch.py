"""복약 알림 발송 — 일정 알림 배치(`reminder_dispatch`)가 1분마다 함께 부른다 (#749)

한 번 돌 때 하는 일:
  1. 예정 시각이 지난 10분 안인 회차를 맡아 `notice_sent_at` 을 찍고 커밋한다 (`claim_due_doses`).
     미체크 · 코스가 멈추지 않음 · 예정 시각 전에 알림을 켠 구독자가 있는 회차만 맡는다.
  2. 그 구독자의 기기 중 복약 알림을 켰고 로그인이 살아 있는 것에 FCM 으로 보낸다. 보내기 직전에
     예정 시각 전부터의 구독 · 미체크 · 코스(`still_due_for`)와 기기 · 로그인 · 스위치
     (`still_medication_target`)를 다시 본다 — 그사이 알림을 끄거나 · 껐다 다시 켜거나 · 연결을
     끊거나 · 다른 보호자가 체크했을 수 있다.
  3. FCM 이 더는 유효하지 않다고 한 기기를 지운다.

🚨 처리 표시는 회차마다 하나다. 보내기 **전에** 찍어서, 일부 보호자에게 실패하거나 보내다 죽어도
   다시 보내지 않는다 — 두 번 울리느니 한 번 빠진다. 누가 받았는지는 저장하지 않는다.
🚨 문구에 약 이름 · 용량을 싣지 않는다(#577) — 약 이름은 늘 건강 정보다. 일정 알림이 제목을 싣는
   것과 다르게 둔다.
🚨 로그에는 건수만 남긴다. 기기 토큰 · 아이 · 회차 정보를 남기지 않는다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.medication.repository import DueDose, claim_due_doses, still_due_for
from app.domains.notification.repository import (
    forget_devices,
    medication_push_targets,
    still_medication_target,
)
from app.integrations.fcm import FcmClient, Message, SendResult

log = logging.getLogger("app.workers.medication_dispatch")

STALE_AFTER = timedelta(minutes=10)
"""예정 시각이 이보다 오래 지난 회차는 보내지 않는다 — 일정 알림과 같은 값 · 같은 이유다.
푸시의 TTL 도 이 값이다."""

TITLE = "복약 알림"
BODY = "등록한 복약 시각이에요. 복약 기록을 확인해 주세요."
ANDROID_CHANNEL = "medication"
"""앱 셸이 만들 "복약 알림" 채널 id. 앱과 같은 값이어야 한다. 이 채널이 없는 앱(업데이트 전)에서는
FCM 이 앱의 기본 채널로 띄운다."""


def medication_message(dose: DueDose) -> Message:
    """회차 1개의 푸시. 화면은 data 로 그 아이의 그날 복약 화면을 연다."""
    return Message(
        title=TITLE,
        body=BODY,
        data={
            "type": "medication_reminder",
            "child_id": str(dose.child_id),
            "date": dose.dose_date.isoformat(),
        },
        ttl=STALE_AFTER,
        android_channel=ANDROID_CHANNEL,
    )


@dataclass(frozen=True)
class MedicationDispatchResult:
    claimed: int
    """맡아서 `notice_sent_at` 을 찍은 회차."""
    recipients: int
    """맡은 회차의 (회차, 구독자) 쌍."""
    delivered: int
    """기기 한 대 이상에 보낸 (회차, 구독자) 쌍."""
    no_device: int
    """받을 기기가 없었던 쌍 — 복약 알림 스위치를 껐거나, 기기가 없거나, 로그인이 만료됐다."""
    dropped: int
    """보내기 직전에 다시 보니 빠진 기기 — 구독 끄기 · 연결 끊기 · 체크 · 로그아웃 · 스위치 끄기."""
    forgotten: int
    """FCM 이 유효하지 않다고 해서 지운 기기."""
    failed: int
    """보내지 못한 기기 수. 다시 보내지 않는다."""


async def run_medication_dispatch(
    session: AsyncSession, *, now: datetime, fcm: FcmClient
) -> MedicationDispatchResult:
    """한 번 돈다. 현재 시각은 호출자가 넘긴다 — 테스트가 실제 시각에 묶이지 않게."""
    due = await claim_due_doses(session, now=now, stale_after=STALE_AFTER)
    # 🚨 보내기 전에 커밋한다. 보내는 동안 회차를 쥐고 있으면 체크 · 코스 수정이 FCM 응답을 기다린다
    await session.commit()
    if not due:
        return MedicationDispatchResult(0, 0, 0, 0, 0, 0, 0)

    targets = await medication_push_targets(
        session, parent_ids=list({p for d in due for p in d.parent_ids}), now=now
    )
    recipients = delivered = no_device = dropped = failed = 0
    gone: list[tuple] = []  # 같은 보호자가 같은 시각의 회차 여럿을 받으면 한 기기가 여러 번 나온다
    for dose in due:
        message = medication_message(dose)
        for parent_id in dose.parent_ids:
            recipients += 1
            devices = targets.get(parent_id, [])
            if not devices:
                no_device += 1
                continue
            sent_any = False
            for device_id, token in devices:
                if (device_id, token) in gone:  # 앞 회차에서 무효라고 들은 기기 — 같은 시각 회차 둘
                    continue
                if not (
                    await still_due_for(session, dose_id=dose.dose_id, parent_id=parent_id)
                    and await still_medication_target(
                        session,
                        parent_id=parent_id,
                        device_id=device_id,
                        device_token=token,
                        now=now,
                    )
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
    result = MedicationDispatchResult(
        len(due), recipients, delivered, no_device, dropped, len(gone), failed
    )
    log.info(
        "복약 알림 발송: 맡은 회차 %d · 받을 보호자 %d · 보냄 %d · 받을 기기 없음 %d"
        " · 직전에 빠진 기기 %d · 지운 기기 %d · 실패 %d",
        *vars(result).values(),
    )
    return result
