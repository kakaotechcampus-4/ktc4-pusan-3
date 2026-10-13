"""`reference/food_doc.yaml` — food_doc 시드 로더 (own_table §4 · RAG_plan Food 절).

YAML 이 원본이다. DB 가 생기면 Alembic 이 이 파일을 `doc_key` 기준으로 올리고(upsert),
그 전까지는 읽는 쪽(`FoodDocReader` 어댑터 — 후속 이슈)이 이 로더로 읽는다.

모양이 틀리면 읽을 때 바로 실패한다. 공통 칸(출처 · 월령 · 상태 · 작성자/검수자)은
`common/reference_docs.parse_doc_meta` 가, Food 칸(`food_groups` · `nutrients` · `texture` ·
`allergen_codes`)의 값은 여기서 본다. 내용 검사(알레르기 코드가 본문을 덮는지 · 연령 규칙 ·
평가 · 이정표 · 수치 · 처방 · row_type 별 칸과 월령 · 출처)는 테스트가 한다 —
`tests/unit/agents/food/test_food_doc_seed.py`.

🚨 `allergen_codes` 는 조회 뒤 `filter_food_safety` 가 거르는 근거다. 읽는 쪽은 적힌 코드만
믿지 않고 `allergen_codes_in` 으로 제목 · 본문을 다시 훑은 코드와 합집합을 쓴다 —
메뉴 행을 거를 때(`filter_food_safety` 2번)와 같은 이유다.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

from app.agents.common.reference import allergen_terms, food_safety_terms, load_reference
from app.agents.common.reference_docs import DocMeta, check_unique_doc_keys, parse_doc_meta
from app.agents.food.tools.safety import menu_codes
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.term_match import TermMatcher, prepare_fields

SEED_FILE = "food_doc.yaml"
KEY_PREFIX = "food."
ROW_TYPES = frozenset(
    {"weaning_stage", "weaning_ingredient", "meal_pattern", "nutrient_note", "feeding_tip"}
)
WEANING_TYPES = frozenset({"weaning_stage", "weaning_ingredient"})
TEXTURES = frozenset({"puree", "mashed", "minced", "soft_pieces", "regular"})
# 6개 식품군(잠정) (own_table §1 · §4).
# menu_catalog.food_groups 와 같은 값을 써야 해서 둘 곳은 리뷰에서 정한다
FOOD_GROUPS = frozenset(
    {"grains", "meat_fish_egg_legumes", "vegetables", "fruits", "dairy", "fats_sugars"}
)

_DOMAIN_KEYS = frozenset({"food_groups", "nutrients", "texture", "allergen_codes"})


@dataclass(frozen=True)
class FoodDocEntry:
    """시드 한 행. 공통 칸(`meta`) + Food 칸."""

    meta: DocMeta
    food_groups: tuple[str, ...]
    nutrients: tuple[str, ...]
    texture: str | None
    allergen_codes: frozenset[int]  # YAML 에 적힌 코드. 읽는 쪽은 allergen_codes_in 과 합친다


@cache
def food_doc_seed() -> tuple[FoodDocEntry, ...]:
    """시드 전체(상태 무관). 서비스는 `approved` 만 쓴다. 거르는 건 읽는 쪽이다."""
    return parse_food_doc(load_reference(SEED_FILE))


def parse_food_doc(data: Mapping[str, Any]) -> tuple[FoodDocEntry, ...]:
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{SEED_FILE} 에 rows 가 없다 (아직 행이 없으면 rows: [])")
    entries = tuple(_parse_entry(raw) for raw in rows)
    check_unique_doc_keys([entry.meta for entry in entries], file=SEED_FILE)
    return entries


def allergen_codes_in(entry: FoodDocEntry) -> frozenset[int]:
    """제목 · 본문을 Food 사전으로 훑어 나오는 19종 코드.

    확실히 든 음식(`menu_codes`)에 제품마다 다른 성분(간장 → 밀 · 대두)까지 더한다.
    문서 행은 확인 질문을 붙일 자리가 없어서 덜 막는 쪽을 두지 않는다.

    사전은 메뉴 이름을 읽으려고 만든 것이라 문장에 그대로 쓰면 문법이 음식으로 읽힌다.
    그래서 두 가지만 바꾼다. 공용 사전(guard)은 메뉴 필터와 같이 쓰므로 고치지 않는다.
    - 낱말마다 따로 본다 — 띄어쓰기를 넘어 붙지 않게("살균한 치즈" 의 한치)
    - `_PROSE_MASKS` 의 자리만 가린다 — 어미 · 동사 · 신체 부위처럼 음식이 아닌 것이 확실한 자리
    """
    words = [_mask(word) for text in (entry.meta.title, entry.meta.body) for word in text.split()]
    varies = _varies_matcher().match(prepare_fields(words))
    return menu_codes(words) | frozenset(int(key) for key in varies)


# 문장에서만 생기는 오탐. (가릴 자리, 이유). 가린 자리는 "x" 로 바꿔 다른 별칭과 이어 붙지
# 않게 한다. 새 행이 오탐에 걸리면 문장을 고치거나 여기에 이유와 함께 더한다.
# 음식 이름을 가리면 안 된다
_PROSE_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?=밀(?:어|려|리|고|폐|착))밀"), "밀다 · 밀폐 · 밀착 — 밀(곡물)이 아니다"),
    (re.compile(r"(?<=내)밀"), "내밀다"),
    (re.compile(r"콩(?=팥)"), "콩팥 — 신체 부위"),
    (re.compile(r"(?<=[집소신정])중(?=하)"), "집중하다 · 소중하다 … — 중하(새우)가 아니다"),
    (re.compile(r"^게(?=다가)"), "게다가 — 접속 부사"),
)
_WORD_EDGES = re.compile(r"^[^\w]+|[^\w]+$")


def _mask(word: str) -> str:
    core = _WORD_EDGES.sub("", word)
    for pattern, _ in _PROSE_MASKS:
        core = pattern.sub("x", core)
    # 어미 "-게"(보게 · 익숙해지게). 낱말 끝의 게만 본다 — 게살 · 꽃게를 은 남는다.
    # 꽃게 · 대게처럼 게 이름이면 가리지 않는다
    if len(core) > 1 and core.endswith("게") and not any(name in core for name in _crab_names()):
        core = core[:-1] + "x"
    return core


@cache
def _crab_names() -> frozenset[str]:
    """두 글자 이상의 게 이름(꽃게 · 대게 …). 어미 "-게" 를 가릴 때 빼 둔다."""
    return frozenset(
        alias
        for term in (*allergen_terms(), *food_safety_terms().code_terms)
        if term.key == str(_CRAB_CODE)
        for alias in term.aliases
        if len(alias) > 1 and alias.endswith("게")
    )


_CRAB_CODE = 8


@cache
def _varies_matcher() -> TermMatcher:
    return TermMatcher(food_safety_terms().varies_terms)


def _parse_entry(raw: Mapping[str, Any]) -> FoodDocEntry:
    meta = parse_doc_meta(
        raw, file=SEED_FILE, key_prefix=KEY_PREFIX, row_types=ROW_TYPES, domain_keys=_DOMAIN_KEYS
    )
    where = f"{SEED_FILE} 행 {meta.doc_key!r}"
    texture = raw.get("texture")
    if texture is not None and texture not in TEXTURES:
        raise ValueError(f"{where} 의 texture {texture!r} 는 정해진 값이 아니다")
    food_groups = _string_list(raw, "food_groups", where)
    if unknown := sorted(set(food_groups) - FOOD_GROUPS):
        raise ValueError(f"{where} 의 food_groups 에 정해지지 않은 값이 있다: {unknown}")
    return FoodDocEntry(
        meta=meta,
        food_groups=food_groups,
        nutrients=_string_list(raw, "nutrients", where),
        texture=texture,
        allergen_codes=_allergen_codes(raw, where),
    )


def _string_list(raw: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    values = raw.get(key, [])
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        raise ValueError(f"{where} 의 {key} 는 문자열 목록이어야 한다 (없으면 [])")
    return tuple(values)


def _allergen_codes(raw: Mapping[str, Any], where: str) -> frozenset[int]:
    values = raw.get("allergen_codes", [])
    if not isinstance(values, list) or not all(
        isinstance(v, int) and not isinstance(v, bool) and v in ALLERGEN_NAMES for v in values
    ):
        raise ValueError(f"{where} 의 allergen_codes 는 19종 코드(1~19) 목록이어야 한다")
    return frozenset(values)
