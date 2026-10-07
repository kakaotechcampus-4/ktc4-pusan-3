"""코드 tool — 모델에게 보이지 않고 코드가 정해진 지점에서 직접 부른다.

모델 tool 로 만들면 모델이 스스로 통과시킬 수 있다 (3-2 "만들지 않는 것").
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.activity.rules import is_recent_duplicate
from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.activity.store.ports import SafetyEntry
from app.agents.common.reference import allergen_terms, hazard_terms
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.term_match import Term, match_terms, normalize

# 이 월령 미만은 위험 용어의 경고도 차단으로 올린다 (D6 규칙 ②). 법령 · 학회 문장은 대부분
# "N세 미만은 감독이 필요하다" 라 경고만 나오는데, 0–17개월은 감독만으로 위험이 안 없어진다.
# 밴드 경계와 따로 둔다 — 로그 편의로 밴드를 옮길 때 안전 경계가 같이 끌려가지 않게.
SAFETY_PROMOTE_BELOW_MONTH = 18

# 알레르기 사전(allergen_terms.yaml)은 Food 의 메뉴 이름용이라 "밀" · "게" · "닭" · "잣" 같은
# 한 글자 별칭이 있다. 놀이 문장에 그대로 대조하면 "공 밀기" · "카드 게임" · "비밀 상자" ·
# "닭 그림" 이 걸린다. 위험 용어 사전이 한 글자 별칭을 금지한 것과 같은 이유로 Activity 는 쓰지
# 않는다 — "밀가루" · "꽃게" · "닭고기" · "잣가루" 처럼 두 글자 이상 별칭이 실제 위험을 잡는다.
MIN_ALLERGEN_ALIAS = 2

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
    - 19종 알레르기는 label 로 사전을 찾아 별칭과 guard 를 쓴다 — DB 에 별칭 칸이 없다(10/4).
      "밀" 로 등록돼 있으면 "밀가루 점토" 도 걸린다.
    - 19종 밖(키위 · 쑥)과 `environmental` 은 보호자가 적은 label 그대로 대조한다. 보호자가 직접
      적은 아이 고유의 값이라 한 글자여도 쓴다 — 놓치는 것보다 몇 개 더 막는 쪽이 안전하다.
    """
    canonical = _canonical_allergens()
    by_alias = {normalize(alias): term for term in allergen_terms() for alias in term.aliases}

    blocking: list[Term] = []
    registered: list[Term] = []
    for entry in entries:
        known = by_alias.get(normalize(entry.label)) if entry.kind == "allergy" else None
        if known is not None:
            term = canonical[known.key]
        else:
            term = Term(key=entry.label, aliases=(entry.label,))
            if entry.kind == "allergy":
                registered.append(term)
        if entry.status == "active":
            blocking.append(term)
    return SafetyTerms(blocking=tuple(blocking), allergens=(*canonical.values(), *registered))


def check_candidate(
    candidate: ActivityCandidate, *, months: int, terms: SafetyTerms
) -> SafetyCheck:
    """후보 하나를 위험 용어와 health_safety 로 판정한다.

    - `content` 와 `materials` 의 각 항목을 **따로** 대조한다. 이어 붙이면
      `["작은", "블록 담는 통"]` 이 "작은블록담는통" 이 되어 없는 위험을 만든다 (3-5).
    - 위험 용어는 걸린 축의 월령으로 차단/경고를 정하고, 18개월 미만은 경고도 차단이다.
    - 걸린 후보는 고치지 않는다. 차단이면 통째로 뺀다 — "물놀이터" 를 "얕은 물놀이터" 로 고쳐
      통과시키지 않는다.
    """
    texts = (candidate.content, *candidate.materials)
    dictionary = hazard_terms()

    hits: list[str] = []
    warnings: list[str] = []
    blocked = False
    for text in texts:
        for key in match_terms(text, dictionary.terms):
            axis = dictionary.axis_of(key)
            level = axis.level_at(months)
            if level == "warn" and months < SAFETY_PROMOTE_BELOW_MONTH:
                level = "block"
            if level is None:
                continue
            if axis.name not in hits:
                hits.append(axis.name)
            if level == "block":
                blocked = True
            elif axis.warning_text and axis.warning_text not in warnings:
                warnings.append(axis.warning_text)
        if match_terms(text, terms.blocking):
            blocked = True
            if HEALTH_SAFETY not in hits:
                hits.append(HEALTH_SAFETY)

    allergens = tuple(
        dict.fromkeys(key for text in texts for key in match_terms(text, terms.allergens))
    )
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


def filter_recent_duplicates(
    candidates: Sequence[ActivityCandidate], *, recent: Sequence[str]
) -> tuple[ActivityCandidate, ...]:
    """최근 창(`rules.DUPLICATE_WINDOW_DAYS`) 안에 한 활동과 같은 후보를 뺀다.

    판정은 `rules.is_recent_duplicate` — 정규화 후 완전 일치다. 출력 검증(`review.py`)은 같은
    판정으로 후보를 거절하고, 이 함수는 사전 조회한 후보 풀에서 미리 뺄 때 쓴다.
    """
    return tuple(c for c in candidates if not is_recent_duplicate(c.content, recent))


def _canonical_allergens() -> dict[str, Term]:
    """19종 사전을 코드 → 정식 명칭(`ALLERGEN_NAMES`)을 key 로 한 Term 으로. 한 글자 별칭은 뺀다."""
    result: dict[str, Term] = {}
    for term in allergen_terms():
        aliases = tuple(a for a in term.aliases if len(normalize(a)) >= MIN_ALLERGEN_ALIAS)
        name = ALLERGEN_NAMES[int(term.key)]
        result[term.key] = Term(key=name, aliases=aliases, guards=term.guards)
    return result
