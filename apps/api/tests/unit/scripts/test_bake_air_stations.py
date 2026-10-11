"""측정소 목록 굽기. 에어코리아를 부르지 않는다 — 응답 모양만 넣어 본다."""

import pytest

from app.rules.kma_grid import latlon_to_grid
from scripts.bake_air_stations import changes, parse_stations, render
from scripts.bake_holidays import BakeError

BUSAN = (35.099849, 129.030344)  # 위도 · 경도


def row(name, *, network="도시대기", items="SO2, CO, O3, NO2, PM10, PM2.5", lat_lon=BUSAN):
    lat, lon = lat_lon
    return {
        "stationName": name,
        "mangName": network,
        "item": items,
        "dmX": str(lat),  # dmX 가 위도다
        "dmY": str(lon),
        "addr": "주소는 굽지 않는다",
    }


def payload(rows, code="00"):
    return {"response": {"header": {"resultCode": code, "resultMsg": "x"}, "body": {"items": rows}}}


class TestParse:
    def test_dmX_를_위도로_읽어_격자로_굽는다(self):
        [(name, nx, ny)] = parse_stations(payload([row("광복동")]))
        assert (name, (nx, ny)) == ("광복동", latlon_to_grid(*BUSAN))

    def test_사는_곳_측정망만_남긴다(self):
        rows = [
            row("도시", network="도시대기"),
            row("교외", network="교외대기"),
            row("도로", network="도로변대기"),
            row("항만", network="항만"),
            row("섬", network="국가배경농도(도서)"),
        ]
        assert [s[0] for s in parse_stations(payload(rows))] == ["교외", "도시"]

    def test_PM10_PM25_를_둘_다_재는_곳만(self):
        rows = [row("둘다"), row("PM10만", items="SO2, PM10"), row("PM25만", items="PM2.5")]
        assert [s[0] for s in parse_stations(payload(rows))] == ["둘다"]

    def test_결과_코드가_다르면_멈춘다(self):
        with pytest.raises(BakeError):
            parse_stations(payload([row("가")], code="30"))

    def test_남은_곳이_없으면_멈춘다(self):
        with pytest.raises(BakeError):
            parse_stations(payload([row("도로", network="도로변대기")]))

    def test_좌표를_못_읽으면_멈춘다(self):
        broken = row("가")
        broken["dmX"] = ""
        with pytest.raises(BakeError):
            parse_stations(payload([broken]))


class TestRender:
    def test_구운_글은_파이썬으로_읽히고_좌표가_없다(self):
        text = render([("가", 98, 76), ("나", 60, 127)])
        namespace: dict = {}
        exec(compile(text, "stations_data.py", "exec"), namespace)
        assert namespace["STATIONS"] == (("가", 98, 76), ("나", 60, 127))
        assert "35.0" not in text and "129.0" not in text

    def test_바뀐_측정소를_알려_준다(self):
        before = (("가", 1, 1), ("나", 2, 2), ("다", 3, 3))
        after = [("가", 1, 1), ("다", 4, 4), ("라", 5, 5)]
        assert changes(before, after) == ["+ 라", "- 나", "~ 다"]
