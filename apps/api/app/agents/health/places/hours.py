"""진료시간 원문 → 지금 진료 중인가. 코드가 정한다 — 모델에게 묻지 않는다 (루트 §3)."""

from datetime import datetime, time

from app.core.hospital_raw import HOLIDAY_SLOT, RawFacility
from app.rules.holidays import is_public_holiday


def open_now(facility: RawFacility, now: datetime) -> bool | None:
    """`now`(KST 벽시계)에 진료 중인가. 그날 칸이 없거나 읽을 수 없으면 None — 모른다.

    - 공휴일은 공휴일 칸(dutyTime8)을 본다. 공휴일 칸이 비었으면 쉬는 날로 보지 않고 모른다로 둔다
      — 원문이 "안 연다" 를 말한 게 아니다.
    - 끝이 시작보다 이르거나 같으면 자정을 넘기는 진료다("1800" ~ "0200").
    - 어제 칸이 자정을 넘겨 오늘 새벽까지 이어지는 경우는 보지 않는다 — 새벽에는 응급실이 먼저다.
    """
    if not facility.hours:
        return None
    slot = HOLIDAY_SLOT if is_public_holiday(now.date()) else now.weekday()
    if slot >= len(facility.hours):
        return None
    start, end = (_hhmm(value) for value in facility.hours[slot])
    if start is None or end is None:
        return None
    at = now.time()
    if start < end:
        return start <= at < end
    return at >= start or at < end  # 자정을 넘긴다


def _hhmm(value: str | None) -> time | None:
    """ "0900" → 09:00. "2400" 은 하루 끝(23:59:59)으로 본다. 모양이 다르면 None."""
    if value is None or len(value) != 4 or not value.isdigit():
        return None
    hours, minutes = int(value[:2]), int(value[2:])
    if hours == 24 and minutes == 0:
        return time(23, 59, 59)
    if hours > 23 or minutes > 59:
        return None
    return time(hours, minutes)
