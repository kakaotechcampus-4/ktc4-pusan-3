"""단기예보 어댑터. 기상청을 부르지 않는다 — httpx.MockTransport 로 응답을 바꿔 끼운다.

고정하는 것
- 발표 시각 고르기(발표 10분 뒤부터) · 날마다 볼 칸
- 원문 그대로 넘긴다 — 해석하지 않는다
- 캐시 키는 발표 시각이다. 같은 발표분은 한 번만 부른다(동시에 불러도)
- 실패는 전부 UpstreamUnavailable 이고, 메시지 · 로그에 인증키와 주소가 없다
"""

import asyncio
import logging
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import settings
from app.core.weather_raw import RawForecast, UpstreamUnavailable, WeatherGrid
from app.integrations.weather.forecast import KST, KmaForecast, latest_base, parse_slots, slot_for

GRID = WeatherGrid(nx=98, ny=76)
NOW = datetime(2026, 10, 11, 17, 43, tzinfo=KST)  # 일요일
KEY = "test-key/with+plus"


@pytest.fixture(autouse=True)
def service_key(monkeypatch):
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", KEY)


# 테스트가 만든 어댑터. 끝나면 닫는다 — 안 닫으면 httpx 클라이언트가 남는다 (#328 리뷰)
_OPENED: list[KmaForecast] = []


@pytest.fixture(autouse=True)
async def close_sources():
    yield
    while _OPENED:
        await _OPENED.pop().aclose()


def opened(src: KmaForecast) -> KmaForecast:
    _OPENED.append(src)
    return src


def item(day: str, hour: str, category: str, value: str) -> dict:
    return {"fcstDate": day, "fcstTime": hour, "category": category, "fcstValue": value}


def payload(items: list[dict], code: str = "00") -> dict:
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": "NORMAL_SERVICE"},
            "body": {"items": {"item": items}},
        }
    }


ITEMS = [
    item("20261011", "1800", "SKY", "4"),
    item("20261011", "1800", "PCP", "강수없음"),
    item("20261011", "1800", "POP", "30"),
    item("20261011", "1800", "TMP", "26"),  # 쓰지 않는 종류는 버린다
    item("20261011", "1900", "SKY", "1"),
    item("20261013", "1200", "SKY", "4"),
    item("20261013", "1200", "PCP", "2.0mm"),
    item("20261013", "1200", "POP", "60"),
]


class Recorder:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def source(response, now=NOW) -> tuple[KmaForecast, Recorder]:
    recorder = Recorder(response)
    return opened(KmaForecast(transport=httpx.MockTransport(recorder), now=lambda: now)), recorder


def ok(items=ITEMS) -> httpx.Response:
    return httpx.Response(200, json=payload(items))


class TestLatestBase:
    @pytest.mark.parametrize(
        ("hour", "minute", "expected"),
        [
            (17, 43, ("20261011", "1700")),
            (17, 9, ("20261011", "1400")),  # 발표 10분 전에는 아직 없다
            (17, 10, ("20261011", "1700")),
            (2, 10, ("20261011", "0200")),
            (1, 0, ("20261010", "2300")),  # 새벽은 전날 23시 발표
        ],
    )
    def test_발표_10분_뒤부터_그_발표를_쓴다(self, hour, minute, expected):
        assert latest_base(datetime(2026, 10, 11, hour, minute, tzinfo=KST)) == expected

    def test_다른_시간대로_와도_KST_로_본다(self):
        utc = datetime(2026, 10, 11, 8, 43, tzinfo=KST).astimezone(UTC)
        assert latest_base(utc) == ("20261011", "0800")


class TestSlot:
    slots = parse_slots(payload(ITEMS))

    def test_오늘은_지금_이후_첫_칸(self):
        assert slot_for(date(2026, 10, 11), NOW, self.slots) == ("20261011", "1800")

    def test_다른_날은_12시_칸(self):
        assert slot_for(date(2026, 10, 13), NOW, self.slots) == ("20261013", "1200")

    def test_칸이_없으면_None(self):
        assert slot_for(date(2026, 10, 12), NOW, self.slots) is None
        late = datetime(2026, 10, 11, 20, 30, tzinfo=KST)
        assert slot_for(date(2026, 10, 11), late, self.slots) is None

    def test_쓰는_세_종류만_남긴다(self):
        assert self.slots[("20261011", "1800")] == {"SKY": "4", "PCP": "강수없음", "POP": "30"}


