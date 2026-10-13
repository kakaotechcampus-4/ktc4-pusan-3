"""격자 → 시군구 · 특보구역 굽기. 엑셀 대신 행을 직접 넣는다 (openpyxl 없이 돈다)."""

import pytest

from scripts.bake_holidays import BakeError
from scripts.bake_weather_areas import OPEN_END, build, parse_regions, parse_zones, render

ZONE_ROWS = [
    ("# REG_ID", "TM_ST", "TM_ED", "REG_SP", "REG_UP", "REG_KO", "REG_NAME"),
    ("L1000000", 200507010000, OPEN_END, 1, 0, "전국", "전국"),
    ("L1010000", 200507010000, OPEN_END, 2, "L1000000", "경기도", "경기도"),
    ("L1011900", 200507010000, OPEN_END, 13, "L1010000", "수원", "수원시"),
    ("L1013700", 202605311330, OPEN_END, 3, "L1010000", "파주", "파주시"),
    ("L1013710", 202605311330, OPEN_END, 14, "L1013700", "파주동북부", "파주시동북부"),
    ("L1013720", 202605311330, OPEN_END, 14, "L1013700", "파주서북부", "파주시서북부"),
    ("L1013000", 200507010000, 202605311330, 13, "L1010000", "파주", "파주시"),  # 끝난 구역
    ("L1150000", 200507010000, OPEN_END, 102, "L1000000", "부산", "부산광역시"),
    ("L1082500", 202405301100, OPEN_END, 113, "L1150000", "부산동부", "부산동부"),
    ("L1082700", 202405301100, OPEN_END, 113, "L1150000", "부산서부", "부산서부"),
    ("L1020000", 200507010000, OPEN_END, 2, "L1000000", "강원도", "강원도"),
    ("L1022200", 202605311330, OPEN_END, 103, "L1020000", "고성", "고성군"),
    ("L1080000", 200507010000, OPEN_END, 2, "L1000000", "경상남도", "경상남도"),
    ("L1082300", 200507010000, OPEN_END, 113, "L1080000", "고성", "고성군"),
]


def region(code, province, district, town="", nx=60, ny=120):
    return ("kor", code, province, district, town, str(nx), str(ny), "126", "58", "1")


REGION_ROWS = [
    ("구분", "행정구역코드", "1단계", "2단계", "3단계", "격자 X", "격자 Y"),
    region("4100000000", "경기도", ""),  # 시도 행은 굽지 않는다
    region("4111100000", "경기도", "수원시장안구"),
    region("4111151000", "경기도", "수원시장안구", "파장동", 60, 121),
    region("4148000000", "경기도", "파주시"),
    region("2635000000", "부산광역시", "해운대구", nx=99, ny=75),
    region("5182000000", "강원특별자치도", "고성군"),
    region("4882000000", "경상남도", "고성군"),
]


def baked():
    return build(parse_regions(REGION_ROWS), parse_zones(ZONE_ROWS))


class TestZones:
    def test_시군_이름으로_찾고_하위_구역과_도까지_넣는다(self):
        assert baked().by_district["4148000000"] == ("L1010000", "L1013700", "L1013710", "L1013720")

    def test_일반구는_시_이름으로_찾는다(self):
        assert baked().by_district["4111100000"] == ("L1010000", "L1011900")

    def test_같은_이름은_시도로_가른다(self):
        assert baked().by_district["5182000000"] == ("L1020000", "L1022200")
        assert baked().by_district["4882000000"] == ("L1080000", "L1082300")

    def test_구_단위_구역이_없으면_시도_묶음_전체(self):
        result = baked()
        assert result.by_district["2635000000"] == ("L1082500", "L1082700", "L1150000")
        assert result.whole_province == ["부산광역시 해운대구"]

    def test_끝난_구역은_쓰지_않는다(self):
        assert "L1013000" not in parse_zones(ZONE_ROWS)

    def test_모르는_시도면_멈춘다(self):
        rows = [*REGION_ROWS, region("9911000000", "새로운특별시", "어느구")]
        with pytest.raises(BakeError):
            build(parse_regions(rows), parse_zones(ZONE_ROWS))


class TestCells:
    def test_시군구와_격자가_같은_행은_하나다(self):
        cells = baked().cells
        assert ("4111100000", 60, 120) in cells and ("4111100000", 60, 121) in cells
        assert len([c for c in cells if c[0] == "4111100000"]) == 2

    def test_구운_글은_파이썬으로_읽힌다(self):
        result = baked()
        namespace: dict = {}
        exec(compile(render(result.cells, result.by_district), "areas_data.py", "exec"), namespace)
        assert namespace["DISTRICTS"] == tuple(result.cells)
        assert namespace["ZONES_BY_DISTRICT"] == result.by_district
