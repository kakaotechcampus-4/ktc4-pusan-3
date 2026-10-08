"""Growth 의 안전 필터 — 출력 검증이 후보 하나마다 부르는 순수 판정 함수들.

규칙은 Growth_Tool_명세.md §2 "안전 필터" 다. 알레르기 필터 · 위험 용어는 규칙(코드)이 막고
모델에게 묻지 않는다 (루트 CLAUDE.md §2).

    음식 용어 스캔   교육 · 루틴 후보. 알레르기 사전 용어나 질식 위험 음식 용어가 나오면 **아이 ·
                    월령 · 동의와 무관하게** 뺀다. 음식 · 재료가 들어가는 놀이는 Activity 몫이다
    등록한 이름 대조  **교육만**, 동의가 있을 때. 보호자가 `health_safety` 에 적은 이름이
                    후보 문장에 나오면 뺀다. 이름을 물건 · 장소로 넓히는 대응표는 만들지
                    않는다 (#261 리뷰 4번) —
                    대신 환경 알레르기가 있으면 확인 문구(`caution.environmental`)를 붙인다
    위험 용어        교육. 차단이면 뺀다. 경고는 후보와 함께 나간다 — **18개월 미만 경고 → 차단
                    승격을 적용하지 않는다** (Activity 와 다르다)

후보는 필드마다 **따로** 대조한다 — 이어 붙이면 필드 경계를 넘어 오탐이 난다. 걸린 후보는 고치지
않고 뺀다. 이 모듈은 어느 용어에 걸렸는지만 코드로 돌려주고 원문은 싣지 않는다(로그 · 모델 입력으로
흘러가지 않게). 걸린 이유를 모델에게 알리지 않는다 — 어휘 회피를 가르치게 된다 (Tool_공통 §5-2).

🚨 매처 · 사전은 `SafetyRules` 로 **주입**한다. 활동 문장 안전 판정은 #261(Activity) · #264(Food) 가
고치는 공유 코드에 있어서, 둘이 develop 에 들어가기 전에는 `load_safety_rules()` 의 임시 구현을
쓰고 들어간 뒤 공통 함수로 바꾼다 (issue 283 의 5번). 사전을 읽지 못하면 빈 사전으로 통과시키지
않고 `SafetyRulesUnavailable` 을 올린다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.agents.common.reference import HazardTerms, allergen_terms, hazard_terms
from app.agents.growth.readouts import CAUTION_ENVIRONMENTAL, READOUTS
from app.agents.growth.store.ports import SafetyEntry
from app.rules.term_match import Term, match_terms, normalize

HazardLevel = Literal["block", "warn"]
RemovedBy = Literal["food_term", "allergy_name", "hazard_block"]

# 질식 위험 음식 축. 교육 · 루틴 문장에 나오면 아이와 상관없이 뺀다
FOOD_CHOKING_AXIS = "food_choking"
# 보호자가 "라텍스 알레르기" 처럼 적는 꼬리말. 대조 전에 뗀다
_LABEL_TAILS = ("알레르기", "알러지")


class SafetyRulesUnavailable(Exception):
    """안전 사전을 읽지 못했다. 빈 사전으로 통과시키지 않는다 — 교육을 닫는다."""


@dataclass(frozen=True)
class HazardHit:
    label: str  # 위험 용어 label. 후보 원문이 아니다
    axis: str
    level: HazardLevel
    warning_text: str | None  # 경고일 때 화면에 붙는 코드 문구. LLM 이 쓰지 않는다


# text -> 걸린 음식 용어 key(알레르기 코드 · food_choking label). 원문이 아니다
FoodTermScan = Callable[[str], tuple[str, ...]]
# (text, months) -> 걸린 위험 용어. 월령으로 block/warn 을 정한다. 승격은 하지 않는다
HazardScan = Callable[[str, int], tuple[HazardHit, ...]]
# (text, active 행) -> 문장에 이름이 나온 행
RegisteredNameScan = Callable[[str, Sequence[SafetyEntry]], tuple[SafetyEntry, ...]]


@dataclass(frozen=True)
class SafetyRules:
    food_terms: FoodTermScan
    hazards: HazardScan
    registered_names: RegisteredNameScan


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool
    removed_by: RemovedBy | None = None
    cautions: tuple[
        str, ...
    ] = ()  # 경고 문구. 후보와 함께 나간다 — SuggestionDraft 에 칸을 더하지 않는다

    def __post_init__(self) -> None:
        if self.allowed == (self.removed_by is not None):
            raise ValueError("뺐으면 이유가, 통과했으면 이유가 없어야 한다")


def check_learning_candidate(
    texts: Sequence[str],
    *,
    months: int,
    entries: Sequence[SafetyEntry],
    rules: SafetyRules,
) -> SafetyVerdict:
    """교육 활동 후보 하나. `texts` 는 title · steps 각각 · materials 각각이다 — 이어 붙이지 않는다.

    `entries` 는 `build_gate` 가 읽어 run state 에 담은 행이다. 동의가 없으면 비어 있다 — 이 함수는
    건강정보를 읽지 않는다. active 가 아닌 행(retracted · none)은 막지 않는다.
    """
    if any(rules.food_terms(text) for text in texts):
        return SafetyVerdict(allowed=False, removed_by="food_term")

    active = tuple(entry for entry in entries if entry.status == "active")
    if active and any(rules.registered_names(text, active) for text in texts):
        return SafetyVerdict(allowed=False, removed_by="allergy_name")

    cautions: list[str] = []
    for text in texts:
        for hit in rules.hazards(text, months):
            if hit.level == "block":
                return SafetyVerdict(allowed=False, removed_by="hazard_block")
            if hit.warning_text and hit.warning_text not in cautions:
                cautions.append(hit.warning_text)
    return SafetyVerdict(allowed=True, cautions=tuple(cautions))


def check_routine_candidate(texts: Sequence[str], *, rules: SafetyRules) -> SafetyVerdict:
    """루틴 후보 하나. 음식 용어만 본다 — 루틴은 건강정보도 위험 용어도 읽지 않는다."""
    if any(rules.food_terms(text) for text in texts):
        return SafetyVerdict(allowed=False, removed_by="food_term")
    return SafetyVerdict(allowed=True)


def environmental_cautions(entries: Sequence[SafetyEntry]) -> tuple[str, ...]:
    """환경 알레르기가 active 로 등록돼 있으면 확인 문구를 낸다. 교육 추천에 붙는다.

    어디가 위험한지는 코드가 정하지 않는다 (꽃가루 → 공원 같은 표는 판단을 담게 된다). 보호자가
    적은 이름을 되돌려 보여 주고 장소 · 재료를 확인하게 할 뿐이다.
    """
    cautions: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        key = normalize(entry.label)
        if entry.kind != "environmental" or entry.status != "active" or key in seen:
            continue
        seen.add(key)
        cautions.append(READOUTS.render(CAUTION_ENVIRONMENTAL, label=entry.label).body)
    return tuple(cautions)


# ── 임시 구현 ────────────────────────────────────────────────────────────────
# #264 의 `common/allergy.py`(`read_label`) · #261 의 활동 안전 판정이 develop 에 들어가면
# 공통 함수로 바꾼다. 지금은 develop 의 `match_terms` 와 사전만 쓴다.


def load_safety_rules() -> SafetyRules:
    """develop 의 사전(`allergen_terms.yaml` · `hazard_terms.yaml`)으로 만든 임시 `SafetyRules`.

    읽지 못하거나 모양이 틀리면 `SafetyRulesUnavailable` — 빈 사전으로 통과시키지 않는다.
    """
    try:  # 어떤 실패든 "사전을 못 읽음" 하나로 올린다
        hazards = hazard_terms()
        raw_allergens = allergen_terms()
    except Exception as exc:
        raise SafetyRulesUnavailable("안전 사전을 읽지 못했다") from exc

    allergens = _without_single_char_aliases(raw_allergens)
    food_terms = (*allergens, *_food_choking_terms(hazards))
    # 보호자가 "밀" 이라고만 적었을 때 어느 알레르기인지는 한 글자 별칭까지 포함해서 찾는다.
    # 대조에 쓰는 별칭은 두 글자 이상만이다 — "밀" 등록 아이의 "공 밀기" 는 통과한다 (#261)
    kept = {term.key: term for term in allergens}
    by_alias = {
        normalize(alias): kept[term.key]
        for term in raw_allergens
        if term.key in kept
        for alias in term.aliases
    }

    def scan_food(text: str) -> tuple[str, ...]:
        return match_terms(text, food_terms)

    def scan_hazard(text: str, months: int) -> tuple[HazardHit, ...]:
        hits: list[HazardHit] = []
        for label in match_terms(text, hazards.terms):
            axis = hazards.axis_of(label)
            level = axis.level_at(months)  # 18개월 미만 승격은 하지 않는다
            if level is not None:
                hits.append(
                    HazardHit(
                        label=label,
                        axis=axis.name,
                        level=level,
                        warning_text=axis.warning_text if level == "warn" else None,
                    )
                )
        return tuple(hits)

    def scan_registered(text: str, entries: Sequence[SafetyEntry]) -> tuple[SafetyEntry, ...]:
        return tuple(
            entry for entry in entries if match_terms(text, (_registered_term(entry, by_alias),))
        )

    return SafetyRules(food_terms=scan_food, hazards=scan_hazard, registered_names=scan_registered)


def _without_single_char_aliases(terms: Sequence[Term]) -> tuple[Term, ...]:
    """한 글자 별칭(밀 · 게 · 닭 · 잣)을 뗀다. 놀이 문장의 "공 밀기" · "카드 게임" 이 걸린다."""
    kept: list[Term] = []
    for term in terms:
        aliases = tuple(alias for alias in term.aliases if len(normalize(alias)) >= 2)
        if aliases:
            kept.append(Term(key=term.key, aliases=aliases, guards=term.guards))
    return tuple(kept)


def _food_choking_terms(hazards: HazardTerms) -> tuple[Term, ...]:
    return tuple(t for t in hazards.terms if hazards.term_axis[t.key] == FOOD_CHOKING_AXIS)


def _registered_term(entry: SafetyEntry, by_alias: dict[str, Term]) -> Term:
    """보호자가 적은 이름 하나를 대조 용어로. 19종이면 사전 별칭으로 넓히고, 아니면 적은 그대로.

    19종 밖(쑥 · 라텍스)과 `environmental`(꽃가루)은 한 글자여도 그대로 쓴다 — 그 아이 하나의 값이라
    몇 개 더 막는 쪽이 놓치는 것보다 낫다 (#261).
    """
    label = entry.label
    for tail in _LABEL_TAILS:
        label = label.replace(tail, "")
    label = label.strip()
    known = by_alias.get(normalize(label)) if entry.kind == "allergy" else None
    if known is not None:
        return Term(key=entry.label, aliases=known.aliases, guards=known.guards)
    return Term(key=entry.label, aliases=(label or entry.label,))
