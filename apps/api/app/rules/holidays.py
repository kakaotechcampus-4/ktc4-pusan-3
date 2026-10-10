"""관공서 공휴일 — 어린이집이 쉬는 날을 아는 데 쓴다.

아이 일정에는 어린이집 휴원일이 없다. 공휴일을 모르면 개천절 오전 10시를 "어린이집에 가 있는
시간"으로 읽는다 (docs/agents/activity/activity-agent-v1.md D8).

서비스가 도는 동안 API 를 부르지 않는다. 공휴일은 전년도에 확정되는 정적 데이터라 요청마다 부를
이유가 없고, 부르면 API 가 죽을 때 날짜 계산이 같이 죽는다. 대신 1년에 한 번 특일정보 API 로 받아
`holidays_data.py` 로 굽는다(`scripts/bake_holidays.py`). 날짜를 사람이 옮겨 적지 않는다.

일요일과 겹친 공휴일도 그대로 둔다. 쉬는 날인지는 주말과 함께 호출부가 본다.
표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

from datetime import date

from app.rules.holidays_data import HOLIDAYS

PUBLIC_HOLIDAYS: dict[int, frozenset[date]] = {
    year: frozenset(days) for year, days in HOLIDAYS.items()
}


def holidays_known(year: int) -> bool:
    """그해 공휴일을 받아 뒀는가. False 면 호출부는 주말만 보고 그 사실을 남긴다."""
    return year in PUBLIC_HOLIDAYS


def is_public_holiday(day: date) -> bool:
    """관공서 공휴일인가. 받아 두지 않은 해는 False 다 — `holidays_known` 으로 먼저 가른다."""
    return day in PUBLIC_HOLIDAYS.get(day.year, frozenset())
