"""TourAPI 행사 어댑터. 한국관광공사를 부르지 않는다.

고정하는 것
- 위치를 보내지 않고 그날 이후에 끝나는 전국 목록을 받는다 · 날짜마다 한 번만 부른다
- 원문은 core 모양의 칸만 옮긴다(요금 · 전화번호 · 이미지는 나가지 않는다) · 빈 목록은 "" 로 온다
- 소개정보는 관람 연령 · 장소만 · 행사마다 캐시 · 없는 행사는 실패
- 실패는 UpstreamUnavailable 이고 메시지 · 로그에 인증키와 주소가 없다
"""

import logging
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.agents.activity.store.ports import EventSource
from app.core.config import settings
from app.core.event_raw import RawEvent, RawEventIntro
from app.core.weather_raw import UpstreamUnavailable
from app.integrations.tour import events as tour
from app.integrations.tour.events import KST, TourEvents

KEY = "test-key/with+plus"
TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 10, tzinfo=KST)


@pytest.fixture(autouse=True)
def service_key(monkeypatch):
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", KEY)


_OPENED: list = []


@pytest.fixture(autouse=True)
async def close_sources():
    yield
    while _OPENED:
        await _OPENED.pop().aclose()


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

    def params(self, index: int = -1) -> dict[str, list[str]]:
        return parse_qs(urlparse(str(self.requests[index].url)).query)


def source(recorder: Recorder, *, now=NOW) -> TourEvents:
    src = TourEvents(transport=httpx.MockTransport(recorder), now=lambda: now)
    _OPENED.append(src)
    return src


def page(items, *, total=None, code="0000"):
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": "OK"},
            "body": {
                "items": {"item": items} if items else "",
                "numOfRows": 1000,
                "pageNo": 1,
                "totalCount": len(items) if total is None else total,
            },
        }
    }


FESTIVAL = {
    "contentid": "3360113",
    "title": "부산 바다 축제",
    "eventstartdate": "20261101",
    "eventenddate": "20261109",
    "mapx": "129.1350266435",
    "mapy": "35.1657825587",
    "lclsSystm2": "EV01",
    "progresstype": "",
    "addr1": "부산광역시 해운대구",
    "tel": "051-000-0000",
    "firstimage": "http://example.invalid/a.jpg",
}


class TestEvents:
    async def test_위치를_보내지_않고_그날_이후에_끝나는_전국_목록을_받는다(self):
        recorder = Recorder(page([FESTIVAL]))

        rows = await source(recorder).events(ending_on_or_after=TODAY)

        params = recorder.params()
        assert params["eventStartDate"] == ["20261102"]
        assert params["_type"] == ["json"]
        assert not {"mapX", "mapY", "radius", "areaCode", "lDongRegnCd"} & set(params)
        assert rows == [
            RawEvent(
                content_id="3360113",
                title="부산 바다 축제",
                start="20261101",
                end="20261109",
                mapx="129.1350266435",
                mapy="35.1657825587",
                kind_code="EV01",
                progress="",
            )
        ]

    async def test_빈_목록은_빈_문자열로_온다(self):
        rows = await source(Recorder(page([]))).events(ending_on_or_after=TODAY)
        assert rows == []

    async def test_같은_날은_한_번만_부르고_날이_바뀌면_다시_부른다(self):
        recorder = Recorder(page([FESTIVAL]))
        src = source(recorder)

        await src.events(ending_on_or_after=TODAY)
        await src.events(ending_on_or_after=TODAY)
        assert len(recorder.requests) == 1

        await src.events(ending_on_or_after=date(2026, 11, 3))
        assert len(recorder.requests) == 2

    async def test_한_쪽을_넘으면_다음_쪽을_부른다(self, monkeypatch):
        monkeypatch.setattr(tour, "ROWS", 1)
        second = {**FESTIVAL, "contentid": "2", "title": "가을 공연", "lclsSystm2": "EV02"}
        recorder = Recorder(page([FESTIVAL], total=2), page([second], total=2))

        rows = await source(recorder).events(ending_on_or_after=TODAY)

        assert [row.content_id for row in rows] == ["3360113", "2"]
        assert [recorder.params(i)["pageNo"] for i in range(2)] == [["1"], ["2"]]

    async def test_숫자로_온_칸도_글자로_옮긴다(self):
        numeric = {**FESTIVAL, "contentid": 3360113, "mapx": 129.13, "eventstartdate": 20261101}
        (row,) = await source(Recorder(page([numeric]))).events(ending_on_or_after=TODAY)
        assert (row.content_id, row.mapx, row.start) == ("3360113", "129.13", "20261101")

    async def test_실패한_조회는_캐시하지_않는다(self):
        recorder = Recorder(httpx.Response(500), page([FESTIVAL]))
        src = source(recorder)

        with pytest.raises(UpstreamUnavailable):
            await src.events(ending_on_or_after=TODAY)
        assert len(await src.events(ending_on_or_after=TODAY)) == 1


class TestIntro:
    def intro(self, **item):
        return page(
            [{"contentid": "3360113", "agelimit": "전 연령", "eventplace": "벡스코", **item}]
        )

    async def test_관람_연령과_장소만_옮긴다(self):
        recorder = Recorder(self.intro(usetimefestival="무료", sponsor1tel="02-000-0000"))

        intro = await source(recorder).intro(content_id="3360113")

        assert intro == RawEventIntro(age_limit="전 연령", place="벡스코")
        assert recorder.params()["contentId"] == ["3360113"]
        assert recorder.params()["contentTypeId"] == ["15"]

    async def test_빈_칸은_빈_글자다(self):
        intro = await source(Recorder(self.intro(agelimit=None, eventplace=""))).intro(
            content_id="3360113"
        )
        assert intro == RawEventIntro(age_limit="", place="")

    async def test_행사마다_한_번만_부른다(self):
        recorder = Recorder(self.intro())
        src = source(recorder)

        await src.intro(content_id="3360113")
        await src.intro(content_id="3360113")

        assert len(recorder.requests) == 1

    async def test_없는_행사는_실패다(self):
        with pytest.raises(UpstreamUnavailable):
            await source(Recorder(page([]))).intro(content_id="1")


class TestFailure:
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500),
            httpx.Response(200, text="<OpenAPI_ServiceResponse>SERVICE_KEY_IS_NOT_REGISTERED"),
            page([], code="0030"),
            {"response": {"header": {"resultCode": "0000"}, "body": {"items": ["이상한 모양"]}}},
        ],
    )
    async def test_실패는_UpstreamUnavailable_이고_키와_주소가_새지_않는다(self, response, caplog):
        caplog.set_level(logging.WARNING)

        with pytest.raises(UpstreamUnavailable) as raised:
            await source(Recorder(response)).events(ending_on_or_after=TODAY)

        for text in (str(raised.value), caplog.text):
            assert KEY not in text and "apis.data.go.kr" not in text


def test_Activity_포트_모양이다():
    src: EventSource = TourEvents()
    assert callable(src.events) and callable(src.intro)
