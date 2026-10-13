"""섭취기준(`nutrient_reference` 시드) 로더. 정본은 `영양소_계산_설계.md` §2 · §6,
값은 `reference/kdri_2025.yaml`.

어느 지표에 EAR · AMDR · 상한만을 쓰는지는 원문이 아니라 설계 §6 표(우리 판단)를 따른다 — 손으로
옮긴 숫자와 그 판단은 분리한다. `select_kdri_group`(`app.rules.nutrition`)이 월령을 연령군으로
바꾼 뒤 이 모듈이 그 연령군의 기준값을 돌려준다.
"""

from dataclasses import dataclass
from functools import cache
from typing import Literal

from app.agents.common.reference import load_reference
from app.rules.nutrition import KdriGroup

SEED_FILE = "kdri_2025.yaml"

RefKind = Literal["ear", "amdr", "upper"]


@dataclass(frozen=True)
class NutrientRef:
    kind: RefKind
    ear: float | None = None
    low: float | None = None
    high: float | None = None
    upper: float | None = None
    unit: str = ""


def _parse_ref(nutrient: str, raw: dict[str, object]) -> NutrientRef:
    kind = raw.get("kind")
    if kind not in ("ear", "amdr", "upper"):
        raise ValueError(f"{SEED_FILE} 의 {nutrient!r}: kind 는 ear · amdr · upper 중 하나다")

    def as_float(key: str) -> float | None:
        value = raw.get(key)
        return None if value is None else float(value)  # type: ignore[arg-type]

    return NutrientRef(
        kind=kind,
        ear=as_float("ear"),
        low=as_float("low"),
        high=as_float("high"),
        upper=as_float("upper"),
        unit=str(raw.get("unit") or ""),
    )


@cache
def _groups() -> dict[str, dict[str, NutrientRef]]:
    data = load_reference(SEED_FILE)
    groups: dict[str, dict[str, NutrientRef]] = {}
    for group, nutrients in data["groups"].items():
        groups[group] = {nutrient: _parse_ref(nutrient, raw) for nutrient, raw in nutrients.items()}
    return groups


def refs_for(group: KdriGroup) -> dict[str, NutrientRef]:
    """이 연령군의 영양소별 섭취기준. 모르는 연령군이면 `ValueError`."""
    groups = _groups()
    if group not in groups:
        raise ValueError(f"{SEED_FILE} 에 섭취기준 연령군 {group!r} 이 없다")
    return groups[group]
