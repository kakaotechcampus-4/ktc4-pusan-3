"""양 텍스트 → 섭취 계수. 정본은 `영양소_계산_설계.md` §4, 값은 `reference/amount_terms.yaml`.

사전에 없는 말은 지어내지 않고 "기록 없음"(`default`)으로 본다 — 양을 모르는 쪽이라
부족 · 과잉 판정에 절반만 들어간다(`app/rules/nutrition.amount_weight`).
"""

from dataclasses import dataclass
from functools import cache

from app.agents.common.reference import load_reference
from app.rules.nutrition import KdriGroup
from app.rules.term_match import normalize

SEED_FILE = "amount_terms.yaml"
PORTION_SCALE_FILE = "portion_scale.yaml"


@dataclass(frozen=True)
class AmountRule:
    factor: float
    known: bool


@cache
def _table() -> tuple[AmountRule, dict[str, AmountRule]]:
    data = load_reference(SEED_FILE)
    raw_default = data["default"]
    default = AmountRule(factor=float(raw_default["factor"]), known=bool(raw_default["known"]))
    by_alias: dict[str, AmountRule] = {}
    for raw in data["rules"]:
        rule = AmountRule(factor=float(raw["factor"]), known=bool(raw["known"]))
        for alias in raw["aliases"]:
            key = normalize(alias)
            if key in by_alias:
                raise ValueError(f"{SEED_FILE} 에 양 별칭이 겹친다: {alias!r}")
            by_alias[key] = rule
    return default, by_alias


def amount_of(text: str | None) -> AmountRule:
    """양 텍스트 하나를 계수로. 비었거나 사전에 없으면 기본값(양을 모르는 한 그릇)."""
    default, by_alias = _table()
    if not text:
        return default
    return by_alias.get(normalize(text), default)


@cache
def _scales() -> dict[str, float]:
    data = load_reference(PORTION_SCALE_FILE)
    return {group: float(value) for group, value in data["scales"].items()}


def portion_scale_for(group: KdriGroup) -> float:
    """성인 1인분에 곱할 유아 보정 계수. 구간 판정에만 쓰고 절대량으로 내보내지 않는다 (설계 §4)."""
    scales = _scales()
    if group not in scales:
        raise ValueError(f"{PORTION_SCALE_FILE} 에 {group!r} 계수가 없다")
    return scales[group]
