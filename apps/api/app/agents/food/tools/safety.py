"""알레르기 · 연령 · 아이 금지 식품을 거르는 내부 safety tool.

코드가 모델 호출 전에 후보 풀에서 위험한 것을 빼고,
모델이 답을 낸 후에 다시 한 번 거른다. 급식 조회도 같은 함수를 쓴다.

호출부(SafetyReader.food_safety)는 `status='active'` 행만 돌려주지만 여기서 한 번 더 본다.
거르지 않는 것은 `retracted` · `none` 둘뿐이고, 그 밖의 값은 모르는 값이라도 거른다.
어댑터가 `none` 행을 섞어 보내면 그 행을 알레르기로 읽지 않고, status 를 잘못 옮겨도 필터가
조용히 꺼지지 않는다. unknown 은 저장하지 않는 값(행이 없는 것)이라 이 칸에 오면 어댑터 버그다.

규칙의 정본은 docs/agents/food/Food_Tool_명세.md §3 "음식 안전 수칙" 이다.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache

from app.agents.common.allergy import (
    LabelBook,
    NameRule,
    Restriction,
    make_book,
    read_label,
    shared_name_rules,
)
from app.agents.common.reference import (
    AgeRule,
    allergen_terms,
    chronic_restriction_codes,
    chronic_restriction_terms,
    food_safety_terms,
)
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.rules.age import Stage, first_month_of
from app.rules.allergen import ALLERGEN_NAMES, strip_allergen_marks
from app.rules.term_match import PreparedFields, Term, TermMatcher, normalize, prepare_fields

# 거르지 않는 상태. 이 밖의 값은 모르는 값이라도 거른다 — 어댑터가 status 를 잘못 옮겨도
# 필터가 조용히 꺼지지 않게(fail closed).
_INACTIVE_STATUSES = frozenset({"retracted", "none"})

# 연령 규칙의 exact 는 메뉴명에서 급식표의 알레르기 번호 · 괄호 · 양을 뗀 뒤 비교한다
# ("우유 2,5" · "우유(2)" · "우유(200ml)" · "우유 한 컵" 은 마시는 우유다)
_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_AMOUNT = re.compile(r"(?:\d+(?:\.\d+)?|한|두|반)\s*(?:ml|cc|l|g|컵|잔|팩|병|개)", re.IGNORECASE)


@dataclass(frozen=True)
class SafetyVerdict:
    """`filter_food_safety` 한 번 호출의 결과. 행마다 셋 중 하나로 나뉜다."""

    passed: tuple[MenuCatalogRow, ...]
    blocked: tuple[MenuCatalogRow, ...]
    unchecked: tuple[MenuCatalogRow, ...]  # 해석 실패거나 재료 모름
    hits: dict[str, tuple[str, ...]]  # menu_key → 걸린 key. 로그에 싣지 않음
    # menu_key → 주의 규칙 label. 막지 않고 안내만 붙인다(질식 주의 → guide.choking_caution).
    # 나뉜 묶음과 따로 본다 — passed 행에도 붙는다
    cautions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # menu_key → 그 메뉴에 든 19종 정식 명칭(ALLERGEN_NAMES, 코드 순). passed 행만 싣고, 든 것이
    # 없으면 키가 없다. 추천의 `suggestion.allergens` 를 채우는 값이다.
    # 승인 때 health_safety 와 대조해서 "이 음식에는 땅콩이 들어가요. 땅콩 알레르기가 있는지 확인해주세요" 를 띄운다.
    # 막지 않은 알레르기 성분(등록하지 않았거나 아직 답하지 않은 것)이 대상이라 passed 행에만 있다
    allergens: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class SafetyRules:
    """이 아이에게 막을 것. health_safety 행들을 읽은 결과다."""

    codes: frozenset[int]  # 19종 코드 — 메뉴 코드와 겹치면 막는다
    terms: tuple[Term, ...]  # 글자로 막을 것 — 19종 밖 알레르기 · 묶음의 19종 밖 식품 · 질환


def filter_food_safety(
    rows: Sequence[MenuCatalogRow],
    entries: Sequence[SafetyEntry],
    stage: Stage,
    *,
    months: int | None = None,
) -> SafetyVerdict:
    """행마다 판정 순서대로 거른다.

    1. health_safety 행을 `resolve_safety` 로 읽어 막을 19종 코드와 글자 Term 을 만든다.
    2. 메뉴 코드 = 저장된 `row.allergen_codes` 와 메뉴명 · 재료의 합집합을 사전으로 훑은 코드.
       저장된 코드만 믿으면 오래된 카탈로그 행 · 빠진 코드가 그대로 통과한다.
       메뉴 코드와 막을 코드의 교집합이 공집합이 아니라면 blocked.
    3. 글자 Term 을 `match_fields((display_name, *재료), …)` 로 찾아 걸리면 blocked. 필드마다
       따로 보고, guard 는 띄어쓰기를 넘어 덮지 않는다.
    4. 연령 · 아이 금지 식품(`food_safety_terms.yaml` age_rules) 중 이 월령에 해당하는 것:
       action=block 이면 blocked, caution 이면 `cautions` 에 남긴다.
       scope=name 인 규칙(술 · 카페인)은 메뉴명만 본다.
    5. 위에 안 걸렸는데 `resolved=False` 거나 재료가 없으면(빈 글자 · 기호뿐이어도) unchecked.
    6. 나머지 passed. passed 행에는 든 19종 정식 명칭도 `allergens` 로 같이 돌려준다. 2번의 메뉴
       코드와 같은 값이라 저장 코드만 쓰면 빠지는 성분(크림 → 우유)도 들어간다.

    months 를 모르면 그 단계가 시작하는 월령으로 본다 — 가장 어린 쪽이라 규칙을 덜 풀지 않는다.
    사전(yaml)을 읽지 못하면 예외를 그대로 올린다. 호출부는 잡아서 넘기지 않는다.
    """
    rules = resolve_safety(entries)
    age = first_month_of(stage) if months is None else months
    age_rules = tuple(rule for rule in food_safety_terms().age_rules if rule.applies_at(age))
    matchers = _Matchers(
        rules=TermMatcher(rules.terms),
        age=TermMatcher([r.term for r in age_rules if r.scope == "all"]),
        age_name=TermMatcher([r.term for r in age_rules if r.scope == "name"]),
    )

    passed: list[MenuCatalogRow] = []
    blocked: list[MenuCatalogRow] = []
    unchecked: list[MenuCatalogRow] = []
    hits: dict[str, tuple[str, ...]] = {}
    cautions: dict[str, tuple[str, ...]] = {}
    allergens: dict[str, tuple[str, ...]] = {}

    for row in rows:
        row_hits, row_cautions, row_codes = _row_hits(row, rules, age_rules, matchers)
        if row_cautions:
            cautions[row.menu_key] = row_cautions
        if row_hits:
            hits[row.menu_key] = row_hits
            blocked.append(row)
        elif not row.resolved or not prepare_fields(row.ingredients):
            # 빈 글자 · 기호만 있는 재료도 재료를 모르는 것이다
            unchecked.append(row)
        else:
            passed.append(row)
            names = tuple(
                ALLERGEN_NAMES[code] for code in sorted(row_codes) if code in ALLERGEN_NAMES
            )
            if names:
                allergens[row.menu_key] = names

    return SafetyVerdict(
        passed=tuple(passed),
        blocked=tuple(blocked),
        unchecked=tuple(unchecked),
        hits=hits,
        cautions=cautions,
        allergens=allergens,
    )


def resolve_safety(entries: Sequence[SafetyEntry]) -> SafetyRules:
    """health_safety 행들을 막을 것으로 바꾼다. 급식 조회는 `codes` 를 인쇄된 번호와도 대조한다.

    - `retracted` · `none` 이 아닌 행을 다 읽는다.
    - 알레르기 행은 `category` 에 `food` 가 있거나 비어 있어야 읽는다. `environment` · `drug`
      만 있는 행(쑥 · 페니실린)은 뺀다. `food` 와 `environment` 가 같이 있으면(쑥) 식품이기도
      해서 읽는다.
    - kind 와 상관없이 label 을 같은 사전으로 읽는다 — 알레르기 칸에 적은 "유당불내증",
      질환 칸에 적은 "우유 알레르기" 도 그대로 막는다. 질환은 label 이
      `chronic_restriction.yaml` 매핑에 있을 때만 그 식품을 막고, 없는 label(당뇨 · 고소공포)은
      식품을 유도하지 않는다.
    - 사전에서 정확히 같은 이름을 못 찾은 조각은 그 글자 그대로도 막는다(키위 → "키위").
    """
    book = food_book()
    codes: set[int] = set()
    terms: list[Term] = []

    def add(term: Term) -> None:
        if term not in terms:
            terms.append(term)

    for entry in entries:
        if entry.status in _INACTIVE_STATUSES:
            continue
        if entry.kind == "allergy" and entry.category and "food" not in entry.category:
            # 환경 · 약물로만 분류된 알레르기는 식품 필터의 대상이 아니다. 분류가 비어 있으면
            # 거른다 — 빠졌거나 틀렸을 때 덜 막는 쪽보다 과하게 막는 쪽이 안전하다
            continue
        found, rest = read_label(entry.label, book)
        codes |= found.codes
        for term in found.terms:
            add(term)
        if rest:
            add(Term(key=entry.label, aliases=rest))
    return SafetyRules(codes=frozenset(codes), terms=tuple(terms))


def menu_codes(fields: Sequence[str]) -> frozenset[int]:
    """메뉴명 · 재료를 사전(공용 이름 + Food 음식 어휘)으로 훑어 붙는 19종 코드."""
    return _codes_in(prepare_fields(fields))


def _codes_in(prepared: PreparedFields) -> frozenset[int]:
    return frozenset(int(key) for key in _code_matcher().match(prepared))


@cache
def food_book() -> LabelBook:
    """Food 가 보호자의 이름을 읽는 사전 — 공용 이름 사전에 Food 음식 어휘 · 질환을 더한다."""
    food = food_safety_terms()
    rules: list[NameRule] = shared_name_rules(food.group_foods)
    for term in food.code_terms:
        coded = Restriction(codes=frozenset({int(term.key)}))
        rules.extend((name, coded, term.guards) for name in term.aliases)
    for term in food.extras:
        rules.extend((name, Restriction(terms=(term,)), term.guards) for name in term.aliases)
    codes = chronic_restriction_codes()
    for name, chronic in chronic_restriction_terms().items():
        rules.append((name, Restriction(codes=codes.get(name, frozenset()), terms=chronic), ()))
    return make_book(rules)


@cache
def _code_matcher() -> TermMatcher:
    return TermMatcher(allergen_terms() + food_safety_terms().code_terms)


@dataclass(frozen=True)
class _Matchers:
    """한 번 호출 안에서 행마다 다시 만들지 않는 매처."""

    rules: TermMatcher
    age: TermMatcher  # 메뉴명 · 재료 전부를 보는 연령 규칙
    age_name: TermMatcher  # 메뉴명만 보는 연령 규칙


def _row_hits(
    row: MenuCatalogRow, rules: SafetyRules, age_rules: tuple[AgeRule, ...], matchers: _Matchers
) -> tuple[tuple[str, ...], tuple[str, ...], frozenset[int]]:
    """(막는 key, 주의 key, 메뉴 코드). 막는 key 는 코드 → 글자 Term → 연령 규칙 순서다.

    메뉴 코드는 저장된 코드와 메뉴명 · 재료를 훑은 코드의 합집합이다.
    """
    prepared = prepare_fields((row.display_name, *row.ingredients))
    blocked: list[str] = []

    def add(found: list[str], key: str) -> None:
        if key not in found:
            found.append(key)

    row_codes = row.allergen_codes | _codes_in(prepared)
    for code in sorted(row_codes & rules.codes):
        add(blocked, str(code))
    for key in matchers.rules.match(prepared):
        add(blocked, key)

    cautions: list[str] = []
    name = _core_name(row.display_name)
    age_hits = set(matchers.age.match(prepared))
    age_hits |= set(matchers.age_name.match(prepare_fields((row.display_name,))))
    for rule in age_rules:
        if name in rule.exact or name.endswith(tuple(rule.ends)) or rule.term.key in age_hits:
            add(blocked if rule.action == "block" else cautions, rule.term.key)
    return tuple(blocked), tuple(cautions), row_codes


def _core_name(name: str) -> str:
    """exact 비교용 메뉴명. 알레르기 번호는 급식표를 읽는 규칙(`strip_allergen_marks`)으로 뗀다."""
    text = strip_allergen_marks(unicodedata.normalize("NFKC", name))
    return normalize(_AMOUNT.sub(" ", _BRACKETS.sub(" ", text)))
