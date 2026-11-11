"""날씨 조회 다섯 곳 — 같은 조회를 동시에 부르면 하나를 결과째(실패도) 같이 기다린다 (#959 · #962).

"이번 주말" 은 토 · 일을 동시에 본다(`check_weather` 를 날마다 `gather`). 잠금으로 한 번만 받으면
첫 조회가 실패했을 때 기다리던 호출이 하나씩 다시 불러, 서버가 계속 느린 날 기다림이
"읽기 제한 × 호출 수" 가 된다. 기상청을 부르지 않는다 — httpx.MockTransport 로 바꿔 끼운다.

고정하는 것
- 동시에 부르다 실패해도 요청은 한 번이고 둘 다 실패를 받는다
- 한 호출이 취소돼도(run 20초) 같이 기다리던 호출은 같은 조회의 결과를 받는다
- 끝나면 받는 중 표시가 남지 않는다 (#328 리뷰)
- 특보는 새 조회가 실패하면 한 시간 안의 목록을 쓰는 것을 동시 호출 모두에 지킨다
- 끝난 조회는 다시 부른다 — 캐시는 헬퍼가 아니라 조회 함수의 일이다
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.core.weather_raw import UpstreamUnavailable, WeatherGrid
from app.integrations.weather.advisories import KmaAdvisories
from app.integrations.weather.air import AirKorea
from app.integrations.weather.forecast import KST, KmaForecast
from app.integrations.weather.inflight import Inflight
from app.integrations.weather.mid import KmaMidForecast
from app.integrations.weather.uv import KmaUv

# 목요일 18시. 이번 주말은 토 14 · 일 15. 중기예보는 이날 06시 발표라 4일 뒤(16)부터다
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=KST)
BUSAN = WeatherGrid(nx=98, ny=76)
WEEKEND = (date(2026, 11, 14), date(2026, 11, 15))
MID_DAYS = (date(2026, 11, 16), date(2026, 11, 17))


@pytest.fixture(autouse=True)
def service_key(monkeypatch):
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", "test-key")


_OPENED: list = []


@pytest.fixture(autouse=True)
async def close_sources():
    yield
    while _OPENED:
        await _OPENED.pop().aclose()


Make = Callable[[httpx.AsyncBaseTransport], Any]
Call = Callable[[Any, int], Awaitable[object]]

# 조회마다 (어댑터 만들기, i 번째 동시 호출). i 는 토 · 일처럼 같은 조회를 부르는 서로 다른 호출이다
CASES: dict[str, tuple[Make, Call]] = {
    "단기예보": (
        lambda transport: KmaForecast(transport=transport, now=lambda: NOW),
        lambda src, i: src.forecast(grid=BUSAN, day=WEEKEND[i]),
    ),
    "중기예보": (
        lambda transport: KmaMidForecast(transport=transport, now=lambda: NOW),
        lambda src, i: src.forecast(grid=BUSAN, day=MID_DAYS[i]),
    ),
    "자외선": (
        lambda transport: KmaUv(transport=transport, now=lambda: NOW),
        lambda src, i: src.uv(grid=BUSAN, day=WEEKEND[i]),
    ),
    "대기질": (
        lambda transport: AirKorea(transport=transport, now=lambda: NOW),
        lambda src, i: src.air_quality(grid=BUSAN),
    ),
    "특보": (
        lambda transport: KmaAdvisories(transport=transport, now=lambda: NOW),
        lambda src, i: src.advisories(grid=BUSAN),
    ),
}
each = pytest.mark.parametrize("name", list(CASES))


def opened(name: str, handler: Callable[[httpx.Request], Any]) -> tuple[Any, Call]:
    make, call = CASES[name]
    src = make(httpx.MockTransport(handler))
    _OPENED.append(src)
    return src, call


class Failing:
    """요청을 세고 500 을 준다. gate 가 있으면 열릴 때까지 응답을 미룬다."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        return httpx.Response(500, text="error")


@each
async def test_동시에_부르다_실패해도_요청은_한_번이다(name):
    server = Failing()
    src, call = opened(name, server)
    results = await asyncio.gather(call(src, 0), call(src, 1), return_exceptions=True)
    assert all(isinstance(result, UpstreamUnavailable) for result in results)
    assert len(server.requests) == 1


