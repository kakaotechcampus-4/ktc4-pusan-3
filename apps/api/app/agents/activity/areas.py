"""보호자가 말한 지역 이름 → 그 지역의 대표 지점. 장소와 날씨를 그 지점 기준으로 본다.

"해운대 가서 놀 곳" 처럼 보호자가 지역을 말하면, 모델이 그 이름을 `search_nearby_places` 의
`area` 로 넘기고 여기서 코드가 시군구로 바꾼다. 모델은 지역을 고르지 않는다 — 이름을 옮겨 적을
뿐이고, 보호자가 쓴 글자 안에 없으면 받지 않는다(`said`).

대표 지점은 기상청 격자 · 위경도 표의 시군구 행이다(대개 시군구청 자리, `areas_data.py`).
장소는 이 지점에서 가까운 순으로 찾아서, 시군구 경계 너머의 곳이 섞일 수 있다 — 그래서 이유
문장에 "근처" 를 쓰지 못하게 할 뿐 "해운대구 안" 이라고 단정하지 않는다.

이름 맞추기 (순서대로)
  1. 띄어쓰기를 지운다. 시도 이름(`PROVINCE_ALIASES`)으로 시작하면 떼고 그 시도 안에서 찾는다.
     떼고 못 찾으면 통째로 다시 찾는다 — "부산진구" 는 부산 + 진구가 아니다
  2. 구 · 군 · 시를 붙여도 떼도 찾는다("해운대구" · "해운대"). 뗀 이름이 한 글자면 받지 않는다
     ("중구" → "중")
  3. 구가 있는 시(수원시장안구)는 구 이름("장안구" · "장안")으로도, 시 이름("수원")으로도 찾는다.
     시 이름이면 그 구들의 대표 지점 가운데다
  4. 여러 곳이면(중구 · 고성) 보호자 위치에서 가장 가까운 곳이다. 위치가 없으면 찾지 않는다
  5. 시도만 말하면(서울 · 부산) 넓어서 찾지 않는다. 시군구가 하나뿐인 시도(세종)는 그곳이고,
     시도 안에 같은 이름의 시가 있으면(제주 → 제주시) 그 시다

🚨 못 찾으면 None 이다. 비슷한 곳으로 짐작하지 않는다 — tool 이 "장소 없이" 로 가고 화면에 한 줄
   안내가 붙는다. 옛 이름(인천 중구)은 표를 새로 구울 때 따라가지 않으면 못 찾는다.
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from statistics import fmean

from app.agents.activity.areas_data import DISTRICTS
from app.agents.activity.store.ports import CoarseLocation

# 격자 표의 시도 이름 → 보호자가 부르는 이름. 표에 없는 시도가 나오면 굽기가 멈춘다
# (`scripts/bake_activity_areas.py`). 옛 광주광역시는 2026 년 전남과 통합됐다 — "광주시" 는 경기
# 광주시와 겹쳐서 시도로도 보고, 둘이 갈리지 않으면 찾지 않는다
PROVINCE_ALIASES: dict[str, tuple[str, ...]] = {
    "서울특별시": ("서울", "서울시"),
    "부산광역시": ("부산", "부산시"),
    "대구광역시": ("대구", "대구시"),
    "인천광역시": ("인천", "인천시"),
    "대전광역시": ("대전", "대전시"),
    "울산광역시": ("울산", "울산시"),
    "세종특별자치시": ("세종", "세종시"),
    "경기도": ("경기",),
    "강원특별자치도": ("강원", "강원도"),
    "충청북도": ("충북",),
    "충청남도": ("충남",),
    "전북특별자치도": ("전북", "전라북도"),
    "전남광주통합특별시": ("전남광주", "전남", "전라남도", "광주", "광주시", "광주광역시"),
    "경상북도": ("경북",),
    "경상남도": ("경남",),
    "제주특별자치도": ("제주", "제주도"),
}

# 뗄 수 있는 끝 글자
_SUFFIXES = ("구", "군", "시")
# 구가 있는 시의 구 — "수원시장안구" → 수원시 · 장안구
_CITY_GU = re.compile(r"^(.+시)(.+구)$")


@dataclass(frozen=True)
class Area:
    """찾은 지역. `name` 은 테스트 · 문서용이다 — 모델 · 로그에 싣지 않는다."""

    name: str
    province: str
    lat: float
    lon: float

    @property
    def location(self) -> CoarseLocation:
        """장소 · 날씨 조회에 넘길 좌표. 보호자 위치와 같은 정밀도로 흐린다."""
        return CoarseLocation(lat=self.lat, lon=self.lon)


def find_area(text: str, *, near: CoarseLocation | None = None) -> Area | None:
    """지역 이름을 대표 지점으로. 못 찾거나 여러 곳 중 못 고르면 None."""
    key = re.sub(r"\s+", "", text)
    if not key:
        return None
    index = _index()
    for alias, province in _aliases():
        if not key.startswith(alias):
            continue
        rest = key[len(alias) :]
        if not rest:
            return _province_only(key, province)
        found = [area for area in index.get(rest, ()) if area.province == province]
        if found:
            return _pick(found, near)
        break  # 시도로 떼서 못 찾으면 통째로 본다
    return _pick(index.get(key, ()), near)


def said(area: str, request_texts: Iterable[str]) -> bool:
    """보호자가 쓴 글자 안에 그 이름이 있는가. 띄어쓰기는 보지 않는다 — 모델이 지어낸 지역으로
    찾지 않게 한다."""
    key = re.sub(r"\s+", "", area)
    return bool(key) and key in re.sub(r"\s+", "", "".join(request_texts))


def _province_only(key: str, province: str) -> Area | None:
    in_province = [area for area in _index().get(key, ()) if area.province == province]
    if len(in_province) == 1:
        return in_province[0]  # 제주 → 제주시
    districts = _districts_of(province)
    return districts[0] if len(districts) == 1 else None  # 세종은 그곳, 서울은 넓다


def _pick(found: Iterable[Area], near: CoarseLocation | None) -> Area | None:
    areas = list(found)
    if len(areas) == 1:
        return areas[0]
    if not areas or near is None:
        return None
    return min(areas, key=lambda area: _distance(area, near))


def _distance(area: Area, near: CoarseLocation) -> float:
    # 고르기만 하면 돼서 평면 근사다
    dlat = area.lat - near.lat
    dlon = (area.lon - near.lon) * math.cos(math.radians(near.lat))
    return dlat * dlat + dlon * dlon


def _stems(name: str) -> tuple[str, ...]:
    if name.endswith(_SUFFIXES) and len(name) > 2:
        return (name, name[:-1])
    return (name,)


@cache
def _aliases() -> tuple[tuple[str, str], ...]:
    """(별칭, 시도) — 긴 것부터. "광주광역시" 를 "광주" 보다 먼저 본다."""
    pairs = [(name, name) for name in PROVINCE_ALIASES]
    pairs += [(alias, name) for name, aliases in PROVINCE_ALIASES.items() for alias in aliases]
    return tuple(sorted(pairs, key=lambda pair: -len(pair[0])))


@cache
def _districts_of(province: str) -> tuple[Area, ...]:
    return tuple(
        Area(f"{p} {district}", p, lat, lon) for p, district, lat, lon in DISTRICTS if p == province
    )


@cache
def _index() -> dict[str, tuple[Area, ...]]:
    """부르는 이름 → 지역들. 시도는 여기 없다 — `find_area` 가 먼저 뗀다."""
    index: dict[str, dict[str, Area]] = {}
    cities: dict[tuple[str, str], list[Area]] = {}

    def add(names: Iterable[str], area: Area) -> None:
        for name in names:
            index.setdefault(name, {})[area.name] = area

    for province, district, lat, lon in DISTRICTS:
        area = Area(f"{province} {district}", province, lat, lon)
        city_gu = _CITY_GU.match(district)
        if city_gu is None:
            add(_stems(district), area)
            continue
        city, gu = city_gu.groups()
        add((district, f"{city[:-1]}{gu}", *_stems(gu)), area)  # 수원장안구 · 장안구 · 장안
        cities.setdefault((province, city), []).append(area)

    for (province, city), gu_areas in cities.items():
        center = Area(
            f"{province} {city}",
            province,
            fmean(area.lat for area in gu_areas),
            fmean(area.lon for area in gu_areas),
        )
        add(_stems(city), center)
    return {name: tuple(areas.values()) for name, areas in index.items()}
