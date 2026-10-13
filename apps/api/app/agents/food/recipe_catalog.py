"""농식품정보원 레시피 → 레시피 카탈로그(`recipe_catalog`) 행. 재료는 사람이 검수한 정리 표로만
읽는다.

추천 후보 풀은 `recipe_catalog` 에서 재료가 있는 행만 쓴다(Food_Tool_명세 §0-5). 급식 · 기록 · 영양
판정용 `menu_catalog` 와는 뗐다 — 레시피 재료로 급식 메뉴를 판정하지 않는다(#326 리뷰,
food_agent_own_table §1-1). 행 모양은 `MenuCatalogRow` 를 같이 쓰고 `source` 로 가른다.

레시피 재료 원문은 그대로 쓸 수 없다 — 오타("돼기고기")는 사전이 못 읽어 돼지고기 알레르기
아이에게 통과하고, 부위명("안심" · "양지머리")은 어느 고기인지 사전이 모르고, 묶음 표기
("식용유/소금/참기름/잣가루")에는 잣이 숨어 있다. 그래서 재료 이름마다 정리한 이름과 19종 코드를
적은 정리 표(`reference/recipe_ingredients.yaml`)를 사람이 검수하고, 그 표로만 재료를 읽는다.

- 표의 `status` — `ok`(검수함) · `not_food`(음식이 아님 — 은박컵 · 랩) · `unknown`(검수했지만 구성을
  알 수 없음 — 육수 · 상표 양념) · `draft`(검수 전).
- 재료가 하나라도 표에 없거나 `ok` · `not_food` 가 아니면 그 레시피는 `resolved=false` 다. 후보 풀에
  들어가지 않고 알레르기 필터에서도 "확인 못 함" 이다(#321 완료 기준).
- 해석 못 한 레시피도 아는 재료 · 원문 이름은 남긴다 — 어디서 쓰이든 알레르기를 덜 보지 않게.
- `allergen_codes` 는 표의 코드와, 메뉴명 · 정리한 재료명을 사전으로 훑은 코드의 합집합이다
  (own_table §1). 제품마다 다른 성분(간장의 밀)은 표에 코드로 넣지 않는다 — 이름이 남아 있으면
  필터가 따로 본다.
- 식품군(`food_groups`)은 아직 채우지 않는다. `stage_min` 은 사람이 태깅한 값만 쓰고 없으면 비운다
  (비면 toddler 로 본다).
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any, Literal

from app.agents.common.reference import load_reference
from app.agents.food.store.ports import MenuCatalogRow, NutritionFacts
from app.agents.food.tools.safety import menu_codes
from app.core.mafra_recipe_raw import RawRecipe
from app.rules.age import Stage
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.menu_key import normalize_menu_key
from app.rules.term_match import normalize

Status = Literal["draft", "ok", "unknown", "not_food"]
_STATUSES: tuple[str, ...] = ("draft", "ok", "unknown", "not_food")
_SETTLED: frozenset[str] = frozenset({"ok", "not_food"})  # 이 둘만 정리된 것으로 센다
_TOP_KEYS = frozenset({"source", "version", "fetched_at", "ingredients"})
_ENTRY_KEYS = frozenset({"raw", "names", "codes", "status", "note", "rows"})
# 재료 앞의 섹션 머리 — "[쇠고기양념] 간장" 의 "[쇠고기양념]" 은 재료가 아니라 묶음 제목이다
_SECTION = re.compile(r"^\s*\[[^\]]*\]\s*")

SOURCE_VERSION = "mafra_recipe"


@dataclass(frozen=True)
class IngredientEntry:
    """정리 표 한 줄 — 원문 재료 이름 하나를 어떻게 읽을지."""

    raw: str  # 원문 이름 (사람이 읽는 표기)
    names: tuple[str, ...]  # 정리한 재료 이름. 묶음 표기는 여럿, 음식이 아니면 비어도 된다
    codes: frozenset[int]  # 이 재료에 확실히 든 19종 코드
    status: Status
    note: str = ""


def ingredient_key(raw: str) -> str:
    """정리 표를 찾는 키. 섹션 머리를 떼고 공백 · 문장부호를 뺀다. 괄호 속 글자는 남긴다 —
    "다짐육(돼지고기)" 의 괄호 속이 알레르기 정보다."""
    return normalize(_SECTION.sub("", unicodedata.normalize("NFKC", raw)))


def parse_ingredient_table(data: Mapping[str, Any]) -> dict[str, IngredientEntry]:
    """정리 표를 검사해 키별로 돌려준다. 모양이 틀리면 `ValueError` — 알레르기 판정 입력이다."""
    if missing := [key for key in ("source", "version", "fetched_at") if not data.get(key)]:
        raise ValueError(f"recipe_ingredients.yaml 에 필수 키가 없다: {missing}")
    if extra := set(data) - _TOP_KEYS:
        raise ValueError(f"recipe_ingredients.yaml 에 정해지지 않은 칸이 있다: {sorted(extra)}")

    table: dict[str, IngredientEntry] = {}
    for raw_entry in data.get("ingredients") or []:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("recipe_ingredients.yaml 의 항목은 객체여야 한다")
        if extra := set(raw_entry) - _ENTRY_KEYS:
            raise ValueError(f"recipe_ingredients.yaml 항목에 정해지지 않은 칸: {sorted(extra)}")
        raw = str(raw_entry.get("raw") or "").strip()
        key = ingredient_key(raw)
        where = f"recipe_ingredients.yaml {raw!r}"
        if not key:
            raise ValueError("recipe_ingredients.yaml 에 원문 이름이 빈 항목이 있다")
        status = raw_entry.get("status")
        if status not in _STATUSES:
            raise ValueError(f"{where}: status 는 {_STATUSES} 중 하나여야 한다")
        names = tuple(str(name).strip() for name in raw_entry.get("names") or ())
        if any(not name for name in names):
            raise ValueError(f"{where}: 빈 이름이 있다")
        if status == "ok" and not names:
            raise ValueError(f"{where}: ok 인데 정리한 이름이 없다")
        codes = list(raw_entry.get("codes") or ())
        if outside := [c for c in codes if isinstance(c, bool) or c not in ALLERGEN_NAMES]:
            raise ValueError(f"{where}: 19종 밖 코드가 있다: {outside}")
        if key in table:
            raise ValueError(f"{where}: 정리하면 {table[key].raw!r} 와 같은 이름이다")
        table[key] = IngredientEntry(
            raw=raw,
            names=names,
            codes=frozenset(codes),
            status=status,
            note=str(raw_entry.get("note") or ""),
        )
    return table


@cache
def ingredient_table() -> dict[str, IngredientEntry]:
    """`reference/recipe_ingredients.yaml`. 레시피 배치만 읽는다 — 요청 경로에서는 부르지 않는다."""
    return parse_ingredient_table(load_reference("recipe_ingredients.yaml"))


def build_recipe_row(
    recipe: RawRecipe,
    table: Mapping[str, IngredientEntry],
    *,
    now: datetime,
    nutrition: NutritionFacts | None = None,
    stage_min: Stage | None = None,
) -> MenuCatalogRow:
    """레시피 하나를 카탈로그 행으로. 재료가 다 정리됐을 때만 `resolved=True` 다.

    `nutrition` 은 레시피 이름이 영양DB 와 정확히 같을 때만 넘긴다(호출부가 `resolve_menu` 규칙으로
    찾는다). 없으면 영양을 비워 두고 영양 판정은 unresolved 로 센다.
    """
    menu_key = normalize_menu_key(recipe.name)
    if not menu_key:
        raise ValueError("레시피 이름이 정규화하면 비어 있다")

    ingredients: list[str] = []
    codes: set[int] = set()
    settled = bool(recipe.ingredients)

    def add(name: str) -> None:
        if name and name not in ingredients:
            ingredients.append(name)

    for item in recipe.ingredients:
        if item.name is None:
            settled = False  # 이름 없는 재료 — 무엇이 들었는지 모른다
            continue
        key = ingredient_key(item.name)
        found = table.get(key) if key else None
        if found is None:
            settled = False
            # 표에 없어도 원문은 남긴다 — 사전이 읽을 수 있는 글자면 필터가 막는다
            add(_SECTION.sub("", unicodedata.normalize("NFKC", item.name)).strip())
            continue
        if found.status not in _SETTLED:
            settled = False
        if found.status == "not_food":
            continue
        codes |= found.codes
        for name in found.names or (found.raw,):
            add(name)

    codes |= menu_codes((recipe.name, *ingredients))
    return MenuCatalogRow(
        menu_key=menu_key,
        display_name=recipe.name,
        source="mafra_recipe",
        resolved=settled,
        synced_at=now,
        food_code=nutrition.food_code if nutrition else None,
        serving_g=nutrition.serving_g if nutrition else None,
        nutrients=dict(nutrition.per_100g) if nutrition else {},
        ingredients=tuple(ingredients),
        allergen_codes=frozenset(codes),
        stage_min=stage_min,
        source_version=SOURCE_VERSION,
        source_id=recipe.recipe_id,
    )
