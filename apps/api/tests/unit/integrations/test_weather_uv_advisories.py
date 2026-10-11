"""자외선 · 특보 어댑터와 격자 → 시군구 · 특보구역. 기상청을 부르지 않는다.

고정하는 것
- 격자 → 가장 가까운 시군구 · 너무 멀면 없음 · 특보구역 표
- 자외선: 밖으로는 시군구 코드만 · 발표 시각부터 3시간 칸 고르기 · 먼 칸의 빈 문자열은 그대로
- 특보: 위치를 보내지 않고 전국을 받아 이 구역 행만 · NO_DATA 는 그대로 · 전국 목록 공유 캐시
- 실패는 UpstreamUnavailable 이고 메시지 · 로그에 인증키와 주소가 없다
- 네 조회를 묶은 PublicWeather 가 Activity 포트 모양이다
"""

import asyncio
import logging
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.agents.activity.store.ports import WeatherSource
from app.agents.activity.weather import read_advisories
from app.core.config import settings
from app.core.weather_raw import RawAdvisories, UpstreamUnavailable, WeatherGrid
from app.integrations.weather.advisories import KmaAdvisories, parse_rows
from app.integrations.weather.areas import MAX_DISTRICT_CELLS, nearest_district, zones_of
from app.integrations.weather.source import PublicWeather
from app.integrations.weather.uv import KST, KmaUv, slot_key

KEY = "test-key/with+plus"
NOW = datetime(2026, 10, 11, 14, 20, tzinfo=KST)
BUSAN = WeatherGrid(nx=98, ny=76)


@pytest.fixture(autouse=True)
def service_key(monkeypatch):
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", KEY)


# 테스트가 만든 어댑터. 끝나면 닫는다 — 안 닫으면 httpx 클라이언트가 남는다 (#328 리뷰)
_OPENED: list = []


@pytest.fixture(autouse=True)
async def close_sources():
    yield
    while _OPENED:
        await _OPENED.pop().aclose()


def opened(src):
    _OPENED.append(src)
    return src


class Recorder:
    def __init__(self, response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def query(self, index: int = 0) -> dict[str, list[str]]:
        return parse_qs(urlparse(str(self.requests[index].url)).query)


def kma(items, code="00") -> dict:
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": "x"},
            "body": {"items": {"item": items}},
        }
    }


class TestAreas:
    def test_가장_가까운_시군구(self):
        districts = (("2635000000", 99, 76), ("2626000000", 98, 77))
        assert (
            nearest_district(WeatherGrid(99, 77), districts) == "2626000000"
        )  # 같은 거리면 코드 순
        assert nearest_district(WeatherGrid(100, 76), districts) == "2635000000"

    def test_너무_멀면_없다(self):
        districts = (("2635000000", 98, 76),)
        assert nearest_district(WeatherGrid(98 + MAX_DISTRICT_CELLS + 1, 76), districts) is None

    def test_구운_표로_부산은_부산_특보구역을_본다(self):
        district = nearest_district(BUSAN)
        assert district is not None and district.startswith("26")
        assert {"L1082500", "L1082600", "L1082700"} <= zones_of(district)

    def test_표에_없는_시군구는_빈_집합(self):
        assert zones_of("0000000000") == frozenset()


# 10-11 06시 발표. 10-12 12시는 30시간 뒤(h30), 10-13 12시는 54시간 뒤(h54 — 먼 칸이라 빈 문자열)
UV_ROW = {"code": "A07_2", "areaNo": "x", "date": "2026101106", "h0": "1", "h3": "3", "h6": "5",
          "h9": "6", "h12": "2", "h30": "7", "h54": ""}  # fmt: skip


class TestUv:
    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            (datetime(2026, 10, 11, 6, 0, tzinfo=KST), "h0"),
            (datetime(2026, 10, 11, 14, 20, tzinfo=KST), "h6"),  # 8시간 뒤 → 6시 칸
            (datetime(2026, 10, 11, 5, 50, tzinfo=KST), "h0"),  # 발표 직전 몇 분
            (datetime(2026, 10, 14, 9, 0, tzinfo=KST), "h75"),
            (datetime(2026, 10, 14, 12, 0, tzinfo=KST), None),  # 78시간 뒤 — 칸이 없다
        ],
    )
    def test_발표_시각부터_3시간_칸(self, target, expected):
        assert slot_key(datetime(2026, 10, 11, 6, tzinfo=KST), target) == expected

    def source(self, response=None):
        recorder = Recorder(response or httpx.Response(200, json=kma([UV_ROW])))
        return opened(KmaUv(transport=httpx.MockTransport(recorder), now=lambda: NOW)), recorder

    async def test_오늘은_지금_칸_다른_날은_12시_칸(self):
        src, recorder = self.source()
        assert await src.uv(grid=BUSAN, day=date(2026, 10, 11)) == "5"
        assert await src.uv(grid=BUSAN, day=date(2026, 10, 12)) == "7"
        assert len(recorder.requests) == 1  # 같은 시간에는 한 번

    async def test_먼_칸의_빈_문자열은_그대로_없는_칸은_None(self):
        src, _ = self.source()
        assert await src.uv(grid=BUSAN, day=date(2026, 10, 13)) == ""
        assert await src.uv(grid=BUSAN, day=date(2026, 10, 20)) is None

    async def test_밖으로는_시군구_코드만_나간다(self):
        src, recorder = self.source()
        await src.uv(grid=BUSAN, day=date(2026, 10, 11))
        area = recorder.query()["areaNo"][0]
        assert area == nearest_district(BUSAN) and area.endswith("00000")
        assert not {"nx", "ny"} & recorder.query().keys()

    async def test_시군구가_없으면_부르지_않고_실패(self):
        src, recorder = self.source()
        with pytest.raises(UpstreamUnavailable):
            await src.uv(grid=WeatherGrid(1, 1), day=date(2026, 10, 11))
        assert recorder.requests == []


