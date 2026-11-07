"""일정 알림 요청 · 응답 — #594.

알림은 보호자 개인 것이다. 일정마다 보호자 한 명에 1개이고,
응답에는 요청한 보호자 본인의 것만 싣는다.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.rules.reminder import ReminderLead

_LEAD_DESCRIPTION = (
    "얼마나 먼저 울릴지. "
    "시간 있는 일정은 at_start · 5m · 10m · 15m · 30m · 1h · 2h · 1d · 2d · 1w, "
    "종일 일정은 day_of_9am · 1d_9am · 2d_9am · 1w_9am (KST 오전 9시). "
    "일정 종류에 맞지 않거나 울릴 시각이 이미 지났으면 422 invalid_reminder"
)


class ReminderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead: ReminderLead = Field(description=_LEAD_DESCRIPTION)


class ReminderOut(BaseModel):
    lead: ReminderLead = Field(description=_LEAD_DESCRIPTION)


class ReminderResponse(BaseModel):
    reminder: ReminderOut
