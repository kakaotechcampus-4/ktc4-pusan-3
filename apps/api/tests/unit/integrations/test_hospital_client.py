"""국립중앙의료원 병원 어댑터. 실제 API 를 부르지 않는다.

고정하는 것
- 밖으로 나가는 위치 값은 시도 이름뿐이다 (격자 · 좌표 · 시군구 코드 없음)
- 소아청소년과는 하루, 응급실은 10분 동안 시도마다 기억한다 · 같은 목록을 낮/밤 종류로 싣는다
- 페이지를 다 받는다 · 한 건이면 객체 하나로 와도 읽는다 · 좌표 없는 기관은 싣지 않는다
- 실패는 UpstreamUnavailable 이고 메시지 · 로그에 인증키가 없다
- Health `FacilitySource` 포트 모양이다
"""

import logging
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.agents.health.places.ports import FacilitySource
from app.core.config import settings
from app.core.hospital_raw import FacilityTier
from app.core.weather_raw import UpstreamUnavailable, WeatherGrid
from app.integrations.hospital import NmcHospitals
from app.integrations.hospital.provinces import PROVINCE_NAMES

KEY = "test-key/with+plus"
BUSAN = WeatherGrid(nx=98, ny=76)
SEA = WeatherGrid(nx=1, ny=1)


@pytest.fixture(autouse=True)
def service_key(monkeypatch):
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", KEY)


def clinic(name: str, lat="35.20", lon="129.08", **extra) -> dict:
    return {
        "dutyName": name,
        "wgs84Lat": lat,
        "wgs84Lon": lon,
        "dutyTel1": "051-000-0000",
        "dutyTime1s": "0900",
        "dutyTime1c": 1830,  # 숫자로 오기도 한다
        **extra,
    }


def page(items, total=None) -> dict:
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "items": {"item": items},
                "totalCount": total if total is not None else len(items),
            },
        }
    }


class Fake:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def params(self, i: int = 0) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(str(self.requests[i].url)).query).items()}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def adapter(fake: Fake, clock: Clock | None = None) -> NmcHospitals:
    return NmcHospitals(transport=httpx.MockTransport(fake), clock=clock or Clock())


async def test_포트_모양이다():
    source: FacilitySource = adapter(Fake(lambda r: httpx.Response(200, json=page([]))))
    await source.aclose()  # type: ignore[attr-defined]


async def test_시도_이름과_진료과목만_보낸다():
    fake = Fake(lambda r: httpx.Response(200, json=page([clinic("의원")])))
    hospitals = adapter(fake)
    try:
        rows = await hospitals.facilities(grid=BUSAN, tier=FacilityTier.PEDIATRIC_CLINIC)
    finally:
        await hospitals.aclose()

    params = fake.params()
    assert params["Q0"] == "부산광역시"
    assert params["QD"] == "D002"
    assert "Q1" not in params
    assert not {"nx", "ny", "lat", "lon", "WGS84_LAT", "WGS84_LON"} & set(params)
    assert rows[0].name == "의원"
    assert rows[0].hours[0] == ("0900", "1830")
    assert rows[0].phone == "051-000-0000"


async def test_한_건이면_객체로_와도_읽고_좌표_없는_곳은_뺀다():
    def handler(request):
        if "Ermct" in str(request.url):
            return httpx.Response(200, json=page({"dutyName": "좌표없음"}, total=1))
        return httpx.Response(200, json=page({**clinic("의원")}, total=1))

    hospitals = adapter(Fake(handler))
    try:
        clinics = await hospitals.facilities(grid=BUSAN, tier=FacilityTier.PEDIATRIC_CLINIC)
        ers = await hospitals.facilities(grid=BUSAN, tier=FacilityTier.EMERGENCY_ROOM)
    finally:
        await hospitals.aclose()

    assert [c.name for c in clinics] == ["의원"]
    assert ers == []


