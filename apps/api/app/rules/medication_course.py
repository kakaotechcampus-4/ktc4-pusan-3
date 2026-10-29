"""복약 코스의 기간 규칙. 표준 라이브러리만 쓴다 — 날짜 계산은 모델에게 묻지 않는다."""

from datetime import date, timedelta

# 코스는 처방 1건에 가깝다. 올리는 것은 쉽고 줄이는 것은 저장된 데이터와 부딪힌다
MAX_SPAN_DAYS = 90


def span_days(starts_on: date, ends_on: date) -> int:
    """기간의 날수. 시작일과 끝일을 둘 다 센다."""
    if ends_on < starts_on:
        raise ValueError("ends_on 은 starts_on 이상이어야 한다")
    return (ends_on - starts_on).days + 1


def dose_dates(starts_on: date, ends_on: date, *, today: date) -> list[date]:
    """회차를 만들 날짜. 지난 날짜는 만들지 않고 오늘은 포함한다 (`today` 는 KST 오늘)."""
    span_days(starts_on, ends_on)  # 뒤바뀐 기간은 여기서 막힌다
    first = max(starts_on, today)
    return [first + timedelta(days=n) for n in range((ends_on - first).days + 1)]
