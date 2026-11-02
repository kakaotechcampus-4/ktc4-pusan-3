"""보호자가 말한 지역 이름 → 대표 지점. 구운 표(`areas_data.DISTRICTS`) 그대로 대조한다."""

from statistics import fmean

import pytest

from app.agents.activity.areas import (
    PROVINCE_ALIASES,
    find_area,
    find_province,
    named_area,
    said,
)
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

    @pytest.mark.parametrize(
        "text",
        [
            "해운대 가서 놀 곳",
            "해운대에서 놀 곳",
            "해운대구에서 놀 곳",  # 구 · 군 · 시를 붙여 말해도
            "해운대가서 놀래",
            "해운대, 기장 쪽",
            "토요일엔 해운대",
            "해운대나 기장",
            "해운대를 가서 놀 곳",  # 목적격 · 한정 조사 (#657 리뷰)
            "해운대구를 가서",
            "해운대만 가서 놀래",
            # 조사 둘이 겹치거나 쪽 · 근처 · 주변 뒤에 조사가 붙어도 (#657 리뷰 뒤 전수 확인)
            "해운대에는 뭐 있어",
            "해운대에서는 뭐 하지",
            "해운대에서만 하는",
            "해운대까지만",
            "해운대로는",
            "해운대쪽은",
            "해운대근처로 갈까",
            "해운대근처는",
            "해운대주변에서 놀 곳",
            "해운대부근",
            "해운대에서라도",
            "해운대요",
            "해운대인데 뭐 하지",
            "해운대에가서 놀래",
            "해운대갈건데",
        ],
    )
    def test_지역_이름_뒤에는_조사나_구_군_시가_온다(self, text):
        assert said("해운대", [text])

    @pytest.mark.parametrize(
        "area, text",
        [
            ("고양", "고양이와 아이가 놀 만한 곳"),  # #657 리뷰
            ("고양", "고양이랑 놀 곳"),
            ("고양", "고양이를 데리고 갈 곳"),  # "을 · 를" 을 받아도 tail 이 "이를" 이다
            ("고양", "고양이라서 놀 곳"),
            ("고양", "고양이나 강아지"),
            ("진구", "부산진구 가서 놀 곳"),  # 낱말 가운데서 시작하면 다른 곳이다
        ],
    )
    def test_다른_낱말의_일부는_지역이_아니다(self, area, text):
        assert not said(area, [text])

    @pytest.mark.parametrize(
        "text", ["해운대해수욕장 가서", "해운대역 근처", "해운대밖에 없어", "해운대놀러가"]
    )
    def test_뒤에_무엇이_붙어도_낱말_첫_글자면_말한_것이다(self, text):
        """조사 목록으로 따지지 않는다 — 지역 뜻인지는 모델이 고른다 (#657 리뷰)."""
        assert said("해운대", [text])

    def test_요청_조각을_이어_붙여_지역을_만들지_않는다(self):
        """조각마다 따로 본다 — 앞 조각 끝과 뒤 조각 앞이 이어져 지역이 되면 안 된다 (#657 리뷰)."""
        assert not said("해운대", ["바닷가에서 해운", "대 공원 말고 다른 놀이"])
        assert said("해운대", ["바닷가에서 놀래", "해운대 공원 말고 다른 놀이"])


class TestNamedArea:
    """모델이 넘긴 글자 앞쪽에서 보호자가 말한 지역 이름을 찾는다 (#657 리뷰).

    모델은 "해운대해수욕장" · "기장읍" 처럼 보호자가 쓴 말을 통째로 넘긴다. 사전에 그 이름은 없어서
    앞에서부터 가장 긴 지역 이름을 찾고, 보호자 글에 있는 것만 받는다.
    """

    @pytest.mark.parametrize(
        ("area", "texts", "name"),
        [
            ("해운대", ["해운대 가서 놀 곳"], "해운대"),
            ("해운대해수욕장", ["해운대해수욕장 가서 놀 곳"], "해운대"),
            ("기장읍", ["기장읍에서 놀 곳"], "기장"),
            ("부산 해운대", ["부산 해운대 쪽 공원"], "부산해운대"),
            ("부산진구", ["부산진구 가서"], "부산진구"),
            # 모델이 구를 붙여도 보호자가 쓴 짧은 이름으로 받는다
            ("해운대구", ["해운대 가서 놀 곳"], "해운대"),
        ],
    )
    def test_보호자가_말한_지역_이름(self, area, texts, name):
        assert named_area(area, texts) == name

    @pytest.mark.parametrize(
        ("area", "texts"),
        [
            ("기장", ["해운대 가서 놀 곳"]),  # 보호자가 쓰지 않았다
            ("서면", ["서면 가서 놀 곳"]),  # 사전에 없는 이름
            ("고양", ["고양이와 놀 곳"]),
            ("", ["해운대"]),
        ],
    )
    def test_없으면_None(self, area, texts):
        assert named_area(area, texts) is None


class TestProvince:
    """시 · 도만 말한 요청 — 행사는 그 시 · 도 전체에서 찾는다(장소는 넓어서 찾지 않는다)."""

    @pytest.mark.parametrize("text", ["부산", "부산광역시", " 부산 시 "])
    def test_시도_이름이면_시도_전체다(self, text):
        area = find_province(text)
        assert area is not None and area.whole_province
        assert area.province == "부산광역시"
        busan = [(lat, lon) for p, _, lat, lon in DISTRICTS if p == "부산광역시"]
        assert (area.lat, area.lon) == (
            fmean(lat for lat, _ in busan),
            fmean(lon for _, lon in busan),
        )

    @pytest.mark.parametrize("text", ["해운대", "부산 해운대", "서면", ""])
    def test_시군구를_말했거나_모르는_이름이면_None(self, text):
        assert find_province(text) is None

    def test_시군구로_찾은_지역은_시도_전체가_아니다(self):
        assert find_area("해운대").whole_province is False

    def test_모든_시도에_대표_지점이_있다(self):
        for province in PROVINCE_ALIASES:
            assert find_province(province) is not None
