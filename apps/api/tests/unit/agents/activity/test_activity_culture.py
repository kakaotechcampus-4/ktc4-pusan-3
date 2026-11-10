"""박물관 · 과학관 · 체험관(`experience_center`) — TourAPI 시 · 도 목록에서 아이와 갈 곳을 고른다.

이름은 전부 가상이다. 좌표는 부산 근처 가상 값이다.
"""

import pytest

from app.agents.activity.culture import CULTURE_MAX_RADIUS_M, kid_place, kid_site, with_culture
from app.agents.activity.schemas.common import PlaceCategory
from app.agents.activity.store.inmemory import InMemoryPlaces
from app.agents.activity.store.ports import CoarseLocation, PlaceRow
from app.core.culture_raw import RawSite
from app.core.weather_raw import UpstreamUnavailable

BUSAN = CoarseLocation(lat=35.18, lon=129.08)
KM = 1 / 111.2  # 위도 1km


def site(title: str, *, km: float = 1.0, cid: str = "1") -> RawSite:
    return RawSite(
        content_id=cid,
        title=title,
        mapx=f"{BUSAN.lon}",
        mapy=f"{BUSAN.lat + km * KM:.6f}",
        content_type="14",
        category="VE07",
    )


class FakeCulture:
    def __init__(self, rows=(), *, fail=False) -> None:
        self.rows = list(rows)
        self.fail = fail
        self.asked: list[str] = []

    async def sites(self, *, province: str) -> list[RawSite]:
        self.asked.append(province)
        if self.fail:
            raise UpstreamUnavailable("down")
        return self.rows


PARK = PlaceRow(
    name="가상공원", category=PlaceCategory.PARK, distance_m=300, source="city_park", data_date=None
)


@pytest.mark.parametrize(
    ("title", "kid"),
    [
        ("가상과학관", True),
        ("가상해양박물관", True),
        ("가상시립미술관", True),
        ("가상안전체험관", True),
        ("가상아쿠아리움", True),
        ("가상천문대", True),
        ("가상곤충생태학습관", True),
        ("가상산생태숲생태체험관", True),  # 바깥 낱말이 있어도 건물 이름으로 끝나면 받는다
        ("가상 키즈뮤지엄", True),
        # 바깥 장소 — 이 종류는 실내라 비 오는 날에도 나간다 (#858 리뷰)
        ("가상어린이대공원", False),
        ("가상 어린이교통공원", False),
        ("가상생태공원", False),
        ("가상산 생태탐방로", False),
        ("가상만습지 생태체험", False),
        ("가상암군립공원(공룡발자국)", False),
        ("가상 공룡 발자국 화석산지", False),
        # "천문" 이 든 다른 말 · 키즈카페와 겹치는 이름
        ("가상천문화원", False),
        ("가상 키즈랜드", False),
        ("가상대학교 박물관", False),
        ("가상사 성보박물관", False),
        ("가상인물 기념관", False),
        ("가상 홍보전시관", False),
        ("가상 아트홀", False),
        ("가상서원", False),
        ("가상기독교선교박물관", False),
        ("가상불교박물관", False),
    ],
)
def test_아이와_갈_곳만_이름으로_고른다(title: str, kid: bool) -> None:
    assert kid_site(title) is kid


@pytest.mark.parametrize(
    ("content_type", "category", "kid"),
    [
        ("14", "VE07", True),  # 문화시설은 이름만 본다
        ("12", "EX07", True),  # 관광지 — 체험관
        ("12", "EX06", True),
        ("12", "VE02", True),  # 아쿠아리움 · 천문대 (어린이대공원은 이름이 가른다)
        ("12", "NA04", False),  # 자연 — 생태공원 · 탐방로
        ("12", "VE03", False),  # 공원
        ("12", "EX03", False),  # 농산촌 체험마을
    ],
)
def test_관광지는_실내_체험_전시_분류만_받는다(content_type: str, category: str, kid: bool) -> None:
    row = RawSite(
        content_id="1",
        title="가상곤충생태학습관",
        mapx="0",
        mapy="0",
        content_type=content_type,
        category=category,
    )
    assert kid_place(row) is kid


async def test_체험관은_그_시도_목록에서_가까운_순으로_찾는다() -> None:
    culture = FakeCulture(
        [
            site("가상과학관", km=5, cid="1"),
            site("가상박물관", km=2, cid="2"),
            site("가상인물 기념관", km=1, cid="3"),
            site("가상먼과학관", km=40, cid="4"),
        ]
    )
    places = with_culture(InMemoryPlaces([PARK]), culture)

    rows = await places.nearby(
        location=BUSAN, category=PlaceCategory.EXPERIENCE_CENTER, radius_m=20_000
    )

    assert culture.asked == ["부산광역시"]
    assert [row.name for row in rows] == ["가상박물관", "가상과학관"]
    assert {row.category for row in rows} == {PlaceCategory.EXPERIENCE_CENTER}
    assert {row.source for row in rows} == {"tour_culture"}
    assert CULTURE_MAX_RADIUS_M > 20_000  # 차로 가는 나들이라 공원보다 넓다


async def test_다른_종류는_적재한_장소에서_찾는다() -> None:
    culture = FakeCulture([site("가상과학관")])
    places = with_culture(InMemoryPlaces([PARK]), culture)

    rows = await places.nearby(location=BUSAN, category=PlaceCategory.PARK, radius_m=20_000)

    assert [row.name for row in rows] == ["가상공원"] and culture.asked == []


async def test_조회_실패는_그대로_실패다() -> None:
    places = with_culture(InMemoryPlaces([PARK]), FakeCulture(fail=True))
    with pytest.raises(UpstreamUnavailable):
        await places.nearby(
            location=BUSAN, category=PlaceCategory.EXPERIENCE_CENTER, radius_m=20_000
        )


def test_문화시설_어댑터가_없으면_그대로다() -> None:
    base = InMemoryPlaces([PARK])
    assert with_culture(base, None) is base


def test_TourAPI_어댑터가_문화시설_포트를_갖춘다() -> None:
    """entrypoint 가 행사 어댑터로 체험관도 찾는다 — 같은 TourAPI 키 · 클라이언트다."""
    from app.agents.activity.store.ports import CultureSource
    from app.integrations.tour.events import TourEvents

    assert issubclass(TourEvents, CultureSource)


def test_체험관_추천에는_관광정보_확인_문구가_붙는다() -> None:
    from app.agents.activity.review import place_notice

    row = PlaceRow(
        name="가상과학관",
        category=PlaceCategory.EXPERIENCE_CENTER,
        distance_m=1000,
        source="tour_culture",
        data_date=None,
    )
    assert place_notice(row).startswith("한국관광공사 관광정보에 있는 곳이에요.")


async def test_비_오는_날에도_체험관은_찾는다() -> None:
    from app.agents.activity.tools.outing import INDOOR_CATEGORIES

    assert PlaceCategory.EXPERIENCE_CENTER in INDOOR_CATEGORIES