@each
async def test_한_호출이_취소돼도_같이_기다리던_호출은_같은_조회의_결과를_받는다(name):
    server = Failing(gate=asyncio.Event())
    src, call = opened(name, server)
    first = asyncio.create_task(call(src, 0))
    second = asyncio.create_task(call(src, 1))
    await asyncio.sleep(0)  # 둘 다 같은 조회를 기다리게 한다
    first.cancel()
    assert server.gate is not None
    server.gate.set()
    with pytest.raises(UpstreamUnavailable):
        await second
    with pytest.raises(asyncio.CancelledError):
        await first
    assert len(server.requests) == 1


@each
async def test_끝나면_받는_중_표시가_남지_않는다(name):
    src, call = opened(name, Failing())
    with pytest.raises(UpstreamUnavailable):
        await call(src, 0)
    assert len(src._inflight) == 0


async def test_특보_새_조회가_실패하면_동시_호출_모두_한_시간_안의_목록을_받는다():
    clock = [NOW]
    rows = [{"areaCode": "L1082500", "warnVar": "2", "warnStress": "0", "command": "1",
             "tmFc": "202611121000", "tmSeq": "1", "cancel": "0"}]  # fmt: skip
    body = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": rows}}}}
    responses = [httpx.Response(200, json=body), httpx.Response(500, text="error")]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses[min(len(requests), len(responses)) - 1]

    src = KmaAdvisories(transport=httpx.MockTransport(handler), now=lambda: clock[0])
    _OPENED.append(src)
    first = await src.advisories(grid=BUSAN)
    clock[0] = NOW + timedelta(minutes=30)  # 캐시는 지났고 새 조회는 실패한다
    results = await asyncio.gather(src.advisories(grid=BUSAN), src.advisories(grid=BUSAN))
    assert results == [first, first]
    assert len(requests) == 2


class TestInflight:
    async def test_같은_키는_한_번만_부르고_결과를_같이_받는다(self):
        calls: list[str] = []
        gate = asyncio.Event()

        async def fetch() -> str:
            calls.append("k")
            await gate.wait()
            return "값"

        inflight: Inflight[str, str] = Inflight()
        first = asyncio.create_task(inflight.run("k", fetch))
        second = asyncio.create_task(inflight.run("k", fetch))
        await asyncio.sleep(0)
        gate.set()
        assert await asyncio.gather(first, second) == ["값", "값"]
        assert calls == ["k"]

    async def test_다른_키는_따로_부른다(self):
        calls: list[str] = []

        def fetch(key: str) -> Callable[[], Awaitable[str]]:
            async def run() -> str:
                calls.append(key)
                await asyncio.sleep(0)
                return key

            return run

        inflight: Inflight[str, str] = Inflight()
        assert await asyncio.gather(
            inflight.run("a", fetch("a")), inflight.run("b", fetch("b"))
        ) == [
            "a",
            "b",
        ]
        assert sorted(calls) == ["a", "b"]

    async def test_끝난_조회는_성공이든_실패든_다시_부른다(self):
        outcomes: list[object] = [UpstreamUnavailable("실패"), "값", "다음 값"]

        async def fetch() -> str:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return str(outcome)

        inflight: Inflight[str, str] = Inflight()
        with pytest.raises(UpstreamUnavailable):
            await inflight.run("k", fetch)
        assert await inflight.run("k", fetch) == "값"
        assert await inflight.run("k", fetch) == "다음 값"
        assert len(inflight) == 0


@each
async def test_닫으면_받는_중인_조회를_끊고_남기지_않는다(name):
    server = Failing(gate=asyncio.Event())
    make, call = CASES[name]
    src = make(httpx.MockTransport(server))
    waiting = asyncio.create_task(call(src, 0))
    await asyncio.sleep(0)  # 조회가 시작되게 한다
    await src.aclose()
    assert len(src._inflight) == 0
    with pytest.raises((asyncio.CancelledError, UpstreamUnavailable)):
        await waiting
