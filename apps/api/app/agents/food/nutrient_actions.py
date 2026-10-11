"""영양 구간 → 식품군 안내와, 아이의 알레르기 식품 빼기. 정본은 `영양소_계산_설계.md` §6.

구간(`low` · `high`)이 나오면 어떤 식품군을 말할지는 이 표가 정한다. 모델은 표에서 나온 문장을
다듬을 뿐 항목을 더하지 않는다. 문제는 표가 아이를 모른다는 것이다 — 달걀 알레르기 아이도 단백질이
적게 나오면 "고기·생선·달걀·콩 반찬 늘리기" 를 받는다. 그래서 먹이는 안내의 식품을 메뉴 행처럼
만들어 `filter_food_safety` 에 **그대로** 통과시키고 남은 것만 안내한다. 알레르기 · 질환 판정을
여기서 새로 짜지 않는다 (묶음 이름 · 질환 · 한 글자 규칙이 모두 그쪽 사전을 따른다).

- 통과(`passed`)한 식품만 남긴다. 막혔거나(`blocked`) 확인 못 한(`unchecked`) 식품은 뺀다.
- 식품이 다 빠지면 안내 문구 없이 구간만 말한다 (`text=None`). 다른 급원 식품은 더하지 않는다.
- 줄이는 안내("국물·절임 줄이기")는 먹이라는 말이 아니라서 거르지 않는다.
- 문장에 빠진 식품이 남지 않았는지는 `mentions_removed` 가 본다. 표에 있는 식품의 이름과 표에 적은
  다른 이름만 본다 — `term_match` 를 문장 전체에 걸면 한 글자 별칭("게")이 "적게" 에 걸린다.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any

from app.agents.common.reference import load_reference
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.agents.food.tools.safety import filter_food_safety
from app.rules.age import Stage
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.nutrition import NutrientBand
from app.rules.term_match import normalize

_BANDS = ("low", "ok", "high")
_TOP_KEYS = frozenset({"source", "version", "fetched_at", "actions"})
_ACTION_KEYS = frozenset({"nutrient", "band", "foods", "suffix", "text"})
_FOOD_KEYS = frozenset({"name", "codes", "aliases"})
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)  # 메뉴 행 모양을 맞추려는 값. 판정에 쓰이지 않는다


@dataclass(frozen=True)
class ActionFood:
    name: str  # 화면에 나가는 이름
    codes: frozenset[int]  # 19종 코드 — filter_food_safety 가 아이의 알레르기와 대조한다
    aliases: tuple[str, ...] = ()  # 요약 문장에서 이 식품을 가리키는 다른 말


@dataclass(frozen=True)
class NutrientAction:
    nutrient: str
    band: NutrientBand
    foods: tuple[ActionFood, ...] = ()  # 먹이는 안내
    suffix: str = ""  # 식품 이름 뒤에 붙는 말
    text: str | None = None  # 줄이는 안내 — 고정 문구


@dataclass(frozen=True)
class ActionAdvice:
    """한 구간에 대한 안내. `text` 가 None 이면 식품 안내 없이 구간만 말한다."""

    nutrient: str
    band: NutrientBand
    text: str | None
    removed: tuple[str, ...] = ()  # 아이의 알레르기 · 질환 때문에 빠진 식품 이름 (표 순서)


@cache
def _load() -> tuple[NutrientAction, ...]:
    data = load_reference("nutrient_actions.yaml")
    _check_keys("nutrient_actions.yaml", data, _TOP_KEYS)
    actions: list[NutrientAction] = []
    seen: set[tuple[str, str]] = set()
    for raw in data.get("actions") or []:
        _check_keys("nutrient_actions.yaml actions", raw, _ACTION_KEYS)
        nutrient, band = raw.get("nutrient"), raw.get("band")
        where = f"nutrient_actions.yaml {nutrient}/{band}"
        if not nutrient or band not in _BANDS:
            raise ValueError(f"{where}: nutrient · band(low/ok/high) 가 있어야 한다")
        if (nutrient, band) in seen:
            raise ValueError(f"{where}: 같은 항목 · 구간이 두 번 나온다")
        seen.add((nutrient, band))
        foods = tuple(_food(where, item) for item in raw.get("foods") or [])
        text = raw.get("text")
        if bool(foods) == bool(text):
            raise ValueError(
                f"{where}: foods(먹이는 안내) 와 text(줄이는 안내) 중 하나만 있어야 한다"
            )
        if text and not str(text).strip():
            raise ValueError(f"{where}: text 가 비었다")
        names = [food.name for food in foods]
        if len(set(names)) != len(names):
            raise ValueError(f"{where}: 식품 이름이 겹친다")
        actions.append(
            NutrientAction(
                nutrient=nutrient,
                band=band,
                foods=foods,
                suffix=str(raw.get("suffix") or ""),
                text=str(text) if text else None,
            )
        )
    return tuple(actions)


def _food(where: str, raw: dict[str, Any]) -> ActionFood:
    _check_keys(where, raw, _FOOD_KEYS)
    name = str(raw.get("name") or "").strip()
    codes = raw.get("codes")
    if not name or not codes:
        raise ValueError(
            f"{where}: 식품에 name · codes 가 있어야 한다 — 코드가 없으면 거를 수 없다"
        )
    if outside := [c for c in codes if isinstance(c, bool) or c not in ALLERGEN_NAMES]:
        raise ValueError(f"{where}: {name} 에 19종 밖 코드가 있다: {outside}")
    aliases = tuple(str(a) for a in raw.get("aliases") or ())
    if short := [a for a in aliases if len(normalize(a)) < 2]:
        raise ValueError(f"{where}: {name} 의 aliases 는 두 글자 이상이어야 한다: {short}")
    return ActionFood(name=name, codes=frozenset(codes), aliases=aliases)


def _check_keys(where: str, raw: Any, allowed: frozenset[str]) -> None:
    if extra := set(raw) - allowed:
        raise ValueError(f"{where} 에 정해지지 않은 칸이 있다: {sorted(extra)}")


# 표. 읽지 못하거나 모양이 틀리면 import 때 바로 실패한다 — 안내가 빈 채로 서버가 뜨지 않게
ACTIONS: tuple[NutrientAction, ...] = _load()


def advice_for(
    nutrient: str,
    band: NutrientBand,
    entries: Sequence[SafetyEntry],
    *,
    stage: Stage,
    months: int,
) -> ActionAdvice | None:
    """(항목, 구간) 의 안내. 표에 없는 조합이면 None — 모델이 항목을 더하지 않는다.

    `entries` 는 이 task 시작에 읽은 `health_safety`(`FoodRunState.safety`)다. 조회에 실패했으면
    이 함수를 부르지 않는다 — registry 가 영양소 분석을 `blocked.safety` 로 닫는다.
    """
    action = next((a for a in ACTIONS if a.nutrient == nutrient and a.band == band), None)
    if action is None:
        return None
    if not action.foods:
        return ActionAdvice(nutrient=nutrient, band=band, text=action.text)

    rows = [_as_row(food) for food in action.foods]
    verdict = filter_food_safety(rows, entries, stage, months=months)
    passed = {row.menu_key for row in verdict.passed}
    kept = [food.name for food in action.foods if _key(food) in passed]
    removed = tuple(food.name for food in action.foods if _key(food) not in passed)
    text = "·".join(kept) + action.suffix if kept else None
    return ActionAdvice(nutrient=nutrient, band=band, text=text, removed=removed)


def mentions_removed(text: str, removed: Iterable[str]) -> tuple[str, ...]:
    """문장에 빠진 식품이 남아 있는 것. 그 식품의 이름과 표에 적은 다른 이름을 본다.

    공백 · 문장부호는 무시한다("달 걀"). 표에 없는 식품은 보지 않는다 — 요약 전체에 알레르기 사전을
    거는 일은 한 글자 별칭 때문에 따로 정해야 한다.
    """
    haystack = normalize(text)
    found: list[str] = []
    for name in removed:
        needles = {normalize(name), *(normalize(a) for a in _aliases_of(name))}
        if any(needle and needle in haystack for needle in needles):
            found.append(name)
    return tuple(found)


def _aliases_of(name: str) -> tuple[str, ...]:
    return tuple(
        alias
        for action in ACTIONS
        for food in action.foods
        if food.name == name
        for alias in food.aliases
    )


def _key(food: ActionFood) -> str:
    return f"action:{food.name}"


def _as_row(food: ActionFood) -> MenuCatalogRow:
    """표의 식품을 메뉴 행처럼. 이름 · 재료 · 코드가 다 있어서 `unchecked` 가 되지 않는다."""
    return MenuCatalogRow(
        menu_key=_key(food),
        display_name=food.name,
        source="manual",
        resolved=True,
        synced_at=_EPOCH,
        ingredients=(food.name,),
        allergen_codes=food.codes,
    )