def advisory(area, *, var=2, stress=0, command="1", tm_fc=202610111000, seq=1) -> dict:
    return {
        "areaCode": area,
        "areaName": "이름은 쓰지 않는다",
        "warnVar": var,
        "warnStress": stress,
        "command": command,
        "tmFc": tm_fc,
        "tmSeq": seq,
        "cancel": "0",
    }


class TestAdvisories:
    def source(self, response, clock=None):
        recorder = Recorder(response)
        now = clock or (lambda: NOW)
        return opened(KmaAdvisories(transport=httpx.MockTransport(recorder), now=now)), recorder

    async def test_이_구역_행만_남기고_원문은_문자열로(self):
        body = kma([advisory("L1082500"), advisory("L1100100", var=12)])
        src, _ = self.source(httpx.Response(200, json=body))
        result = await src.advisories(grid=BUSAN)
        assert [row.area_code for row in result.rows] == ["L1082500"]
        row = result.rows[0]
        assert (row.warn_var, row.warn_stress, row.tm_fc, row.tm_seq) == (
            "2",
            "0",
            "202610111000",
            "1",
        )
        assert read_advisories(result).severe is True  # 호우주의보 발효 → Activity 가 실내만

    async def test_위치를_보내지_않고_30일치를_묻는다(self):
        src, recorder = self.source(httpx.Response(200, json=kma([])))
        await src.advisories(grid=BUSAN)
        query = recorder.query()
        assert not {"areaCode", "nx", "ny", "areaNo"} & query.keys()
        assert (query["fromTmFc"], query["toTmFc"]) == (["20260911"], ["20261011"])

    async def test_NO_DATA_는_그대로_넘긴다(self):
        src, _ = self.source(httpx.Response(200, json=kma([], code="03")))
        result = await src.advisories(grid=BUSAN)
        assert result == RawAdvisories(result_code="03")
        assert read_advisories(result) is not None  # 특보 없음 — 실패가 아니다

    async def test_다른_결과_코드도_그대로_넘기고_캐시하지_않는다(self):
        src, recorder = self.source(httpx.Response(200, json=kma([], code="02")))
        assert (await src.advisories(grid=BUSAN)).result_code == "02"
        await src.advisories(grid=BUSAN)
        assert len(recorder.requests) == 2

    async def test_전국_목록은_10분_동안_같이_쓴다(self):
        clock = [NOW]
        src, recorder = self.source(httpx.Response(200, json=kma([])), clock=lambda: clock[0])
        await asyncio.gather(src.advisories(grid=BUSAN), src.advisories(grid=WeatherGrid(60, 127)))
        clock[0] = datetime(2026, 10, 11, 14, 29, tzinfo=KST)
        await src.advisories(grid=BUSAN)
        assert len(recorder.requests) == 1
        clock[0] = datetime(2026, 10, 11, 14, 31, tzinfo=KST)
        await src.advisories(grid=BUSAN)
        assert len(recorder.requests) == 2

    def test_행_모양이_다르면_실패(self):
        with pytest.raises(UpstreamUnavailable):
            parse_rows(kma([{"areaCode": "L1082500"}]))


class TestFailure:
    @pytest.mark.parametrize(
        "response",
        [httpx.Response(500, text="error"), httpx.ReadTimeout("timed out")],
    )
    @pytest.mark.parametrize("which", ["uv", "advisories"])
    async def test_실패는_UpstreamUnavailable_이고_키가_없다(self, response, which, caplog):
        caplog.set_level(logging.DEBUG, logger="app")
        transport = httpx.MockTransport(Recorder(response))
        with pytest.raises(UpstreamUnavailable) as raised:
            if which == "uv":
                await opened(KmaUv(transport=transport, now=lambda: NOW)).uv(
                    grid=BUSAN, day=date(2026, 10, 11)
                )
            else:
                await opened(KmaAdvisories(transport=transport, now=lambda: NOW)).advisories(
                    grid=BUSAN
                )
        assert raised.value.__cause__ is None
        for text in (str(raised.value), caplog.text):
            assert KEY not in text and "serviceKey" not in text and "apis.data.go.kr" not in text


async def test_PublicWeather_는_Activity_날씨_포트다():
    source: WeatherSource = opened(PublicWeather(transport=httpx.MockTransport(Recorder(None))))
    assert all(
        callable(getattr(source, name)) for name in ("forecast", "air_quality", "uv", "advisories")
    )
