"""Health 병원 찾기(`find_pediatric_places`) — 시간대 분기 · 거리순 다섯 곳 · 실패와 0곳 구분."""

from datetime import date, datetime

import pytest

from app.agents.activity.store.ports import CoarseLocation
from app.agents.health.places import (
    Facility,
    FacilityLookupError,
    FacilityTier,
    PlaceLookup,
    PlaceMode,
    find_pediatric_places,
    place_readout,
    tiers_for,
)
from app.core.weather_raw import WeatherGrid
from app.rules import holidays

HERE = CoarseLocation(lat=35.20, lon=129.08)

# 2026-10-14 수 · 10-17 토 · 10-09 금(한글날)
WED_NOON = datetime(2026, 10, 14, 12, 0)
WED_NIGHT = datetime(2026, 10, 14, 21, 0)
WED_EARLY = datetime(2026, 10, 14, 8, 59)
WED_CLOSING = datetime(2026, 10, 14, 18, 0)
SAT_NOON = datetime(2026, 10, 17, 12, 0)
HANGUL_DAY_NOON = datetime(2026, 10, 9, 12, 0)

P, M, E = FacilityTier.PEDIATRIC_CLINIC, FacilityTier.MOONLIGHT, FacilityTier.EMERGENCY_ROOM


def at(name: str, tier: FacilityTier, dlat: float, **kw) -> Facility:
    """HERE 에서 북쪽으로 dlat 도 떨어진 기관. 0.01도 ≈ 1.1km."""
    return Facility(name=name, tier=tier, lat=HERE.lat + dlat, lon=HERE.lon, **kw)


class FakeSource:
    def __init__(self, rows: list[Facility] | None = None, *, fail: FacilityTier | None = None):
        self.rows = rows or []
        self.fail = fail
        self.calls: list[tuple[WeatherGrid, FacilityTier]] = []

    async def facilities(self, *, grid, tier, now):
        self.calls.append((grid, tier))
        if tier is self.fail:
            raise FacilityLookupError
        return [row for row in self.rows if row.tier is tier]


async def run(source, *, now=WED_NOON, mode=PlaceMode.NORMAL, location=HERE) -> PlaceLookup:
    return await find_pediatric_places(location=location, now=now, mode=mode, source=source)


class TestTiers:
    def test_평일_주간은_소아청소년과(self):
        assert tiers_for(WED_NOON, PlaceMode.NORMAL) == (P,)

    @pytest.mark.parametrize("now", [WED_NIGHT, WED_EARLY, WED_CLOSING, SAT_NOON])
    def test_야간_주말은_달빛_다음_응급실(self, now):
        assert tiers_for(now, PlaceMode.NORMAL) == (M, E)

    def test_공휴일_낮도_야간_휴일로_본다(self):
        # 상수에 한글날이 있어야 이 테스트가 뜻이 있다
        assert holidays.is_public_holiday(date(2026, 10, 9))
        assert tiers_for(HANGUL_DAY_NOON, PlaceMode.NORMAL) == (M, E)

    @pytest.mark.parametrize("now", [WED_NOON, WED_NIGHT])
    def test_응급에서_넘어오면_시간과_상관없이_응급실만(self, now):
        assert tiers_for(now, PlaceMode.EMERGENCY) == (E,)