async def test_페이지를_다_받는다():
    def handler(request):
        page_no = int(parse_qs(urlparse(str(request.url)).query)["pageNo"][0])
        return httpx.Response(200, json=page([clinic(f"의원{page_no}")], total=1500))

    fake = Fake(handler)
    hospitals = adapter(fake)
    try:
        rows = await hospitals.facilities(grid=BUSAN, tier=FacilityTier.PEDIATRIC_CLINIC)
    finally:
        await hospitals.aclose()

    assert sorted(r.name for r in rows) == ["의원1", "의원2"]
    assert len(fake.requests) == 2


async def test_소아과는_하루_기억하고_낮밤_종류로_나눠_싣는다():
    fake = Fake(lambda r: httpx.Response(200, json=page([clinic("의원")])))
    clock = Clock()
    hospitals = adapter(fake, clock)
    try:
        day = await hospitals.facilities(grid=BUSAN, tier=FacilityTier.PEDIATRIC_CLINIC)
        clock.now = 23 * 60 * 60
        night = await hospitals.facilities(grid=BUSAN, tier=FacilityTier.NIGHT_CLINIC)
        assert len(fake.requests) == 1  # 같은 목록이다
        clock.now = 25 * 60 * 60
        await hospitals.facilities(grid=BUSAN, tier=FacilityTier.PEDIATRIC_CLINIC)
    finally:
        await hospitals.aclose()

    assert day[0].tier is FacilityTier.PEDIATRIC_CLINIC
    assert night[0].tier is FacilityTier.NIGHT_CLINIC
    assert len(fake.requests) == 2  # 하루가 지나 다시 받았다


async def test_응급실은_10분만_기억한다():
    er = {"dutyName": "응급실", "wgs84Lat": "35.2", "wgs84Lon": "129.0", "dutyTel3": "051-119"}
    fake = Fake(lambda r: httpx.Response(200, json=page([er])))
    clock = Clock()
    hospitals = adapter(fake, clock)
    try:
        rows = await hospitals.facilities(grid=BUSAN, tier=FacilityTier.EMERGENCY_ROOM)
        clock.now = 11 * 60
        await hospitals.facilities(grid=BUSAN, tier=FacilityTier.EMERGENCY_ROOM)
    finally:
        await hospitals.aclose()

    assert rows[0].phone == "051-119"  # 응급실 전화가 먼저다
    assert rows[0].hours == ()
    assert len(fake.requests) == 2


async def test_바다_격자는_묻지_않고_빈_목록():
    fake = Fake(lambda r: httpx.Response(200, json=page([clinic("의원")])))
    hospitals = adapter(fake)
    try:
        assert await hospitals.facilities(grid=SEA, tier=FacilityTier.EMERGENCY_ROOM) == []
    finally:
        await hospitals.aclose()
    assert fake.requests == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="<OpenAPI_ServiceResponse>SERVICE_KEY</OpenAPI_ServiceResponse>"),
        httpx.Response(200, json={"response": {"header": {"resultCode": "30"}}}),
    ],
)
async def test_실패는_빈_목록이_아니라_UpstreamUnavailable(response, caplog):
    caplog.set_level(logging.WARNING)
    hospitals = adapter(Fake(lambda r: response))
    try:
        with pytest.raises(UpstreamUnavailable) as caught:
            await hospitals.facilities(grid=BUSAN, tier=FacilityTier.PEDIATRIC_CLINIC)
    finally:
        await hospitals.aclose()

    assert KEY not in str(caught.value)
    assert "test-key" not in caplog.text


def test_시도_표는_날씨_변환표의_시도를_전부_덮는다():
    """변환표에 있는 시도가 여기 없으면 그 지역은 늘 빈 목록이다."""
    from app.integrations.weather.areas_data import DISTRICTS

    assert {code[:2] for code, _, _ in DISTRICTS} <= set(PROVINCE_NAMES)
