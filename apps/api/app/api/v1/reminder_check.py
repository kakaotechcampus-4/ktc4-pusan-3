"""일정 알림을 받기 전에 보는 것 — #594.

일정 제출(`POST`) · 수정(`PATCH`) · 내 알림 두기(`PUT …/reminder`) 세 길이 같은 검사를 거친다.
"""

from datetime import UTC, datetime

from app.api.errors import ApiError
from app.api.v1.schemas.reminders import ReminderOut
from app.domains.schedule.models import Reminder
from app.rules.reminder import ReminderLead, fits, remind_at


def check_reminder(lead: ReminderLead, *, starts_at: datetime, all_day: bool) -> None:
    """일정 종류에 맞는 값인지, 울릴 시각이 아직 오지 않았는지. 아니면 422."""
    if not fits(lead, all_day=all_day):
        raise ApiError(422, "invalid_reminder", "이 일정에는 걸 수 없는 알림이에요")
    if remind_at(lead, starts_at=starts_at, all_day=all_day) <= datetime.now(UTC):
        raise ApiError(422, "invalid_reminder", "알림 시각이 이미 지났어요")


def reminder_out(row: Reminder | None) -> ReminderOut | None:
    return ReminderOut(lead=row.lead) if row is not None else None
