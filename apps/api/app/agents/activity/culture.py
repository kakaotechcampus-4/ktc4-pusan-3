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

# 아이와 갈 만한 곳의 이름. 11-09 부산 · 경남 실측 목록으로 골랐다
_KID = re.compile(
    r"박물관|과학관|미술관|체험관|체험센터|어린이|키즈|천문|우주|아쿠아리움|수족관|생태|자연사|공룡|곤충"
)
# 이름에 위 말이 있어도 뺀다 — 대학 · 절 박물관, 인물 기념관, 홍보관, 공연장, 서원
_NOT_KID = re.compile(
    r"대학교|대학|성보|기념관|기념사업|홍보|갤러리|화랑|아트홀|극장|씨어터|공연장|서원|향교|사찰|교회|성당|"
    r"기독교|선교|불교|천주교|순교"
)


def kid_site(title: str) -> bool:
    """아이와 갈 만한 박물관 · 과학관 · 체험관인가. 이름만 본다."""
    return bool(_KID.search(title)) and not _NOT_KID.search(title)


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
    if not kid_site(site.title):
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
