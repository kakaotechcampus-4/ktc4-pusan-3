"""중기 육상예보 어댑터. 기상청을 부르지 않는다.

고정하는 것
- 격자를 중기 육상예보 구역(regId)으로 바꿔 묻는다 — 좌표 · 격자를 보내지 않는다
- 발표 시각(06 · 18시)과 날 차이로 그날 오전 · 오후 칸을 읽는다 — 강수확률은 둘 중 큰 값, 하늘은
  비 · 눈이 든 쪽(없으면 오후)
- 4일 뒤보다 가깝거나 10일 뒤보다 멀면 실패다 · 같은 구역 · 같은 발표는 한 번만 부른다
- PublicWeather 는 단기예보에 그날 칸이 없고 3일 이상 뒤면 중기예보로 넘어간다
"""

from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import settings
from app.core.weather_raw import RawForecast, UpstreamUnavailable, WeatherGrid
from app.integrations.weather.mid import KST, KmaMidForecast, latest_tm
from app.integrations.weather.source import PublicWeather

BUSAN = WeatherGrid(nx=98, ny=76)
NOW = datetime(2026, 11, 9, 10, tzinfo=KST)  # 월요일 — 06시 발표가 최신


@pytest.fixture(autouse=True)
def service_key(monkeypatch):
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", "test-key")


_OPENED: list = []


@pytest.fixture(autouse=True)
async def close_sources():
    yield
    while _OPENED:
        await _OPENED.pop().aclose()


def land(**values):
    item = {"regId": "11H20000", **values}
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {"items": {"item": [item]}, "totalCount": 1},
        }
    }


class Recorder:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses[min(len(self.requests), len(self.responses)) - 1]
        return (
            response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)
        )

    def params(self, index=-1):
        return parse_qs(urlparse(str(self.requests[index].url)).query)


def mid(recorder, now=NOW) -> KmaMidForecast:
    src = KmaMidForecast(transport=httpx.MockTransport(recorder), now=lambda: now)
    _OPENED.append(src)
    return src


def test_발표_시각은_06시_18시_중_최신이다() -> None:
    assert latest_tm(datetime(2026, 11, 9, 10, tzinfo=KST)) == ("202611090600", date(2026, 11, 9))
    assert latest_tm(datetime(2026, 11, 9, 20, tzinfo=KST)) == ("202611091800", date(2026, 11, 9))
    assert latest_tm(datetime(2026, 11, 9, 5, tzinfo=KST)) == ("202611081800", date(2026, 11, 8))


async def test_구역_코드로_묻고_그날_오전_오후를_읽는다() -> None:
    recorder = Recorder(land(rnSt5Am=20, rnSt5Pm=70, wf5Am="맑음", wf5Pm="흐리고 비"))

    raw = await mid(recorder).forecast(grid=BUSAN, day=date(2026, 11, 14))  # 토 = 5일 뒤

    params = recorder.params()
    assert params["regId"] == ["11H20000"] and params["tmFc"] == ["202611090600"]
    assert not {"nx", "ny", "lat", "lon"} & set(params)
    assert raw == RawForecast(sky=None, pcp=None, pop="70", wf="흐리고 비")


async def test_8일_뒤부터는_하루_칸이다() -> None:
    raw = await mid(Recorder(land(rnSt8=30, wf8="구름많음"))).forecast(
        grid=BUSAN, day=date(2026, 11, 17)
    )
    assert raw == RawForecast(sky=None, pcp=None, pop="30", wf="구름많음")


@pytest.mark.parametrize("day", [date(2026, 11, 12), date(2026, 11, 20)])
async def test_범위_밖은_실패다(day) -> None:
    with pytest.raises(UpstreamUnavailable):
        await mid(Recorder(land(rnSt5Am=1))).forecast(grid=BUSAN, day=day)


async def test_같은_구역_같은_발표는_한_번만_부른다() -> None:
    recorder = Recorder(
        land(
            rnSt5Am=0,
            rnSt5Pm=0,
            wf5Am="맑음",
            wf5Pm="맑음",
            rnSt6Am=0,
            rnSt6Pm=0,
            wf6Am="맑음",
            wf6Pm="맑음",
        )
    )
    src = mid(recorder)
    await src.forecast(grid=BUSAN, day=date(2026, 11, 14))
    await src.forecast(grid=BUSAN, day=date(2026, 11, 15))
    assert len(recorder.requests) == 1


async def test_PublicWeather_는_단기예보에_없는_먼_날을_중기예보로_본다() -> None:
    source = PublicWeather(transport=httpx.MockTransport(Recorder(None)), now=lambda: NOW)
    _OPENED.append(source)

    async def no_slot(**_):
        raise UpstreamUnavailable("단기예보에 그날 칸이 없다")

    async def from_mid(*, grid, day):
        return RawForecast(sky=None, pcp=None, pop="10", wf="맑음")

    source._forecast.forecast = no_slot
    source._mid.forecast = from_mid

    assert (await source.forecast(grid=BUSAN, day=date(2026, 11, 14))).wf == "맑음"
    with pytest.raises(UpstreamUnavailable):  # 가까운 날은 중기예보로 메우지 않는다
        await source.forecast(grid=BUSAN, day=date(2026, 11, 10))
