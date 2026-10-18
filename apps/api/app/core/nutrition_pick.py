"""영양DB 행을 고르고 영양성분으로 옮기는 규칙. 요청 중 조회(`resolve_menu`)와 새벽 배치(FT-19)가
같이 쓴다.

🚨 `app/agents/food/` 가 아니라 여기 있는 이유 — 배치는 `app/integrations/`(core 만 import) 나
`app/workers/`(agents import 금지)에 있어서 agents 의 함수를 부를 수 없다. 다르게 고르면 같은 메뉴가
배치 전후로 다른 영양값을 갖는다(own_table §1). `core/weather_raw.py` 와 같은 자리다.

같은 이름이 여러 행이다(된장찌개 12 · 김치찌개 25). 클라이언트는 원문 행(`RawNutriRow`)을
그대로 주고, 어느 행을 쓸지는 여기서 정한다. 알레르기와 영양을 붙이는 입력이라
**못 고르는 쪽으로 틀린다** — 못 고르면 그 메뉴는 unresolved 이고 알레르기는 "확인 못 함" 이다.

규칙 (`docs/agents/shared/외부연결_계획.md` §2)
  1. 이름이 정확히 같은 행만. 비슷한 이름으로 추정하지 않는다 — 미역국에 황태미역국을 붙이면
     쇠고기 · 조개류가 "없음" 이 된다.
  2. **품목대표만.** 상용제품(포장 제품)은 어느 그룹이든 메뉴명에 쓰지 않는다 — "백미밥" 은 즉석밥
     제품이고 칼슘 · 철이 빈 행도 제품 행이었다.
  3. 음식 → 원재료성 → 가공식품 순서. 우유는 원재료성 그룹에만, 백김치 · 동그랑땡은 가공식품
     그룹에만 있어서 세 그룹을 다 본다. 다른 그룹 이름이 오면 받지 않는다.
  4. 핵심 영양 7칸(에너지 · 단백질 · 지방 · 탄수화물 · 칼슘 · 철 · 나트륨)이 다 찬 행만. 빈 칸을
     0 으로 넣으면 합계가 실제보다 작아져 거짓 low 가 난다. 당류 · 포화지방은 있으면 싣고 비면
     뺀다 — 0 으로 채우지 않는다. 당류가 빈 메뉴가 섞인 판정은 당류를 판단하지 않고 안내만
     낸다(10-11 결정, 영양 계산이 맡는다).
     1인분 중량은 필수가 아니다 — 실제 응답에서 원재료성 품목대표는 12행 모두, 가공식품
     품목대표는 18행 중 17행이 비어 있다. 없으면 `serving_g=None`(모르는 양)이고 100g 으로
     가정하지 않는다.
  5. 같은 순위가 여럿이면 조사 일자가 최근인 행, 그다음 식품코드 순 — 입력 순서와 상관없이
     매번 같은 행이 나오게 한다. 출처(`source`)로 가르는 기준은 아직 없다(열린 질문).
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, OPTIONAL_NUTRIENT_KEYS, RawNutriRow
from app.rules.term_match import normalize

# 쓰는 그룹과 순서. 이 밖의 그룹은 받지 않는다
GROUP_ORDER: tuple[str, ...] = ("음식", "원재료성", "가공식품")
# 메뉴명에 쓰는 분류. 상용제품 등 그 밖의 값은 받지 않는다
REPRESENTATIVE_CLASS = "품목대표"

SOURCE_VERSION = "mfds_nutri_db"

# "300g" · "200ml" · "1,200 g". 마시는 것은 ml 로 오는데 1ml = 1g 으로 읽는다(물 · 우유 밀도 근사)
_GRAMS = re.compile(r"^\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:g|ml)?\s*$", re.IGNORECASE)
_DATE_DIGITS = 8  # YYYYMMDD. "2023-01" 은 20230100 으로 맞춰 견준다


@dataclass(frozen=True)
class NutritionFacts:
    """외부 영양성분 DB 조회 결과 한 건."""

    food_code: str
    name: str
    category: str  # 식품 대분류. 식품군 매핑에 쓴다
    serving_g: float | None  # 성인 1인분
    # energy · protein · fat · carbohydrate · sugars · sodium · calcium · iron · saturated_fat
    per_100g: dict[str, float]
    source_version: str
    # 원재료성 행은 대분류 이름이 비고 코드만 오기도 한다 — 식품군 대응표가 코드로 한 번 더 찾는다.
    # 코드 뜻이 DB 그룹마다 달라서 그룹을 같이 싣는다
    db_group: str = ""
    category_code: str | None = None


def parse_nutrient(raw: str | None) -> float | None:
    """영양값 원문 → 숫자. 읽을 수 없거나 음수면 None.

    "-" · "Tr" 은 측정 안 한 칸이지 0 이 아니다.
    """
    if raw is None:
        return None
    try:
        value = float(raw.strip().replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def parse_grams(raw: str | None) -> float | None:
    """1인분 중량 원문 → g. 읽을 수 없거나 0 이하면 None. g · ml 만 받는다(kg 등은 읽지 않는다)."""
    if raw is None:
        return None
    match = _GRAMS.match(raw)
    if match is None:
        return None
    value = float(match.group(1).replace(",", ""))
    return value if math.isfinite(value) and value > 0 else None


def pick_nutrition_row(rows: Iterable[RawNutriRow], menu_key: str) -> RawNutriRow | None:
    """이 메뉴에 쓸 영양DB 행 하나. 쓸 수 있는 행이 없으면 None."""
    if not menu_key:
        return None
    usable = [row for row in rows if _usable(row, menu_key)]
    return min(usable, key=_rank) if usable else None


def pick_nutrition(rows: Iterable[RawNutriRow], menu_key: str) -> NutritionFacts | None:
    """`pick_nutrition_row` 로 고른 행을 100g 기준 영양성분으로. 못 고르면 None."""
    row = pick_nutrition_row(rows, menu_key)
    if row is None:
        return None
    serving_g = parse_grams(row.food_weight)
    per_100g = {
        key: value
        for key in (*CORE_NUTRIENT_KEYS, *OPTIONAL_NUTRIENT_KEYS)
        if (value := parse_nutrient(row.nutrients.get(key))) is not None
    }
    return NutritionFacts(
        food_code=row.food_code.strip(),
        name=row.name,
        category=row.category or "",
        serving_g=serving_g,
        per_100g=per_100g,
        source_version=SOURCE_VERSION,
        db_group=row.db_group.strip(),
        category_code=row.category_code,
    )


def nutrition_facts_by_key(rows: Iterable[RawNutriRow]) -> dict[str, NutritionFacts]:
    """원문 행 목록 → `menu_key` 별 영양성분. 배치(FT-19)가 품목대표 전체를 한 번에 넘긴다.

    행 하나는 `normalize(row.name)` 키 하나에만 쓰인다(`_usable`). 그래서 그 값으로 묶으면
    요청 중 조회와 결과가 같다. 쓸 수 있는 행이 없는 이름은 키 자체가 없다.
    """
    grouped: dict[str, list[RawNutriRow]] = {}
    for row in rows:
        grouped.setdefault(normalize(row.name), []).append(row)
    picked = {key: pick_nutrition(group, key) for key, group in grouped.items() if key}
    return {key: facts for key, facts in picked.items() if facts is not None}


def _usable(row: RawNutriRow, menu_key: str) -> bool:
    # 이름은 영양DB 쪽 괄호를 지우지 않고 견준다 — "김치찌개(참치)" 는 다른 변형이다
    return (
        normalize(row.name) == menu_key
        and row.db_class.strip() == REPRESENTATIVE_CLASS
        and row.db_group.strip() in GROUP_ORDER
        and bool(row.food_code.strip())
        and all(parse_nutrient(row.nutrients.get(key)) is not None for key in CORE_NUTRIENT_KEYS)
    )


def _rank(row: RawNutriRow) -> tuple[int, int, str]:
    return (GROUP_ORDER.index(row.db_group.strip()), -_date_key(row.researched_on), row.food_code)


def _date_key(raw: str | None) -> int:
    digits = "".join(ch for ch in raw or "" if ch.isdigit())[:_DATE_DIGITS]
    return int(digits.ljust(_DATE_DIGITS, "0")) if digits else 0
