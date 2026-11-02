"""일정 알림이 울릴 시각 (#594).

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

알림은 시각이 아니라 "얼마나 먼저" 를 저장한다. 울릴 시각은 보낼 때 이 함수로 일정 시작에서
계산한다 — 일정 시간을 옮기면 알림이 따라가고, 고칠 곳이 일정 한 줄로 끝난다.

목록은 두 벌이다. 시간 있는 일정은 시작에서 거꾸로 센 간격이고, 종일 일정은 시작 시각이
자정이라 "몇 분 전" 이 뜻이 없어서 "며칠 전 오전 9시" 로 센다 (Apple 캘린더와 같은 모양).
일정 종류에 맞지 않는 값은 받지 않는다.
"""

import enum
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

# 종일 일정 알림이 울리는 시각 (KST)
_ALL_DAY_AT = time(9, 0)


class ReminderLead(enum.StrEnum):
    # 시간 있는 일정
    AT_START = "at_start"
    MINUTES_5 = "5m"
    MINUTES_10 = "10m"
    MINUTES_15 = "15m"
    MINUTES_30 = "30m"
    HOURS_1 = "1h"
    HOURS_2 = "2h"
    DAYS_1 = "1d"
    DAYS_2 = "2d"
    WEEKS_1 = "1w"
    # 종일 일정
    DAY_OF_9AM = "day_of_9am"
    DAYS_1_9AM = "1d_9am"
    DAYS_2_9AM = "2d_9am"
    WEEKS_1_9AM = "1w_9am"


_TIMED: dict[ReminderLead, timedelta] = {
    ReminderLead.AT_START: timedelta(0),
    ReminderLead.MINUTES_5: timedelta(minutes=5),
    ReminderLead.MINUTES_10: timedelta(minutes=10),
    ReminderLead.MINUTES_15: timedelta(minutes=15),
    ReminderLead.MINUTES_30: timedelta(minutes=30),
    ReminderLead.HOURS_1: timedelta(hours=1),
    ReminderLead.HOURS_2: timedelta(hours=2),
    ReminderLead.DAYS_1: timedelta(days=1),
    ReminderLead.DAYS_2: timedelta(days=2),
    ReminderLead.WEEKS_1: timedelta(weeks=1),
}

# 며칠 전인가
_ALL_DAY: dict[ReminderLead, int] = {
    ReminderLead.DAY_OF_9AM: 0,
    ReminderLead.DAYS_1_9AM: 1,
    ReminderLead.DAYS_2_9AM: 2,
    ReminderLead.WEEKS_1_9AM: 7,
}

# 일정 종류가 바뀔 때 알림을 지우지 않고 날 수를 지켜 가까운 값으로 옮긴다 (#603 PM 결정).
# 예방접종 · 병원 일정은 알림이 안 오는 쪽이 조금 다른 시각에 오는 것보다 훨씬 나쁘다.
_TO_ALL_DAY: dict[ReminderLead, ReminderLead] = {
    ReminderLead.AT_START: ReminderLead.DAY_OF_9AM,
    ReminderLead.MINUTES_5: ReminderLead.DAY_OF_9AM,
    ReminderLead.MINUTES_10: ReminderLead.DAY_OF_9AM,
    ReminderLead.MINUTES_15: ReminderLead.DAY_OF_9AM,
    ReminderLead.MINUTES_30: ReminderLead.DAY_OF_9AM,
    ReminderLead.HOURS_1: ReminderLead.DAY_OF_9AM,
    ReminderLead.HOURS_2: ReminderLead.DAY_OF_9AM,
    ReminderLead.DAYS_1: ReminderLead.DAYS_1_9AM,
    ReminderLead.DAYS_2: ReminderLead.DAYS_2_9AM,
    ReminderLead.WEEKS_1: ReminderLead.WEEKS_1_9AM,
}
_TO_TIMED: dict[ReminderLead, ReminderLead] = {
    ReminderLead.DAY_OF_9AM: ReminderLead.AT_START,
    ReminderLead.DAYS_1_9AM: ReminderLead.DAYS_1,
    ReminderLead.DAYS_2_9AM: ReminderLead.DAYS_2,
    ReminderLead.WEEKS_1_9AM: ReminderLead.WEEKS_1,
}


def fits(lead: ReminderLead, *, all_day: bool) -> bool:
    """이 알림 값을 그 종류의 일정에 걸 수 있는가."""
    return lead in (_ALL_DAY if all_day else _TIMED)


def for_kind(lead: ReminderLead, *, all_day: bool) -> ReminderLead:
    """그 종류의 일정에 맞는 값. 이미 맞으면 그대로, 아니면 날 수를 지켜 가까운 값으로 옮긴다."""
    if fits(lead, all_day=all_day):
        return lead
    return (_TO_ALL_DAY if all_day else _TO_TIMED)[lead]


def remind_at(lead: ReminderLead, *, starts_at: datetime, all_day: bool) -> datetime:
    """알림이 울릴 시각. 종류가 맞지 않는 값이면 ValueError.

    🚨 종일 일정의 날짜는 KST 로 본다. 종일 일정은 그날 KST 00:00 에 시작하는데, UTC 로 날짜를
       읽으면 전날(15:00 UTC)이 돼서 하루 일찍 울린다.
    """
    if not fits(lead, all_day=all_day):
        kind = "종일" if all_day else "시간 있는"
        msg = f"{lead} 는 {kind} 일정에 걸 수 없어요"
        raise ValueError(msg)
    if not all_day:
        return starts_at - _TIMED[lead]
    day = starts_at.astimezone(_KST).date() - timedelta(days=_ALL_DAY[lead])
    return datetime.combine(day, _ALL_DAY_AT, tzinfo=_KST)


def starts_window(remind_from: datetime, remind_until: datetime) -> tuple[datetime, datetime]:
    """울릴 시각이 [remind_from, remind_until] 안인 알림이 걸려 있을 수 있는 일정 시작의 범위.

    발송이 모든 일정을 훑지 않고 이 범위의 일정만 꺼낸 뒤 `remind_at` 으로 정확히 거른다.
    넉넉하게 잡는다 — 좁히는 용도라 더 꺼내는 것은 괜찮고 빠뜨리는 것은 안 된다.
      - 시작보다 늦게 울리는 값: 종일 당일 오전 9시 — 시작(그날 00:00)의 9시간 뒤. 하루를 둔다.
      - 시작보다 가장 일찍 울리는 값: 1주 전 · 7일 전 오전 9시. 종일 일정의 시작을 그날 끝까지
        보면 7일 15시간이라, 가장 먼 값에 하루를 더 둔다.
    """
    furthest = max(max(_TIMED.values()), timedelta(days=max(_ALL_DAY.values())))
    return remind_from - timedelta(days=1), remind_until + furthest + timedelta(days=1)
