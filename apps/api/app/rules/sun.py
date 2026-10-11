"""일출 · 일몰 시각을 계산한다.

위도 · 경도 · 날짜만으로 정해지는 순수 함수다. API 로 붙이지 않는다 — 붙이면 야외 판정이
외부 장애에 물린다. 날씨 조회 실패는 "실내만"으로 안전하게 물러설 수 있지만, 해가 진 시각을
모르면 물러설 방향이 없다 (docs/agents/activity/activity-agent-v1.md D8).

계산식은 미국 해양대기청(NOAA)의 태양 위치 계산식이다. 한국 위도에서 수 분 안쪽으로 맞는다.
표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import math
from datetime import date, time

_KST_OFFSET_HOURS = 9.0
# 대기 굴절과 태양 반지름을 넣은 일출 · 일몰 기준 고도(도)
_SUN_ALTITUDE = 90.833


def _julian_day(day: date) -> float:
    """그날 UTC 0시의 율리우스일."""
    a = (14 - day.month) // 12
    y = day.year + 4800 - a
    m = day.month + 12 * a - 3
    jdn = day.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    return jdn - 0.5


def _minutes_to_time(minutes: float) -> time:
    total = round(minutes) % (24 * 60)
    return time(hour=total // 60, minute=total % 60)


def sun_times(
    day: date, lat: float, lon: float, *, tz_offset_hours: float = _KST_OFFSET_HOURS
) -> tuple[time, time] | None:
    """(일출, 일몰) 현지 시각. 해가 안 지거나 안 뜨는 날(극지방)이면 None.

    예외 메시지에 좌표를 넣지 않는다 — 예외는 로그로 흘러간다.
    """
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise ValueError("위경도가 범위를 벗어났다")

    rad = math.radians
    deg = math.degrees
    # 현지 정오 기준으로 계산한다 — 하루 안에서 적위 변화가 무시할 만하다
    jd = _julian_day(day) + (12.0 - tz_offset_hours) / 24.0
    t = (jd - 2451545.0) / 36525.0

    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (
        math.sin(rad(m)) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(rad(2 * m)) * (0.019993 - 0.000101 * t)
        + math.sin(rad(3 * m)) * 0.000289
    )
    omega = 125.04 - 1934.136 * t
    apparent_long = l0 + c - 0.00569 - 0.00478 * math.sin(rad(omega))
    mean_obliq = (
        23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    )
    obliq = mean_obliq + 0.00256 * math.cos(rad(omega))
    decl = math.asin(math.sin(rad(obliq)) * math.sin(rad(apparent_long)))

    y = math.tan(rad(obliq / 2.0)) ** 2
    eq_time = 4.0 * deg(
        y * math.sin(2 * rad(l0))
        - 2 * e * math.sin(rad(m))
        + 4 * e * y * math.sin(rad(m)) * math.cos(2 * rad(l0))
        - 0.5 * y * y * math.sin(4 * rad(l0))
        - 1.25 * e * e * math.sin(2 * rad(m))
    )

    cos_ha = math.cos(rad(_SUN_ALTITUDE)) / (math.cos(rad(lat)) * math.cos(decl)) - math.tan(
        rad(lat)
    ) * math.tan(decl)
    if not -1.0 <= cos_ha <= 1.0:
        return None
    ha = deg(math.acos(cos_ha))

    noon = 720.0 - 4.0 * lon - eq_time + tz_offset_hours * 60.0
    return _minutes_to_time(noon - 4.0 * ha), _minutes_to_time(noon + 4.0 * ha)
