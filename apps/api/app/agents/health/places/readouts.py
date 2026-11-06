"""병원 찾기 결과 → 화면 readout. 문구는 `places.readout.yaml`, 줄 모양은 여기서 코드로 만든다."""

from pathlib import Path

from app.agents.common.readout import Readout, ReadoutCatalog
from app.agents.health.places.ports import FacilityTier, NearbyPlace, PlaceLookup

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("places.readout.yaml"))

# 진료과목에 소아청소년과가 있는 기관이다 — 소아청소년과 의원만이 아니라 내과 · 이비인후과도
# 들어온다(API 에 진료과 칸이 없다). 그래서 "소아청소년과" 가 아니라 "소아청소년과 진료" 라고 쓴다.
# 낮 · 밤 · 토요일 오전이 같은 목록이라 라벨도 같다. 밤에는 "지금 진료 중" 이 붙는다 (#517 리뷰)
_TIER_LABEL = {
    FacilityTier.PEDIATRIC_CLINIC: "소아청소년과 진료",
    FacilityTier.NIGHT_CLINIC: "소아청소년과 진료",
    FacilityTier.EMERGENCY_ROOM: "응급실",
}

# 목록 머리에 쓰는 말 — 처음 나온 종류로 고른다. 밤에 문 연 곳이 없으면 응급실만 남는다
_WHAT = {
    FacilityTier.PEDIATRIC_CLINIC: "소아청소년과 진료 병원",
    FacilityTier.NIGHT_CLINIC: "소아청소년과 진료 병원",
    FacilityTier.EMERGENCY_ROOM: "응급실",
}


def place_readout(lookup: PlaceLookup) -> Readout:
    """상태마다 문구 하나. 목록은 한 줄에 한 곳 — 이름 · 종류 · 거리 · 운영 여부 · 전화."""
    if lookup.status == "no_location":
        return READOUTS.render("place.no_location")
    if lookup.status == "unavailable":
        return READOUTS.render("place.unavailable")
    if not lookup.places:
        return READOUTS.render("place.empty")
    lines = "\n".join(_line(place) for place in lookup.places)
    return READOUTS.render("place.list", what=_WHAT[lookup.places[0].tier], lines=lines)


def _line(place: NearbyPlace) -> str:
    parts = [place.name, _TIER_LABEL[place.tier], _distance(place.distance_m)]
    if place.open_now is True:
        parts.append("지금 진료 중")
    if place.phone:
        parts.append(place.phone)
    return " · ".join(parts)


def _distance(meters: int) -> str:
    if meters < 1000:
        return f"{max(meters, 100)}m"
    return f"{meters / 1000:.1f}km"
