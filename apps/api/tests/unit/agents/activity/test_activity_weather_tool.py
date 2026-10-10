"""날씨 조회 tool — 네 조회를 병렬로 · 실패한 것만 "모름" · 판정과 등급 라벨만 모델에게.

판정 표(4-2) 자체는 test_activity_weather.py 가 본다. 여기서는 조회를 묶는 쪽을 본다.
- 날씨를 못 읽으면 "맑음"이 아니라 UPSTREAM_ERROR 이고 실내만이다.
- 대기질은 오늘만 본다 — 지금 측정값이라 내일 추천에 쓰지 않는다.
- 해가 졌는지는 오늘만 본다.
"""

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.result import ErrorCode
from app.agents.activity.schemas.common import DayLabel
from app.agents.activity.schemas.outing import LookupWeatherArgs
from app.agents.activity.store.inmemory import InMemoryWeather, in_memory_ports
from app.agents.activity.store.ports import (
    CoarseLocation,
    RawAir,
    RawForecast,
    UpstreamUnavailable,
    WeatherGrid,
)
from app.agents.activity.tools.outing import check_weather, day_of, days_of, lookup_weather
from app.agents.activity.weather import AFTER_SUNSET, AIR_UNCHECKED, WEATHER_UNCHECKED

CHILD = UUID(int=1)
KST = ZoneInfo("Asia/Seoul")
BUSAN = CoarseLocation(lat=35.18, lon=129.08)
MORNING = datetime(2026, 10, 7, 10, tzinfo=KST)  # 수요일
NIGHT = datetime(2026, 10, 7, 20, tzinfo=KST)  # 부산 일몰(18시 전후) 뒤
HEAVY_RAIN = RawForecast(sky="4", pcp="30.0~50.0mm", pop="90")


class RainOn(InMemoryWeather):
    """그날만 강한 비. 주말을 날마다 따로 판정하는지 본다."""

    def __init__(self, rainy: date) -> None:
        super().__init__(uv="4")
        self._rainy = rainy

    async def forecast(self, *, grid: WeatherGrid, day: date) -> RawForecast:
        return HEAVY_RAIN if day == self._rainy else await super().forecast(grid=grid, day=day)


class ForecastDownOn(InMemoryWeather):
    """그날 예보만 실패."""

    def __init__(self, down: date) -> None:
        super().__init__(uv="4")
        self._down = down

    async def forecast(self, *, grid: WeatherGrid, day: date) -> RawForecast:
        if day == self._down:
            raise UpstreamUnavailable("forecast 조회 실패 (테스트 주입)")
        return await super().forecast(grid=grid, day=day)


VERY_BAD_AIR = RawAir(
    pm10="200", pm10_flag=None, pm25="15", pm25_flag=None, o3="0.03", o3_flag=None
)


async def context(weather=None, *, now=MORNING, location=BUSAN) -> ActivityContext:
    ctx = ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=now,
        timezone=KST,
        ports=in_memory_ports(CHILD, date(2024, 1, 1), weather=weather),
        location=location,
    )
    ctx.state.gate = await build_gate(ctx, outdoor_ok=True)
    return ctx


async def lookup(ctx, day=DayLabel.TODAY):
    return await lookup_weather(ctx, LookupWeatherArgs(day=day))


class TestDayOf:
    @pytest.mark.parametrize(
        ("today", "label", "expected"),
        [
            (date(2026, 10, 7), DayLabel.TODAY, date(2026, 10, 7)),
            (date(2026, 10, 7), DayLabel.TOMORROW, date(2026, 10, 8)),
            (date(2026, 10, 7), DayLabel.THIS_WEEKEND, date(2026, 10, 10)),  # 수 → 토
            (date(2026, 10, 10), DayLabel.THIS_WEEKEND, date(2026, 10, 10)),  # 토 → 오늘
            (date(2026, 10, 11), DayLabel.THIS_WEEKEND, date(2026, 10, 11)),  # 일 → 오늘
        ],
    )
    def test_라벨을_날짜로(self, today, label, expected):
        assert day_of(label, today) == expected

    @pytest.mark.parametrize(
        ("today", "expected"),
        [
            (date(2026, 10, 7), (date(2026, 10, 10), date(2026, 10, 11))),  # 수 → 토 · 일
            (date(2026, 10, 10), (date(2026, 10, 10), date(2026, 10, 11))),  # 토 → 토 · 일
            (date(2026, 10, 11), (date(2026, 10, 11),)),  # 일 → 오늘만
        ],
    )
    def test_이번_주말은_토_일_이틀(self, today, expected):
        assert days_of(DayLabel.THIS_WEEKEND, today) == expected

    def test_오늘_내일은_하루(self):
        assert days_of(DayLabel.TOMORROW, date(2026, 10, 7)) == (date(2026, 10, 8),)


