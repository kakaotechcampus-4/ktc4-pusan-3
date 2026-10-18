"""진료시간 원문 → 지금 진료 중인가 (`places.hours.open_now`)."""

from datetime import datetime

import pytest

from app.agents.health.places.hours import open_now
from app.core.hospital_raw import FacilityTier, RawFacility

# 2026-10-14 수 · 10-09 금(한글날)
WED = datetime(2026, 10, 14)
HANGUL_DAY = datetime(2026, 10, 9)


def clinic(*hours: tuple[str | None, str | None]) -> RawFacility:
    return RawFacility(name="의원", tier=FacilityTier.PEDIATRIC_CLINIC, lat=0, lon=0, hours=hours)


WEEKDAY_9_18 = clinic(*[("0900", "1800")] * 7, (None, None))


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(8, 59, False), (9, 0, True), (17, 59, True), (18, 0, False)],
)
def test_그날_칸의_시작은_포함하고_끝은_뺀다(hour, minute, expected):
    assert open_now(WEEKDAY_9_18, WED.replace(hour=hour, minute=minute)) is expected


def test_공휴일은_공휴일_칸을_본다():
    holiday_open = clinic(*[(None, None)] * 7, ("0900", "1300"))
    assert open_now(holiday_open, HANGUL_DAY.replace(hour=10)) is True
    assert open_now(holiday_open, WED.replace(hour=10)) is None  # 수요일 칸이 비었다 — 모름


def test_공휴일_칸이_비면_닫힘이_아니라_모름():
    assert open_now(WEEKDAY_9_18, HANGUL_DAY.replace(hour=10)) is None


def test_자정을_넘기는_진료():
    late = clinic(*[("1800", "0200")] * 8)
    assert open_now(late, WED.replace(hour=23)) is True
    assert open_now(late, WED.replace(hour=1)) is True
    assert open_now(late, WED.replace(hour=12)) is False


def test_2400_은_하루_끝까지():
    allday = clinic(*[("0000", "2400")] * 8)
    assert open_now(allday, WED.replace(hour=23, minute=59)) is True


@pytest.mark.parametrize("bad", [("9시", "18시"), ("0900", None), ("2500", "2600")])
def test_읽을_수_없으면_모름(bad):
    assert open_now(clinic(*[bad] * 8), WED.replace(hour=10)) is None


def test_진료시간이_없는_기관은_모름():
    er = RawFacility(name="응급실", tier=FacilityTier.EMERGENCY_ROOM, lat=0, lon=0)
    assert open_now(er, WED.replace(hour=3)) is None
