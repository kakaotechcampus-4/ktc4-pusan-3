"""TourAPI 문화시설 · 관광지 조회(박물관 · 과학관 · 체험관). 한국관광공사를 부르지 않는다.

고정하는 것
- 시 · 도 이름을 TourAPI 법정동 시도 코드로 바꿔(`ldongCode2`, 한 번만) 그 시 · 도 목록을 받는다 —
  좌표를 보내지 않는다(`locationBasedList2` 는 예전 지역 코드가 붙은 행만 줘서 쓰지 않는다)
- 문화시설(14) · 관광지(12) 두 타입을 받고 시 · 도마다 그날 하루 캐시한다
- 원문은 이름 · 좌표 · 타입 · 분류만 옮긴다(전화번호 · 이미지 · 주소는 나가지 않는다)
- 모르는 시 · 도는 실패다
"""

from datetime import datetime

import httpx
import pytest

from app.core.culture_raw import RawSite
from app.core.weather_raw import UpstreamUnavailable
from tests.unit.integrations.test_tour_events import KST, Recorder, page, source

CODES = page([{"code": "26", "name": "부산광역시"}, {"code": "48", "name": "경상남도"}])
MUSEUM = {
    "contentid": "126100",
    "contenttypeid": "14",
    "title": "부산박물관",
    "mapx": "129.0947549351",
    "mapy": "35.1295140526",
    "lclsSystm2": "VE07",
    "tel": "051-000-0000",
    "addr1": "부산광역시 남구",
    "firstimage": "http://example.invalid/a.jpg",
}
SCIENCE = {**MUSEUM, "contentid": "200", "contenttypeid": "12", "title": "부산과학체험관"}


class TestSites:
    async def test_시도_코드로_두_타입을_받고_좌표를_보내지_않는다(self):
        recorder = Recorder(CODES, page([MUSEUM]), page([SCIENCE]))

        rows = await source(recorder).sites(province="부산광역시")

        assert recorder.params(0)["numOfRows"]  # 코드표
        for index, content_type in ((1, "14"), (2, "12")):
            params = recorder.params(index)
            assert params["lDongRegnCd"] == ["26"]
            assert params["contentTypeId"] == [content_type]
            assert not {"mapX", "mapY", "radius"} & set(params)
        assert rows == [
            RawSite(
                content_id="126100",
                title="부산박물관",
                mapx="129.0947549351",
                mapy="35.1295140526",
                content_type="14",
                category="VE07",
            ),
            RawSite(
                content_id="200",
                title="부산과학체험관",
                mapx="129.0947549351",
                mapy="35.1295140526",
                content_type="12",
                category="VE07",
            ),
        ]

    async def test_같은_날_같은_시도는_한_번만_부른다(self):
        recorder = Recorder(CODES, page([MUSEUM]), page([]))
        src = source(recorder)

        await src.sites(province="부산광역시")
        await src.sites(province="부산광역시")

        assert len(recorder.requests) == 3

    async def test_날이_바뀌면_다시_받는다(self):
        recorder = Recorder(CODES, page([MUSEUM]), page([]))
        clock = {"now": datetime(2026, 11, 2, 10, tzinfo=KST)}
        src = source(recorder)
        src._now = lambda: clock["now"]

        await src.sites(province="부산광역시")
        clock["now"] = datetime(2026, 11, 3, 10, tzinfo=KST)
        await src.sites(province="부산광역시")

        assert len(recorder.requests) == 5  # 코드표는 다시 받지 않는다

    async def test_모르는_시도는_실패다(self):
        with pytest.raises(UpstreamUnavailable):
            await source(Recorder(CODES)).sites(province="없는도")

    async def test_조회_실패는_UpstreamUnavailable(self):
        with pytest.raises(UpstreamUnavailable):
            await source(Recorder(CODES, httpx.Response(500))).sites(province="부산광역시")
