"""박물관 · 과학관 · 체험관(`experience_center`) — TourAPI 시 · 도 목록에서 아이와 갈 곳을 고른다.

적재한 `place` 표에는 이 종류가 없다. TourAPI 어댑터(`CultureSource.sites`)가 시 · 도 이름만 받아 그
시 · 도의 문화시설 · 관광지를 넘기고, 여기서 이름으로 거르고 거리를 계산한다 — 보호자 좌표는 밖으로
나가지 않는다(행사 조회와 같은 구조, `tools/outing.search_events`).

- 시 · 도는 기준점에서 가장 가까운 시군구 대표 지점(`areas_data.DISTRICTS`)의 시 · 도다.
- 아이와 갈 곳은 이름 규칙으로 고른다(`kid_site`) — 모델에게 묻지 않는다. 기념관 · 대학 박물관 ·
  성보박물관 · 공연장은 뺀다.
- 반경은 `CULTURE_MAX_RADIUS_M` 이다 — 차로 가는 나들이라 공원 · 놀이터(20km)보다 넓다.
"""

import math
import re

from app.agents.activity.areas_data import DISTRICTS
from app.agents.activity.events import EARTH_RADIUS_M
from app.agents.activity.schemas.common import PlaceCategory
from app.agents.activity.store.ports import CoarseLocation, CultureSource, PlaceRow, PlaceSource
from app.core.culture_raw import RawSite
from app.core.weather_raw import UpstreamUnavailable

# 잠정값 — eval 로 조정한다
CULTURE_MAX_RADIUS_M = 30_000
CULTURE_SOURCE = "tour_culture"

# 아이와 갈 만한 곳의 이름. 11-09 부산 · 경남 실측 목록으로 골랐다. "천문" 만 두면 사천문화원 ·
# 금천문화원이 걸리고, "키즈" 는 키즈카페(`indoor_playground`)와 겹쳐서 넣지 않는다 (#858 리뷰)
_KID = re.compile(
    r"박물관|과학관|미술관|체험관|체험센터|어린이|뮤지엄|천문대|천문관|천문과학|우주|아쿠아리움|수족관|"
    r"생태|자연사|공룡|곤충"
)
# 이름에 위 말이 있어도 뺀다 — 대학 · 절 박물관, 인물 기념관, 홍보관, 공연장, 서원
_NOT_KID = re.compile(
    r"대학교|대학|성보|기념관|기념사업|홍보|갤러리|화랑|아트홀|극장|씨어터|공연장|서원|향교|사찰|교회|성당|"
    r"기독교|선교|불교|천주교|순교"
)
# 바깥 장소의 이름 — 이 종류는 실내(`INDOOR_CATEGORIES`)라 비 오는 날에도 나간다. 생태공원 ·
# 어린이대공원 · 공룡 발자국 화석지가 "생태 · 어린이 · 공룡" 에 걸렸다 (#858 리뷰). 건물 이름으로
# 끝나면(생태숲 생태체험관) 받는다
_OUTDOOR = re.compile(r"공원|탐방|습지|둘레길|수목원|숲|늪|화석|마을|캠핑|해변|해수욕장")
_BUILDING = re.compile(r"(관|센터|뮤지엄)(\s|\(|$)")
# 관광지(12) 행은 분류 코드(lclsSystm2)가 실내 체험 · 전시 계열일 때만 받는다 — 자연(NA) ·
# 공원(VE03) · 농산촌 체험(EX03)은 바깥이다. 문화시설(14)은 전부 건물이라 이름만 본다.
# VE02 는 아쿠아리움 · 천문대와 어린이대공원이 섞여 있어 이름(`_OUTDOOR`)이 가른다 (11-10 실측)
_SPOT = "12"
_INDOOR_SPOT_CODES = frozenset({"EX02", "EX06", "EX07", "VE02", "VE07"})


def kid_site(title: str) -> bool:
    """아이와 갈 만한 실내 박물관 · 과학관 · 체험관 이름인가. 이름만 본다."""
    if not _KID.search(title) or _NOT_KID.search(title):
        return False
    return not _OUTDOOR.search(title) or bool(_BUILDING.search(title))


def kid_place(site: RawSite) -> bool:
    """TourAPI 한 행이 아이와 갈 만한 실내 체험 · 전시 시설인가 — 관광지는 분류 코드도 본다."""
    if site.content_type == _SPOT and site.category not in _INDOOR_SPOT_CODES:
        return False
    return kid_site(site.title)


def province_of(location: CoarseLocation) -> str:
    """기준점에서 가장 가까운 시군구 대표 지점의 시 · 도. TourAPI 시 · 도 이름과 같은 글자다."""
    province, *_ = min(DISTRICTS, key=lambda row: _meters(location, row[2], row[3]))
    return province


class CulturePlaces:
    """`experience_center` 만 TourAPI 로, 나머지 종류는 적재한 장소 어댑터로 찾는다."""

    def __init__(self, base: PlaceSource | None, culture: CultureSource) -> None:
        self._base = base
        self._culture = culture

    async def nearby(
        self, *, location: CoarseLocation, category: PlaceCategory, radius_m: int
    ) -> list[PlaceRow]:
        if category != PlaceCategory.EXPERIENCE_CENTER:
            if self._base is None:
                raise UpstreamUnavailable("장소 어댑터가 없다")
            return await self._base.nearby(location=location, category=category, radius_m=radius_m)
        sites = await self._culture.sites(province=province_of(location))
        rows = [row for site in sites if (row := _row(site, location)) is not None]
        rows = [row for row in rows if row.distance_m <= CULTURE_MAX_RADIUS_M]
        return sorted(rows, key=lambda row: row.distance_m)


def with_culture(base: PlaceSource | None, culture: CultureSource | None) -> PlaceSource | None:
    """문화시설 어댑터가 있으면 장소 어댑터를 감싼다. 없으면 그대로다 — 체험관은 빈 목록이다."""
    if culture is None:
        return base
    return CulturePlaces(base, culture)


def _row(site: RawSite, origin: CoarseLocation) -> PlaceRow | None:
    if not kid_place(site):
        return None
    try:
        lon, lat = float(site.mapx), float(site.mapy)
    except ValueError:
        return None
    return PlaceRow(
        name=site.title.strip(),
        category=PlaceCategory.EXPERIENCE_CENTER,
        distance_m=_meters(origin, lat, lon),
        source=CULTURE_SOURCE,
        data_date=None,
    )


def _meters(origin: CoarseLocation, lat: float, lon: float) -> int:
    lat1, lat2 = math.radians(origin.lat), math.radians(lat)
    dlat = lat2 - lat1
    dlon = math.radians(lon - origin.lon)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return round(2 * EARTH_RADIUS_M * math.asin(math.sqrt(a)))
