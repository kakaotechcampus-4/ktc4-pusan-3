"""Health 줄에 찍는 날짜 · 시각 꼴과 회차 예정 시각 — 여러 tool 이 같은 꼴을 쓴다.

날짜는 0 을 채우지 않는다(`10/4`). 복약 현황 `dose.status` · 열 타임라인 · 진료 준비 · 검진 맥락
요약이 같은 꼴이다.
"""

from datetime import date, datetime, tzinfo

from app.agents.health.store.ports import MedicationDose

_RANGE_DASH = "–"  # 9/18–9/20


def md(day: date) -> str:
    return f"{day.month}/{day.day}"


def md_time(moment: datetime) -> str:
    """`10/14 23:00`."""
    return f"{moment.month}/{moment.day} {moment:%H:%M}"


def span(start: date, end: date) -> str:
    """하루면 `9/18`, 여러 날이면 `9/18–9/20`."""
    return md(start) if start == end else f"{md(start)}{_RANGE_DASH}{md(end)}"


def dose_moment(dose: MedicationDose, timezone: tzinfo) -> datetime:
    """회차의 예정 시각 — 날짜 + 시각을 아이 타임존으로."""
    return datetime.combine(dose.dose_date, dose.scheduled_time, tzinfo=timezone)
