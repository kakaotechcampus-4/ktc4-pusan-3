"""알레르기·연령 금지식품을 거르는 내부 safety tool.

코드가 모델 호출 전에 후보 풀에서 위험한 것을 빼고,
모델이 답을 낸 후에 다시 한 번 거른다. 급식 조회도 같은 함수를 쓴다.

호출부(SafetyReader.food_safety)는 `state` 무관 전체 행을 돌려준다.
`unknown`·`retracted`·`none`이 섞이고, 거르는 것은 `state == "active"`뿐이다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.common.reference import allergen_terms, chronic_restriction_terms
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.rules.age import Stage
from app.rules.term_match import Term, match_terms, normalize

# 이유기 이하 단계에서 금지하는 재료
# 알레르기가 아니라 연령 규칙이라 allergen_terms()와 별도로 둔다.
_INFANT_BANNED: tuple[Term, ...] = (
    Term(key="honey", aliases=("꿀", "벌꿀")),
    Term(key="raw_milk", aliases=("생우유",)),
)

# LifeStage.stage 네 값 중 영아기 둘.
_INFANT_STAGES: frozenset[Stage] = frozenset({"infant_milk", "infant_weaning"})


@dataclass(frozen=True)
class SafetyVerdict:
    """`filter_food_safety` 한 번 호출의 결과. 행마다 셋 중 하나로 나뉜다."""

    passed: tuple[MenuCatalogRow, ...]
    blocked: tuple[MenuCatalogRow, ...]
    unchecked: tuple[MenuCatalogRow, ...]  # 해석 실패거나 재료 모름
    hits: dict[str, tuple[str, ...]]  # menu_key → 걸린 key. 로그에 싣지 않음


def filter_food_safety(
    rows: Sequence[MenuCatalogRow], entries: Sequence[SafetyEntry], stage: Stage
) -> SafetyVerdict:
    """행마다 판정 순서대로 거른다.

    1. `state == "active"` 인 알레르기
        - allergen_code가 이미 있으면 그대로 사용
        - 없으면 `label`·`aliases`를 `allergen_terms()`동의어표에 대조 -> 19종 코드로 정규화
          정규화된 코드 집합과 `row.allergen_codes` 교집합이 있으면 blocked.
    2. 정규화되지 않은 알레르기(19종 밖, 예: "키위")와 만성질환·식이제한
        - `match_terms(display_name + 재료, …)`로 걸리면 blocked.
        - 만성질환·식이제한은 보호자가 등록한 `restricted_foods` 와, 질환 정의상 그 식품을
          배제하는 것만 담은 매핑(`chronic_restriction.yaml`, 예: 유당불내증 → 우유)의
          합집합을 쓴다. 매핑에 없는 질환(당뇨 등)은 질환명만으로 식품을 유도하지 않는다.
    3. `stage` 가 `infant_*` 이면 `_INFANT_BANNED`(꿀·벌꿀·생우유)에 걸리면 blocked.
    4. 위에 안 걸렸는데 `resolved=False` 거나 `ingredients == ()` 면 unchecked.
    5. 나머지 passed.

    코드 있는 알레르기는 `row.allergen_codes` 교집합만 본다 — 이름 매칭(2번)은 문서상
    "코드 매핑 안 되는" 알레르기로 범위가 좁혀져 있고, `menu_catalog.allergen_codes`
    자체가 이미 "재료 + 메뉴명 사전의 합집합"이라 이름 매칭을 코드 판정에 다시 더하면
    같은 검사를 두 곳에서 하게 된다(메뉴명 매칭 책임은 `resolve_menu`).
    """
    active = [e for e in entries if e.state == "active"]
    canonical = allergen_terms()

    coded_codes: set[int] = set()
    restriction_entries: list[SafetyEntry] = []
    for entry in active:
        if entry.kind == "allergy":
            code = entry.allergen_code
            if code is None:
                code = _normalize_to_code(entry, canonical)
            if code is not None:
                coded_codes.add(code)
                continue
            restriction_entries.append(entry)
        else:
            restriction_entries.append(entry)

    coded_codes_frozen = frozenset(coded_codes)
    restriction_terms = _restriction_terms(restriction_entries)
    check_infant = stage in _INFANT_STAGES

    passed: list[MenuCatalogRow] = []
    blocked: list[MenuCatalogRow] = []
    unchecked: list[MenuCatalogRow] = []
    hits: dict[str, tuple[str, ...]] = {}

    for row in rows:
        row_hits = _row_hits(
            row,
            coded_codes=coded_codes_frozen,
            restriction_terms=restriction_terms,
            check_infant=check_infant,
        )
        if row_hits:
            hits[row.menu_key] = row_hits
            blocked.append(row)
        elif not row.resolved or not row.ingredients:
            unchecked.append(row)
        else:
            passed.append(row)

    return SafetyVerdict(
        passed=tuple(passed), blocked=tuple(blocked), unchecked=tuple(unchecked), hits=hits
    )


def _row_hits(
    row: MenuCatalogRow,
    *,
    coded_codes: frozenset[int],
    restriction_terms: tuple[Term, ...],
    check_infant: bool,
) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(key: str) -> None:
        if key not in seen:
            seen.add(key)
            ordered.append(key)

    for code in sorted(row.allergen_codes & coded_codes):
        _add(str(code))

    haystack = row.display_name + " " + " ".join(row.ingredients)
    for key in match_terms(haystack, restriction_terms):
        _add(key)
    if check_infant:
        for key in match_terms(haystack, _INFANT_BANNED):
            _add(key)

    return tuple(ordered)


def _normalize_to_code(entry: SafetyEntry, canonical: Sequence[Term]) -> int | None:
    """`label`·`aliases`를 동의어표와 대조해 19종 코드를 찾는다.

    "이 문자열 안에 이 용어가 있나"(`match_terms`)가 아니라 "이 label 자체가 어느
    코드의 별칭인가"를 보는 반대 방향이라 정규화(`normalize`) 값으로 직접 비교한다.
    """
    candidates = {normalize(entry.label)} | {normalize(a) for a in entry.aliases if a}
    candidates.discard("")
    if not candidates:
        return None
    for term in canonical:
        term_aliases = {normalize(a) for a in term.aliases}
        if candidates & term_aliases:
            return int(term.key)
    return None


def _restriction_terms(entries: Sequence[SafetyEntry]) -> tuple[Term, ...]:
    """동의어표로도 코드를 못 찾은 알레르기(19종 밖)와 만성질환·식이제한의 제한 식품.

    만성질환·식이제한은 두 출처의 합집합을 쓴다 — 둘 중 하나만 쓰면 미탐이 생긴다.
    - 보호자가 `restricted_foods` 에 직접 적은 값. 지금 이 값을 채우는 입력 경로가
      DB·API·화면 어디에도 없어 항상 비어 있지만, 생기면 그대로 쓴다.
    - `chronic_restriction_terms()` 매핑. 유당불내증·갈락토스혈증·셀리악병처럼 질환의
      정의 자체가 그 식품을 배제하는 것만 담겨 있다 — "당뇨"에서 "설탕"을 유도하지 않는다.
      매핑의 `guards` 도 함께 실어야 한다("밀크티"가 셀리악병에 걸리면 안 된다).

    `restricted_foods` 로 만드는 `Term` 에는 guard 가 없다 — 보호자가 적은 값에
    guard 를 붙일 근거가 없기 때문이다.
    """
    mapping = chronic_restriction_terms()
    terms: list[Term] = []
    for entry in entries:
        if entry.kind == "allergy":
            aliases = tuple(a for a in (entry.label, *entry.aliases) if a)
            if aliases:
                terms.append(Term(key=entry.label, aliases=aliases))
            continue

        mapped = mapping.get(normalize(entry.label))
        if mapped:
            terms.extend(mapped)

        restricted = tuple(a for a in entry.restricted_foods if a)
        if restricted:
            terms.append(Term(key=entry.label, aliases=restricted))
    return tuple(terms)
