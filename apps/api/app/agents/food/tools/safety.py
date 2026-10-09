"""알레르기 · 연령 · 아이 금지 식품을 거르는 내부 safety tool.

코드가 모델 호출 전에 후보 풀에서 위험한 것을 빼고,
모델이 답을 낸 후에 다시 한 번 거른다. 급식 조회도 같은 함수를 쓴다.
제품마다 다른 성분(간장의 밀)도 지금은 막되, 그것 때문에만 막힌 메뉴를 needs_check 로 표시한다.

호출부(SafetyReader.food_safety)는 `status='active'` 행만 돌려주지만 여기서 한 번 더 본다.
거르지 않는 것은 `retracted` · `none` 둘뿐이고, 그 밖의 값은 모르는 값이라도 거른다.
어댑터가 `none` 행을 섞어 보내면 그 행을 알레르기로 읽지 않고, status 를 잘못 옮겨도 필터가
조용히 꺼지지 않는다. unknown 은 저장하지 않는 값(행이 없는 것)이라 이 칸에 오면 어댑터 버그다.

규칙의 정본은 docs/agents/food/Food_Tool_명세.md §3 "음식 안전 수칙" 이다.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Literal

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

# 추천인지 급식 조회인지. 급식은 기관이 이미 잘라서 주는 떡을 막지 않고 주의만 단다
Purpose = Literal["recommend", "daycare"]

# 연령 규칙의 exact 는 메뉴명에서 급식표의 알레르기 번호 · 괄호 · 양을 뗀 뒤 비교한다
# ("우유 2,5" · "우유(2)" · "우유(200ml)" · "우유 한 컵" 은 마시는 우유다)
_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_AMOUNT = re.compile(r"(?:\d+(?:\.\d+)?|한|두|반)\s*(?:ml|cc|l|g|컵|잔|팩|병|개)", re.IGNORECASE)


@dataclass(frozen=True)
class IngredientCheck:
    """제품마다 다른 성분 하나 — "사용할 {item}에 {allergen} 성분이 들어 있는지 확인해 주세요".

    아이의 알레르기를 묻는 질문이 아니다. 알레르기가 있는지가 아니라, 보호자가 쓸 제품에
    그 성분이 있는지를 묻는다(#267 멘토 리뷰).
    """

    item: str  # 사전의 음식 이름(간장 · 어묵). 레시피 원문 조각이 아니다
    allergen: str  # 19종 정식 명칭(ALLERGEN_NAMES — 밀)


@dataclass(frozen=True)
class SafetyVerdict:
    """`filter_food_safety` 한 번 호출의 결과. 행마다 passed · blocked · unchecked 중 하나다.

    needs_check 는 blocked 의 일부다 — 제품 확인으로 풀 수 있는 행을 표시해 둔 것이다.
    """

    passed: tuple[MenuCatalogRow, ...]
    blocked: tuple[MenuCatalogRow, ...]
    unchecked: tuple[MenuCatalogRow, ...]  # 해석 실패거나 재료 모름 — 추천하지 않는다
    hits: dict[str, tuple[str, ...]]  # menu_key → 걸린 key. 로그에 싣지 않음
    # menu_key → 주의 규칙 label. 막지 않고 안내만 붙인다(질식 주의 → guide.choking_caution).
    # 나뉜 묶음과 따로 본다 — passed 행에도 붙는다
    cautions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # menu_key → 그 메뉴에 들어 있거나 제품에 따라 들어 있을 수 있는 19종 정식 명칭(코드 순).
    # passed · needs_check 행만 싣고, 없으면 키가 없다.
    # 추천의 `suggestion.allergens` 는 이 값을 그대로 쓴다(#264 PM 결정).
    # 승인 때 health_safety 와 대조해서 행이 없는 항목을 "먹어본 적 있나요?" 로 묻는다.
    # 들어 있을 수 있는 성분까지 싣는 것은 등록하지 않은 알레르기도 계속 묻기 위해서다
    allergens: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # blocked 중 제품마다 다른 성분 때문에만 막힌 행 — 재료를 알고, 확실한 성분 · 글자 ·
    # 연령 규칙에는 안 걸렸다. 지금은 막는다(#264 PM 결정). 식단 추천을 구현할 때 `checks` 를
    # 실어 "확인 필요" 로 내보낸다 — 확인 전에는 채택하지 않는다(#267 멘토 리뷰)
    needs_check: tuple[MenuCatalogRow, ...] = ()
    # menu_key → 그 메뉴의 제품마다 다른 성분(코드 순 · 이름 순).
    # passed · needs_check 행에 붙는다. passed 행에도 싣는다 — 추천이 사는 24시간 안에
    # 보호자가 그 알레르기를 등록할 수 있다
    checks: dict[str, tuple[IngredientCheck, ...]] = field(default_factory=dict)


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
    purpose: Purpose = "recommend",
) -> SafetyVerdict:
    """행마다 판정 순서대로 거른다.

    1. health_safety 행을 `resolve_safety` 로 읽어 막을 19종 코드와 글자 Term 을 만든다.
    2. 메뉴 코드 = 저장된 `row.allergen_codes` 와 메뉴명 · 재료를 사전으로 훑은 코드의 합집합.
       저장된 코드만 믿으면 오래된 카탈로그 행 · 빠진 코드가 그대로 통과한다. 메뉴명에 인쇄된
       알레르기 번호(급식표 "(2.5.6)")는 읽지 않는다 — 기관이 아이의 알레르기를 관리하고
       보호자에게 미리 알린다. 제품마다 다른 성분(varies_foods)은 메뉴 코드에 넣지 않고
       (코드, 음식)으로 따로 든다. 메뉴 코드나 제품마다 다른 성분의 코드가 막을 코드와 겹치면
       blocked.
    3. 글자 Term 을 `match_fields((display_name, *재료), …)` 로 찾아 걸리면 blocked. 필드마다
       따로 보고, guard 는 띄어쓰기를 넘어 덮지 않는다.
    4. 연령 · 아이 금지 식품(`food_safety_terms.yaml` age_rules) 중 이 월령에 해당하는 것:
       action=block 이면 blocked, caution 이면 `cautions` 에 남긴다.
       scope=name 인 규칙(술 · 카페인)은 메뉴명만 본다.
       급식 조회(`purpose="daycare"`)는 규칙의 `daycare_action` 이 있으면 그것을 쓴다(떡 → 주의).
       purpose 를 넘기지 않으면 추천이다 — 더 막는 쪽이다. 알레르기 판정은 purpose 와 상관없다.
    5. 위에 안 걸렸는데 `resolved=False` 거나 재료가 없으면(빈 글자 · 기호뿐이어도) unchecked.
    6. 나머지 passed.
    7. blocked 중 제품마다 다른 성분 때문에만 막힌 행(재료를 알고, 2 의 메뉴 코드 · 3 · 4 에는
       안 걸림)은 needs_check 에도 넣는다. 지금은 막고, 식단 추천을 구현할 때 확인 필요로 내보낸다.
       passed · needs_check 행은 `allergens`(들어 있거나 들어 있을 수 있는 19종 이름)와 `checks` 를
       싣는다.

    months 를 모르면 그 단계가 시작하는 월령으로 본다 — 가장 어린 쪽이라 규칙을 덜 풀지 않는다.
    사전(yaml)을 읽지 못하면 예외를 그대로 올린다. 호출부는 잡아서 넘기지 않는다.
    """
    rules = resolve_safety(entries)
    age = first_month_of(stage) if months is None else months
    age_rules = tuple(
        _for_purpose(rule, purpose)
        for rule in food_safety_terms().age_rules
        if rule.applies_at(age)
    )
    matchers = _Matchers(
        rules=TermMatcher(rules.terms),
        age=TermMatcher([r.term for r in age_rules if r.scope == "all"]),
        age_name=TermMatcher([r.term for r in age_rules if r.scope == "name"]),
    )

    passed: list[MenuCatalogRow] = []
    blocked: list[MenuCatalogRow] = []
    unchecked: list[MenuCatalogRow] = []
    needs_check: list[MenuCatalogRow] = []
    hits: dict[str, tuple[str, ...]] = {}
    cautions: dict[str, tuple[str, ...]] = {}
    allergens: dict[str, tuple[str, ...]] = {}
    checks: dict[str, tuple[IngredientCheck, ...]] = {}

    for row in rows:
        prepared = prepare_fields((row.display_name, *row.ingredients))
        row_hits, row_cautions, row_codes, row_varies = _row_hits(
            row, prepared, rules, age_rules, matchers
        )
        if row_cautions:
            cautions[row.menu_key] = row_cautions
        # 빈 글자 · 기호만 있는 재료도 재료를 모르는 것이다
        known = row.resolved and bool(prepare_fields(row.ingredients))
        # 제품마다 다른 성분이 막을 코드와 겹치면 지금은 그것만으로도 막는다(#264 PM 결정)
        varies_hits = tuple(str(code) for code in sorted({c for c, _ in row_varies} & rules.codes))
        if row_hits or varies_hits:
            hits[row.menu_key] = row_hits + tuple(k for k in varies_hits if k not in row_hits)
            blocked.append(row)
            if row_hits or not known:
                continue  # 확인으로 풀 수 없다 — 확실히 들었거나, 다른 재료를 모른다
            needs_check.append(row)
        elif not known:
            unchecked.append(row)
            continue
        else:
            passed.append(row)
        # 들어 있을 수 있는 성분까지 이름 표에 싣는다 — 등록하지 않은 알레르기도 계속 묻는다
        codes = row_codes | {code for code, _ in row_varies}
        # 19종 밖은 이름으로 담는다(데이터 모델). 승인 때 active 행과 대조하는 데만 쓴다.
        # 19종 밖은 행이 active 로만 생겨서 "모름" 이 없다 — 물으면 추천마다
        # "쌀 — 먹어본 적 있나요?" 가 뜬다
        names = (
            *(ALLERGEN_NAMES[c] for c in sorted(codes) if c in ALLERGEN_NAMES),
            *_extra_matcher().match(prepared),
        )
        if names:
            allergens[row.menu_key] = names
        if row_varies:
            checks[row.menu_key] = tuple(
                IngredientCheck(item=food, allergen=ALLERGEN_NAMES[code])
                for code, food in row_varies
            )

    return SafetyVerdict(
        passed=tuple(passed),
        blocked=tuple(blocked),
        unchecked=tuple(unchecked),
        hits=hits,
        cautions=cautions,
        allergens=allergens,
        needs_check=tuple(needs_check),
        checks=checks,
    )


def resolve_safety(entries: Sequence[SafetyEntry]) -> SafetyRules:
    """health_safety 행들을 막을 것으로 바꾼다.

    - `retracted` · `none` 이 아닌 행을 다 읽는다.
    - 알레르기는 분류 없이 전부 읽는다. 환경 · 약물 알레르기(꽃가루 · 페니실린)도 읽지만
      메뉴명 · 재료와 매칭되지 않으면 아무것도 막지 않는다. 읽는 행이 늘기만 하므로 막히던
      메뉴가 통과하게 되는 경우는 없다.
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
    for term in food.varies_terms:
        coded = frozenset({int(term.key)})
        for name in term.aliases:
            # 이 이름으로 등록하면 그 코드와 그 음식 글자를 함께 막는다. 이름 읽기는 확실 ·
            # 확인 필요를 가르지 않는다 — "어묵 알레르기" 를 확인 질문으로 풀면 덜 막는다.
            # 같은 칸의 동의어(어묵 = 오뎅 · 맛살 = 크래미)도 같은 음식이라 함께 막는다
            own = Term(key=name, aliases=term.aliases, guards=term.guards)
            rules.append((name, Restriction(codes=coded, terms=(own,)), term.guards))
    for term in food.extras:
        rules.extend((name, Restriction(terms=(term,)), term.guards) for name in term.aliases)
    codes = chronic_restriction_codes()
    for name, chronic in chronic_restriction_terms().items():
        rules.append((name, Restriction(codes=codes.get(name, frozenset()), terms=chronic), ()))
    return make_book(rules)


@cache
def _code_matcher() -> TermMatcher:
    return TermMatcher(allergen_terms() + food_safety_terms().code_terms)


@cache
def _varies_matcher() -> TermMatcher:
    """제품마다 다른 성분. key 는 "코드:음식" — 걸린 음식 이름을 확인 질문에 쓴다."""
    return TermMatcher(
        [
            Term(key=f"{term.key}:{name}", aliases=(name,), guards=term.guards)
            for term in food_safety_terms().varies_terms
            for name in term.aliases
        ]
    )


@cache
def _extra_matcher() -> TermMatcher:
    """19종 밖 알레르기 이름. key 는 extra_allergens 의 label — 이름 표에 그대로 담는다."""
    return TermMatcher(food_safety_terms().extras)


@dataclass(frozen=True)
class _Matchers:
    """한 번 호출 안에서 행마다 다시 만들지 않는 매처."""

    rules: TermMatcher
    age: TermMatcher  # 메뉴명 · 재료 전부를 보는 연령 규칙
    age_name: TermMatcher  # 메뉴명만 보는 연령 규칙


def _row_hits(
    row: MenuCatalogRow,
    prepared: PreparedFields,
    rules: SafetyRules,
    age_rules: tuple[AgeRule, ...],
    matchers: _Matchers,
) -> tuple[tuple[str, ...], tuple[str, ...], frozenset[int], tuple[tuple[int, str], ...]]:
    """(막는 key, 주의 key, 메뉴 코드, 확인할 (코드, 음식)). 막는 key 는 코드 → 글자 Term →
    연령 규칙 순서다. 제품마다 다른 성분으로 막는 것은 여기 넣지 않는다 — 호출부가 따로 본다.

    메뉴 코드는 저장된 코드와 메뉴명 · 재료를 훑은 코드의 합집합이다.
    확인할 것은 제품마다 다른 성분 중 메뉴 코드에 없는 것이다 — 그 성분이 확실히 든 재료가
    같이 있으면(간장 국수의 소면) 메뉴 코드로 정해져서 확인할 필요가 없다.
    """
    blocked: list[str] = []

    def add(found: list[str], key: str) -> None:
        if key not in found:
            found.append(key)

    row_codes = row.allergen_codes | _codes_in(prepared)
    for code in sorted(row_codes & rules.codes):
        add(blocked, str(code))
    for key in matchers.rules.match(prepared):
        add(blocked, key)

    varies: set[tuple[int, str]] = set()
    for key in _varies_matcher().match(prepared):
        code, food = key.split(":", 1)
        if int(code) not in row_codes:
            varies.add((int(code), food))

    cautions: list[str] = []
    name = _core_name(row.display_name)
    age_hits = set(matchers.age.match(prepared))
    age_hits |= set(matchers.age_name.match(prepare_fields((row.display_name,))))
    for rule in age_rules:
        if name in rule.exact or name.endswith(tuple(rule.ends)) or rule.term.key in age_hits:
            add(blocked if rule.action == "block" else cautions, rule.term.key)
    return tuple(blocked), tuple(cautions), row_codes, tuple(sorted(varies))


def _for_purpose(rule: AgeRule, purpose: Purpose) -> AgeRule:
    """급식 조회는 daycare_action 이 있으면 그것을 action 으로 쓴다."""
    if purpose == "daycare" and rule.daycare_action is not None:
        return replace(rule, action=rule.daycare_action)
    return rule


def _core_name(name: str) -> str:
    """exact 비교용 메뉴명. 알레르기 번호는 급식표를 읽는 규칙(`strip_allergen_marks`)으로 뗀다."""
    text = strip_allergen_marks(unicodedata.normalize("NFKC", name))
    return normalize(_AMOUNT.sub(" ", _BRACKETS.sub(" ", text)))
