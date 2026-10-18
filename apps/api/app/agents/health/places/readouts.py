"""병원 찾기 결과 → 화면 readout. 문구는 `places.readout.yaml`, 줄 모양은 여기서 코드로 만든다."""

from pathlib import Path

from app.agents.common.readout import Readout, ReadoutCatalog
from app.agents.health.places.ports import FacilityTier, NearbyPlace, PlaceLookup

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("places.readout.yaml"))

_TIER_LABEL = {
    FacilityTier.PEDIATRIC_CLINIC: "소아청소년과",
    FacilityTier.MOONLIGHT: "달빛어린이병원",
    FacilityTier.EMERGENCY_ROOM: "응급실",
}

# 목록 머리에 쓰는 말 — 처음 나온 종류로 고른다. 야간에 달빛어린이병원이 없으면 응급실만 남는다
_WHAT = {
    FacilityTier.PEDIATRIC_CLINIC: "소아청소년과",
    FacilityTier.MOONLIGHT: "야간 · 휴일 진료 병원",
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