class TestLookupWeather:
    async def test_판정과_등급_라벨만_준다(self):
        ctx = await context(InMemoryWeather(uv="4"))
        result = await lookup(ctx)
        assert result.success is True
        assert result.data == {
            "days": [
                {
                    "date": "2026-10-07",
                    "weekday": "수",
                    "checked": True,
                    "outdoor_ok": True,
                    "sky": "맑음",
                    "rain": "비 없음",
                    "pm10": "좋음",
                    "pm25": "좋음",
                    "uv": "보통",
                }
            ]
        }

    async def test_이번_주말은_날마다_따로_판정한다(self):
        """토요일 비가 일요일 야외를 막지 않는다."""
        ctx = await context(RainOn(date(2026, 10, 10)))
        result = await lookup(ctx, DayLabel.THIS_WEEKEND)
        days = result.data["days"]
        assert [(d["weekday"], d["outdoor_ok"]) for d in days] == [("토", False), ("일", True)]
        assert set(ctx.state.weather) == {date(2026, 10, 10), date(2026, 10, 11)}

    async def test_하루만_못_읽으면_그날만_실내로_표시한다(self):
        ctx = await context(ForecastDownOn(date(2026, 10, 10)))
        result = await lookup(ctx, DayLabel.THIS_WEEKEND)
        sat, sun = result.data["days"]
        assert (sat["checked"], sat["outdoor_ok"]) == (False, False)
        assert (sun["checked"], sun["outdoor_ok"]) == (True, True)

    async def test_화면_안내는_모델에게_주지_않고_run_state_에_둔다(self):
        ctx = await context(InMemoryWeather(uv="4", fail=frozenset({"air_quality"})))
        result = await lookup(ctx)
        assert "notices" not in result.data["days"][0]
        assert AIR_UNCHECKED in ctx.state.weather[ctx.today].notices

    @pytest.mark.parametrize("dead", ["forecast", "advisories"])
    async def test_예보나_특보를_못_읽으면_맑음이_아니라_실패(self, dead):
        ctx = await context(InMemoryWeather(uv="4", fail=frozenset({dead})))
        result = await lookup(ctx)
        assert result.error["code"] == ErrorCode.UPSTREAM_ERROR
        assert ctx.state.weather[ctx.today].outdoor_ok is False
        assert WEATHER_UNCHECKED in ctx.state.weather[ctx.today].notices

    async def test_미세먼지만_실패하면_야외는_허용하고_알린다(self):
        ctx = await context(InMemoryWeather(uv="4", fail=frozenset({"air_quality"})))
        (day,) = (await lookup(ctx)).data["days"]
        assert day["outdoor_ok"] is True
        assert "pm10" not in day

    @pytest.mark.parametrize("missing", ["location", "port"])
    async def test_위치나_날씨_포트가_없으면_조회하지_않고_실패(self, missing):
        if missing == "location":
            ctx = await context(InMemoryWeather(uv="4"), location=None)
        else:
            ctx = await context(None)
        result = await lookup(ctx)
        assert result.error["code"] == ErrorCode.UPSTREAM_ERROR


class TestCheckWeather:
    async def test_대기질은_오늘만_본다(self):
        """에어코리아는 지금 측정값만 준다. 오늘 매우나쁨이 내일을 막지 않고 "확인 못 함"이 된다."""
        weather = InMemoryWeather(uv="4", air=VERY_BAD_AIR)
        ctx = await context(weather)
        today = await check_weather(ctx, ctx.today)
        tomorrow = await check_weather(ctx, day_of(DayLabel.TOMORROW, ctx.today))
        assert today.outdoor_ok is False
        assert tomorrow.outdoor_ok is True
        assert AIR_UNCHECKED in tomorrow.notices

    async def test_해가_진_뒤면_오늘은_실내만(self):
        ctx = await context(InMemoryWeather(uv="4"), now=NIGHT)
        brief = await check_weather(ctx, ctx.today)
        assert brief.outdoor_ok is False
        assert AFTER_SUNSET in brief.notices

    async def test_해가_진_뒤라도_내일은_막지_않는다(self):
        ctx = await context(InMemoryWeather(uv="4"), now=NIGHT)
        brief = await check_weather(ctx, day_of(DayLabel.TOMORROW, ctx.today))
        assert brief.outdoor_ok is True

    async def test_자외선만_실패하면_야외는_허용한다(self):
        ctx = await context(InMemoryWeather(uv="4", fail=frozenset({"uv"})))
        brief = await check_weather(ctx, ctx.today)
        assert brief.outdoor_ok is True