class TestLookup:
    async def test_가까운_순으로_다섯_곳만(self):
        rows = [at(f"의원{i}", P, 0.01 * i) for i in (6, 2, 4, 1, 3, 5)]

        result = await run(FakeSource(rows))

        assert result.status == "ok"
        assert [p.name for p in result.places] == ["의원1", "의원2", "의원3", "의원4", "의원5"]
        assert [p.distance_m for p in result.places] == sorted(p.distance_m for p in result.places)

    async def test_야간에는_달빛을_먼저_채우고_모자라면_응급실(self):
        rows = [at("응급실가까움", E, 0.001), at("달빛멀리", M, 0.05)]

        result = await run(FakeSource(rows), now=WED_NIGHT)

        # 거리보다 종류 순서가 먼저다 — 소아 경증은 달빛어린이병원이 1순위 (명세 6-4)
        assert [p.name for p in result.places] == ["달빛멀리", "응급실가까움"]

    async def test_달빛으로_다섯_곳이_차면_응급실은_묻지_않는다(self):
        source = FakeSource([at(f"달빛{i}", M, 0.01 * i) for i in range(1, 6)])

        await run(source, now=WED_NIGHT)

        assert [tier for _, tier in source.calls] == [M]

    async def test_문을_닫은_게_확실한_곳은_빼고_모르는_곳은_남긴다(self):
        rows = [
            at("닫음", P, 0.001, open_now=False),
            at("모름", P, 0.002, open_now=None),
            at("열림", P, 0.003, open_now=True),
        ]

        result = await run(FakeSource(rows))

        assert [p.name for p in result.places] == ["모름", "열림"]

    async def test_어댑터에는_좌표가_아니라_격자만_간다(self):
        source = FakeSource([at("의원", P, 0.01)])

        await run(source)

        grid, _ = source.calls[0]
        assert grid == HERE.grid
        assert not hasattr(grid, "lat")

    async def test_다른_종류를_돌려줘도_섞지_않는다(self):
        class Leaky(FakeSource):
            async def facilities(self, *, grid, tier, now):
                return self.rows  # 종류를 거르지 않는 어댑터

        result = await run(Leaky([at("응급실", E, 0.001), at("의원", P, 0.01)]))

        assert [p.name for p in result.places] == ["의원"]


class TestClosedAndFailure:
    async def test_위치가_없으면_묻지_않고_닫는다(self):
        source = FakeSource([at("의원", P, 0.01)])

        result = await run(source, location=None)

        assert result.status == "no_location"
        assert source.calls == []

    async def test_어댑터가_없으면_조회_실패와_같다(self):
        assert (await run(None)).status == "unavailable"

    async def test_조회_실패는_빈_목록이_아니라_unavailable(self):
        """0곳과 실패를 섞으면 "근처에 병원이 없다" 로 읽힌다."""
        result = await run(FakeSource(fail=M), now=WED_NIGHT)

        assert result == PlaceLookup(status="unavailable")

    async def test_찾았는데_0곳이면_empty(self):
        assert (await run(FakeSource([]))).status == "empty"


class TestReadout:
    def test_상태마다_코드_문구(self):
        assert place_readout(PlaceLookup(status="no_location")).body == (
            "위치를 허용하시면 가까운 병원을 찾아드려요."
        )
        assert place_readout(PlaceLookup(status="unavailable")).body == (
            "병원 정보를 불러오지 못했어요. 응급 시 119에 연락하세요."
        )
        assert place_readout(PlaceLookup(status="empty")).body == (
            "근처에서 지금 찾을 수 있는 병원이 없어요. 응급 시 119에 연락하세요."
        )

    async def test_목록은_한_줄에_한_곳_거리와_운영_여부만(self):
        rows = [
            at("가까운의원", P, 0.004, open_now=True, phone="051-000-0000"),
            at("먼의원", P, 0.02),
        ]

        readout = place_readout(await run(FakeSource(rows)))

        assert readout.kind == "place_list"
        assert readout.authored_by == "code"
        assert readout.body.splitlines() == [
            "가까운 소아청소년과 목록이에요. 가기 전에 전화로 진료 중인지 확인해 주세요.",
            "가까운의원 · 소아청소년과 · 400m · 지금 진료 중 · 051-000-0000",
            "먼의원 · 소아청소년과 · 2.2km",
        ]

    @pytest.mark.parametrize("word", ["추천", "좋은", "최고", "평점"])
    def test_평가_표현이_없다(self, word):
        from app.agents.health.places.readouts import READOUTS

        assert all(word not in text.template for text in READOUTS.texts.values())
