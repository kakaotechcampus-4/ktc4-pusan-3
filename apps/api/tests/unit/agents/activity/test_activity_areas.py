"""보호자가 말한 지역 이름 → 대표 지점. 구운 표(`areas_data.DISTRICTS`) 그대로 대조한다."""

from statistics import fmean

import pytest

from app.agents.activity.areas import find_area, said
from app.agents.activity.areas_data import DISTRICTS
from app.agents.activity.store.ports import CoarseLocation

# 대전 시청 부근. 보호자 위치 대신 쓰는 테스트 좌표다
DAEJEON = CoarseLocation(lat=36.35, lon=127.38)


def name(text, near=None):
    area = find_area(text, near=near)
    return area.name if area is not None else None


class TestFind:
    @pytest.mark.parametrize(
        "text",
        ["해운대구", "해운대", "부산 해운대구", "부산광역시 해운대구", "부산해운대", " 해운대구 "],
    )
    def test_구_이름은_붙여도_떼도_띄워도_찾는다(self, text):
        assert name(text) == "부산광역시 해운대구"

    def test_시도_이름으로_시작하는_구는_통째로_본다(self):
        # "부산" + "진구" 가 아니다
        assert name("부산진구") == "부산광역시 부산진구"

    def test_군도_찾는다(self):
        assert name("기장군") == name("기장") == "부산광역시 기장군"

    def test_여러_시도에_있는_이름은_위치가_없으면_찾지_않는다(self):
        assert name("중구") is None
        assert name("고성") is None

    def test_여러_시도에_있는_이름은_보호자_위치에서_가까운_곳(self):
        assert name("중구", near=DAEJEON) == "대전광역시 중구"

    def test_시도를_붙이면_가른다(self):
        assert name("대구 중구") == "대구광역시 중구"
        assert name("강원 고성") == name("강원도 고성군") == "강원특별자치도 고성군"

    def test_한곳뿐이면_멀어도_그곳(self):
        # 위치는 같은 이름이 여럿일 때만 쓴다 — 대전에 있어도 해운대는 해운대다
        assert name("해운대", near=DAEJEON) == "부산광역시 해운대구"

    def test_구가_있는_시는_구_대표_지점들의_가운데(self):
        area = find_area("수원")
        assert area is not None and area.name == "경기도 수원시"
        gu = [(lat, lon) for _, district, lat, lon in DISTRICTS if district.startswith("수원시")]
        assert len(gu) == 4
        assert (area.lat, area.lon) == (fmean(p[0] for p in gu), fmean(p[1] for p in gu))
        assert name("수원시") == "경기도 수원시"

    def test_구가_있는_시의_구는_구_이름으로도_찾는다(self):
        assert name("장안구") == name("수원 장안구") == "경기도 수원시장안구"
        assert name("분당") == "경기도 성남시분당구"

    def test_시도만_말하면_넓어서_찾지_않는다(self):
        assert name("서울") is None
        assert name("부산", near=DAEJEON) is None

    def test_시군구가_하나뿐인_시도는_그곳(self):
        assert name("세종") == name("세종시") == "세종특별자치시 세종특별자치시"

    def test_시도와_같은_이름의_시는_그_시도_안이면_그곳(self):
        assert name("제주") == "제주특별자치도 제주시"

    def test_옛_광주광역시와_경기_광주시는_가른다(self):
        assert name("광주") is None
        assert name("광주시") is None
        assert name("광주 동구") == name("광주광역시 동구") == "전남광주통합특별시 동구"
        assert name("경기 광주") == name("경기도 광주시") == "경기도 광주시"

    @pytest.mark.parametrize("text", ["", "  ", "서면", "인천 중구", "우리 동네", "해운대구청"])
    def test_모르는_이름은_찾지_않는다(self, text):
        # 인천 중구는 2026 개편으로 없어졌다(제물포구 · 영종구)
        assert name(text) is None

    def test_대표_지점은_위치로_흐린다(self):
        area = find_area("해운대")
        assert area is not None
        location = area.location
        assert (location.lat, location.lon) == (round(area.lat, 2), round(area.lon, 2))


class TestSaid:
    def test_보호자가_쓴_글자_안에_있어야_한다(self):
        assert said("해운대", ["이번 주말에 해운대 가서 놀 곳"])
        assert said("부산 해운대", ["부산해운대 쪽 공원"])  # 띄어쓰기는 보지 않는다

    def test_모델이_지어낸_지역은_아니다(self):
        assert not said("해운대구", ["해운대 가서 놀 곳"])
        assert not said("해운대", ["주말에 공원 가고 싶어"])
        assert not said("", ["해운대"])