class TestForecast:
    async def test_원문을_그대로_넘긴다(self):
        src, _ = source(ok())
        assert await src.forecast(grid=GRID, day=date(2026, 10, 13)) == RawForecast(
            sky="4", pcp="2.0mm", pop="60"
        )

    async def test_요청에는_격자와_발표_시각만_싣는다(self):
        src, recorder = source(ok())
        await src.forecast(grid=GRID, day=date(2026, 10, 11))
        query = parse_qs(urlparse(str(recorder.requests[0].url)).query)
        assert query["nx"] == ["98"] and query["ny"] == ["76"]
        assert query["base_date"] == ["20261011"] and query["base_time"] == ["1700"]
        assert query["serviceKey"] == [KEY]

    async def test_Encoding_키는_한_번_풀어서_보낸다(self, monkeypatch):
        monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", "test-key%2Fwith%2Bplus")
        src, recorder = source(ok())
        await src.forecast(grid=GRID, day=date(2026, 10, 11))
        query = parse_qs(urlparse(str(recorder.requests[0].url)).query)
        assert query["serviceKey"] == [KEY]

    async def test_같은_발표분은_한_번만_부른다(self):
        src, recorder = source(ok())
        await src.forecast(grid=GRID, day=date(2026, 10, 11))
        await src.forecast(grid=GRID, day=date(2026, 10, 13))
        assert len(recorder.requests) == 1

    async def test_동시에_불러도_한_번만_부른다(self):
        """이번 주말(토 · 일)은 두 날을 동시에 묻는다."""
        src, recorder = source(ok())
        await asyncio.gather(
            src.forecast(grid=GRID, day=date(2026, 10, 11)),
            src.forecast(grid=GRID, day=date(2026, 10, 13)),
        )
        assert len(recorder.requests) == 1

    async def test_새_발표가_나오면_다시_부른다(self):
        clock = [NOW]
        recorder = Recorder(ok())
        src = opened(KmaForecast(transport=httpx.MockTransport(recorder), now=lambda: clock[0]))
        await src.forecast(grid=GRID, day=date(2026, 10, 13))
        clock[0] = datetime(2026, 10, 11, 20, 15, tzinfo=KST)
        await src.forecast(grid=GRID, day=date(2026, 10, 13))
        assert len(recorder.requests) == 2

    async def test_그날_칸이_없으면_실패다(self):
        src, _ = source(ok())
        with pytest.raises(UpstreamUnavailable):
            await src.forecast(grid=GRID, day=date(2026, 10, 12))


class TestLocks:
    async def test_실패해도_Lock_을_남기지_않는다(self):
        src, _ = source(httpx.Response(500, text="error"))
        with pytest.raises(UpstreamUnavailable):
            await src.forecast(grid=GRID, day=date(2026, 10, 11))
        assert src._locks == {}

    async def test_성공하면_Lock_을_남기지_않는다(self):
        src, _ = source(ok())
        await src.forecast(grid=GRID, day=date(2026, 10, 11))
        assert src._locks == {}


class TestFailure:
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500, text="error"),
            httpx.Response(200, text="<OpenAPI_ServiceResponse>SERVICE_KEY_IS_NOT_REGISTERED"),
            httpx.Response(200, json=payload([], code="03")),
            httpx.ReadTimeout("timed out"),
            httpx.ConnectError("refused"),
        ],
    )
    async def test_실패는_전부_UpstreamUnavailable(self, response, caplog):
        # httpx 자신의 INFO 요청 줄(주소 · 키 포함)은 logging_config 가 루트를 WARNING 으로
        # 둬서 막는다. 여기서는 이 어댑터가 남기는 로그만 본다
        caplog.set_level(logging.DEBUG, logger="app")
        src, _ = source(response)
        with pytest.raises(UpstreamUnavailable) as raised:
            await src.forecast(grid=GRID, day=date(2026, 10, 11))
        assert raised.value.__cause__ is None
        for text in (str(raised.value), caplog.text):
            assert KEY not in text and "serviceKey" not in text and "apis.data.go.kr" not in text

    async def test_키가_없으면_부르지_않고_실패다(self, monkeypatch):
        monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", "")
        src, recorder = source(ok())
        with pytest.raises(UpstreamUnavailable):
            await src.forecast(grid=GRID, day=date(2026, 10, 11))
        assert recorder.requests == []
