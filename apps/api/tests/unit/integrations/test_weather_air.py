"""대기질 어댑터. 에어코리아를 부르지 않는다 — httpx.MockTransport 로 응답을 바꿔 끼운다.

고정하는 것
- 가장 가까운 측정소 고르기 · 너무 멀면 쓰지 않음
- 원문 그대로 넘긴다 — 측정기가 멈춘 "-" 도 해석하지 않는다
- 요청에는 측정소 이름만 싣는다 (격자 · 좌표 없음)
- 캐시 키는 (측정소, 시) — 같은 시간에 두 번 부르지 않는다
- 실패는 전부 UpstreamUnavailable 이고 메시지 · 로그에 인증키와 주소가 없다
"""

import asyncio
import logging
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import settings
from app.core.weather_raw import RawAir, UpstreamUnavailable, WeatherGrid
from app.integrations.weather.air import (
    KST,
    MAX_STATION_CELLS,
    AirKorea,
    nearest_station,
    parse_air,
)

KEY = "test-key/with+plus"
NOW = datetime(2026, 10, 11, 17, 43, tzinfo=KST)
STATIONS = (("가동", 98, 76), ("나동", 98, 77), ("다동", 100, 76), ("섬동", 10, 10))
GRID = WeatherGrid(nx=98, ny=76)


@pytest.fixture(autouse=True)
def service_key(monkeypatch):
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", KEY)


def payload(rows: list[dict], code: str = "00") -> dict:
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": "NORMAL_CODE"},
            "body": {"items": rows, "totalCount": len(rows)},
        }
    }


ROW = {
    "dataTime": "2026-10-11 17:00",
    "pm10Value": "13",
    "pm10Flag": None,
    "pm25Value": "-",
    "pm25Flag": "통신장애",
    "o3Value": "0.050",
    "o3Flag": None,
}


class Recorder:
    def __init__(self, response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def source(response, clock=None) -> tuple[AirKorea, Recorder]:
    recorder = Recorder(response)
    now = clock or (lambda: NOW)
    return (
        AirKorea(transport=httpx.MockTransport(recorder), now=now, stations=STATIONS),
        recorder,
    )


def ok() -> httpx.Response:
    return httpx.Response(200, json=payload([ROW]))


class TestNearest:
    def test_가장_가까운_측정소(self):
        assert nearest_station(WeatherGrid(99, 76), STATIONS) == "가동"
        assert nearest_station(WeatherGrid(100, 77), STATIONS) == "다동"

    def test_같은_거리면_이름_순(self):
        assert nearest_station(WeatherGrid(99, 77), (("나", 98, 77), ("가", 100, 77))) == "가"

    def test_너무_멀면_없다(self):
        far = WeatherGrid(98 + MAX_STATION_CELLS + 1, 76)
        assert nearest_station(far, STATIONS[:1]) is None
        assert nearest_station(WeatherGrid(98 + MAX_STATION_CELLS, 76), STATIONS[:1]) == "가동"

    def test_구운_목록이_비어_있지_않다(self):
        """실제 목록에서 부산 격자는 측정소를 찾는다."""
        assert nearest_station(GRID) is not None


class TestParse:
    def test_원문을_그대로_넘긴다(self):
        assert parse_air(payload([ROW])) == RawAir(
            pm10="13", pm10_flag=None, pm25="-", pm25_flag="통신장애", o3="0.050", o3_flag=None
        )

    @pytest.mark.parametrize("body", [payload([]), payload([ROW], code="22")])
    def test_행이_없거나_결과_코드가_다르면_실패(self, body):
        with pytest.raises(UpstreamUnavailable):
            parse_air(body)


class TestAirQuality:
    async def test_요청에는_측정소_이름만_싣는다(self):
        src, recorder = source(ok())
        await src.air_quality(grid=GRID)
        query = parse_qs(urlparse(str(recorder.requests[0].url)).query)
        assert query["stationName"] == ["가동"]
        assert not {"nx", "ny", "lat", "lon", "tmX", "tmY"} & query.keys()

    async def test_같은_시간에는_한_번만_부른다(self):
        src, recorder = source(ok())
        await src.air_quality(grid=GRID)
        await src.air_quality(grid=WeatherGrid(99, 76))  # 같은 측정소
        assert len(recorder.requests) == 1

    async def test_동시에_불러도_한_번만_부른다(self):
        src, recorder = source(ok())
        await asyncio.gather(src.air_quality(grid=GRID), src.air_quality(grid=GRID))
        assert len(recorder.requests) == 1

    async def test_시가_바뀌면_다시_부른다(self):
        clock = [NOW]
        src, recorder = source(ok(), clock=lambda: clock[0])
        await src.air_quality(grid=GRID)
        clock[0] = datetime(2026, 10, 11, 18, 5, tzinfo=KST)
        await src.air_quality(grid=GRID)
        assert len(recorder.requests) == 2

    async def test_가까운_측정소가_없으면_부르지_않고_실패(self):
        src, recorder = source(ok())
        with pytest.raises(UpstreamUnavailable):
            await src.air_quality(grid=WeatherGrid(60, 40))
        assert recorder.requests == []

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500, text="error"),
            httpx.Response(200, text="<OpenAPI_ServiceResponse>SERVICE_KEY_IS_NOT_REGISTERED"),
            httpx.ReadTimeout("timed out"),
        ],
    )
    async def test_실패는_UpstreamUnavailable_이고_키가_없다(self, response, caplog):
        caplog.set_level(logging.DEBUG, logger="app")
        src, _ = source(response)
        with pytest.raises(UpstreamUnavailable) as raised:
            await src.air_quality(grid=GRID)
        assert raised.value.__cause__ is None
        for text in (str(raised.value), caplog.text):
            assert KEY not in text and "serviceKey" not in text and "apis.data.go.kr" not in text
