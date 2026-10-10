"""코드 tool — 모델에게 보이지 않고 코드가 정해진 지점에서 직접 부른다.

모델 tool 로 만들면 모델이 스스로 통과시킬 수 있다 (3-2 "만들지 않는 것").
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.activity.readouts import CAUTION_NON_FOOD_ALLERGY, READOUTS
from app.agents.activity.rules import is_recent_duplicate
from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.activity.store.ports import SafetyEntry
from app.agents.common.allergy import non_food_allergies, read_label, shared_book
from app.agents.common.reference import allergen_terms, hazard_terms
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.term_match import Term, match_fields, normalize

# 알레르기 사전(allergen_terms.yaml)은 메뉴 이름용이라 놀이 문장에 그대로 대조하면 다른 뜻으로
# 걸린다. Activity 는 대조할 때만 아래 두 규칙으로 걸러 쓴다 — 보호자가 적은 이름을 읽을 때는
# 사전을 그대로 쓴다("에그" 로 등록하면 난류다).
#
# 한 글자 이름은 허용 목록만 쓴다. "밀" · "게" · "굴" · "닭" 은 "공 밀기" · "카드 게임" ·
# "굴리기" · "닭 그림" 이 걸린다. 잣 · 콩 · 깨 · 쑥은 놀이 문장에서도 거의 그 음식이다
# ("콩주머니" 에는 실제 콩이 든다). 사전에 없는 한 글자(강아지 알레르기를 "개" 로 적은 것)도
# 글자 그대로 대조하지 않는다 — "블록 3개" 가 걸린다 (#261 · #298 리뷰).
SINGLE_CHAR_NAMES = frozenset({"잣", "콩", "깨", "쑥"})
# 두 글자 이상이어도 놀이 문장에서 다른 뜻으로 읽히는 별칭 (#261 리뷰 — "플라스틱 포크" ·
# "에그 쉐이커" · "밀크 카톤" · "피치색 물감" · "전복 모양" · "돼지저금통")
EXCLUDED_ALIASES = frozenset({"포크", "에그", "밀크", "피치", "전복", "돼지"})

# 로그와 사유에 남기는 이름. 알레르기 이름 · 활동명은 남기지 않는다
HEALTH_SAFETY = "health_safety"


@dataclass(frozen=True)
class SafetyTerms:
    """이번 run 의 health_safety 행으로 만든 대조표. 후보마다 다시 만들지 않는다."""

    blocking: tuple[Term, ...]  # status='active' 행 — 걸리면 차단
    allergens: tuple[Term, ...]  # 놀이에 든 알레르기 항목 표시용 — 상태 무관, key 가 정식 명칭


@dataclass(frozen=True)
class SafetyCheck:
    """후보 하나의 안전 판정."""

    blocked: bool
    hits: tuple[str, ...]  # 걸린 위험 축 이름 · "health_safety". 로그용 — 원문을 싣지 않는다
    warnings: tuple[str, ...]  # 경고 축의 warning_text (상수). 차단이면 비어 있다
    allergens: tuple[str, ...]  # 놀이에 든 알레르기 항목 정식 명칭 (suggestion.allergens)


def safety_terms(entries: Sequence[SafetyEntry]) -> SafetyTerms:
    """health_safety 행을 대조표로 바꾼다.

    - 거르는 것은 `status='active'` 뿐이다. `none` · `retracted` · 행 없음(unknown)은 막지 않는다.
    - label 은 보호자의 자유 입력이라 Food 와 같은 규칙(`common/allergy.read_label`)으로 읽는다.
      "우유 알레르기" · "우유, 계란" · "Milk" 도 19종 코드가 되고, 묶음 이름("갑각류")은 게 ·
      새우로 펼친다. 19종은 사전의 별칭과 guard 로 넓혀 대조한다 — "밀" 로 등록하면 "밀가루 점토"
      도 걸린다.
    - 사전에 없는 조각(키위 · 꽃가루)은 그 글자 그대로 대조한다.
    - 대조에 쓰는 이름은 `SINGLE_CHAR_NAMES` · `EXCLUDED_ALIASES` 로 거른다.
    """
    canonical = _canonical_allergens()
    book = shared_book()

    blocking: list[Term] = []
    registered: list[Term] = []

    def add(found: list[Term], term: Term | None) -> None:
        if term is not None and term not in found:
            found.append(term)

    for entry in entries:
        named, rest = read_label(entry.label, book)
        terms = [canonical.get(str(code)) for code in sorted(named.codes)]
        terms += [_for_activity(term) for term in named.terms]
        literal = [_for_activity(Term(key=word, aliases=(word,))) for word in rest]
        if entry.kind == "allergy":
            for term in literal:
                add(registered, term)
        if entry.status == "active":
            for term in (*terms, *literal):
                add(blocking, term)
    return SafetyTerms(blocking=tuple(blocking), allergens=(*canonical.values(), *registered))


def check_candidate(
    candidate: ActivityCandidate, *, months: int, terms: SafetyTerms
) -> SafetyCheck:
    """후보 하나를 위험 용어와 health_safety 로 판정한다.

    - `content` 와 `materials` 의 각 항목을 **따로** 대조한다(`match_fields`). 이어 붙이면
      `["작은", "블록 담는 통"]` 이 "작은블록담는통" 이 되어 없는 위험을 만든다 (3-5). 한 항목
      안에서도 별칭은 문장부호를, guard 는 띄어쓰기를 넘지 않는다.
    - 위험 용어는 걸린 축의 월령으로 차단/경고를 정한다(`HazardAxis.level_at`). 18개월 미만은
      경고도 차단이다 — 승격은 Growth 와 같이 쓰는 그 공용 판정이 한다 (D6 규칙 ②).
    - 걸린 후보는 고치지 않는다. 차단이면 통째로 뺀다 — "물놀이터" 를 "얕은 물놀이터" 로 고쳐
      통과시키지 않는다.
    """
    texts = (candidate.content, *candidate.materials)
    dictionary = hazard_terms()

    hits: list[str] = []
    warnings: list[str] = []
    blocked = False
    for key in match_fields(texts, dictionary.terms):
        axis = dictionary.axis_of(key)
        level = axis.level_at(months)
        if level is None:
            continue
        if axis.name not in hits:
            hits.append(axis.name)
        if level == "block":
            blocked = True
        elif axis.warning_text and axis.warning_text not in warnings:
            warnings.append(axis.warning_text)
    if match_fields(texts, terms.blocking):
        blocked = True
        hits.append(HEALTH_SAFETY)

    allergens = tuple(dict.fromkeys(match_fields(texts, terms.allergens)))
    return SafetyCheck(
        blocked=blocked,
        hits=tuple(hits),
        warnings=() if blocked else tuple(warnings),
        allergens=allergens,
    )


def filter_activity_safety(
    candidates: Sequence[ActivityCandidate],
    *,
    months: int,
    safety: Sequence[SafetyEntry],
) -> tuple[SafetyCheck, ...]:
    """안전 필터 (코드 tool). 후보마다 판정을 같은 순서로 돌려준다.

    `safety` 는 build_gate 가 run state 에 담아 둔 행이다 — 여기서 health_safety 를 다시 읽지
    않는다. 위험 용어 사전을 읽지 못하면 예외를 그대로 올린다 — 빈 사전으로 통과시키면 전 후보가
    조용히 나간다.
    """
    terms = safety_terms(safety)
    return tuple(check_candidate(c, months=months, terms=terms) for c in candidates)


def allergy_cautions(entries: Sequence[SafetyEntry]) -> tuple[str, ...]:
    """식품 사전에 없는 알레르기 이름이 active면 확인 문구를 한 줄씩 낸다(D7).

    꽃가루 · 동물털은 재료가 아니라 장소와 계절에 걸려 있어 뺄 놀이를 코드가 못 정한다.
    이름이 문장에 그대로 나오는 후보만 `check_candidate` 가 뺀다. 그래서 보호자가 적은 이름을
    보여 주고 장소 · 재료를 확인하게 한다. 어느 행에 붙일지는 Growth 와 같은 공용 판정
    (`common/allergy.non_food_allergies`)이 고른다. `environmental`(고소공포 같은 것)에는
    붙이지 않는다.

    `entries` 는 build_gate가 run state에 담아 둔 행으로, 여기서 다시 읽지 않는다.
    """
    return tuple(
        READOUTS.render(CAUTION_NON_FOOD_ALLERGY, label=label).body
        for label in non_food_allergies(entries, shared_book())
    )


def filter_recent_duplicates(
    candidates: Sequence[ActivityCandidate], *, recent: Sequence[str]
) -> tuple[ActivityCandidate, ...]:
    """최근 창(`rules.DUPLICATE_WINDOW_DAYS`) 안에 한 활동과 같은 후보를 뺀다.

    판정은 `rules.is_recent_duplicate` — 정규화 후 완전 일치다. 출력 검증(`review.py`)은 같은
    판정으로 후보를 거절하고, 이 함수는 사전 조회한 후보 풀에서 미리 뺄 때 쓴다.
    """
    return tuple(c for c in candidates if not is_recent_duplicate(c.content, recent))


def _canonical_allergens() -> dict[str, Term]:
    """19종 사전을 코드 → 정식 명칭(`ALLERGEN_NAMES`)을 key 로 한 Term 으로 (Activity 대조용)."""
    result: dict[str, Term] = {}
    for term in allergen_terms():
        name = ALLERGEN_NAMES[int(term.key)]
        usable = _for_activity(Term(key=name, aliases=term.aliases, guards=term.guards))
        if usable is not None:
            result[term.key] = usable
    return result


def _for_activity(term: Term) -> Term | None:
    """놀이 문장 대조에 쓸 별칭만 남긴다. 남는 게 없으면 None — 그 이름으로는 대조하지 않는다."""
    excluded = {normalize(alias) for alias in EXCLUDED_ALIASES}
    aliases = tuple(
        alias
        for alias in term.aliases
        if (key := normalize(alias)) not in excluded and (len(key) >= 2 or key in SINGLE_CHAR_NAMES)
    )
    if not aliases:
        return None
    return Term(key=term.key, aliases=aliases, guards=term.guards)
