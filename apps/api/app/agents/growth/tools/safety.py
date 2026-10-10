"""Growth 의 안전 필터 — 출력 검증이 후보 하나마다 부르는 순수 판정 함수들.

규칙은 Growth_Tool_명세.md §2 "안전 필터" 다. 알레르기 필터 · 위험 용어는 규칙(코드)이 막고
모델에게 묻지 않는다 (루트 CLAUDE.md §2).

    음식 용어 스캔   교육 · 루틴 후보. 알레르기 사전 용어나 질식 위험 음식 용어가 나오면 **아이 ·
                    월령 · 동의와 무관하게** 뺀다. 음식 · 재료가 들어가는 놀이는 Activity 몫이다.
                    한 글자 · 외래어 별칭(포크 · pork)은 문장에서 다른 뜻으로 걸려 쓰지 않는다
    등록한 이름 대조  교육만, 동의가 있을 때. 보호자가 `health_safety` 에 적은 이름이
                    후보 문장에 나오면 뺀다. 이름은 공용 `read_label` 로 조각마다 읽는다.
                    이름을 물건 · 장소로 넓히는 대응표는 만들지 않는 대신 식품 사전에 없는
                    알레르기 이름(꽃가루 · 라텍스)이 있으면 확인 문구
                    (`caution.non_food_allergy`)를 붙인다
    위험 용어        교육. 차단이면 뺀다. 경고는 후보와 함께 나간다.
                    0–17개월은 경고도 차단이다 — Activity 와 같은 공용 판정(`HazardAxis.level_at`)

후보는 필드마다 **따로** 대조한다 — 이어 붙이면 필드 경계를 넘어 오탐이 난다. 걸린 후보는 고치지
않고 뺀다. 이 모듈은 어느 용어에 걸렸는지만 코드로 돌려주고 원문은 싣지 않는다(로그 · 모델 입력으로
흘러가지 않게). 걸린 이유를 모델에게 알리지 않는다 — 어휘 회피를 가르치게 된다 (Tool_공통 §5-2).

🚨 매처 · 사전은 `SafetyRules`로 주입한다 — 판정 흐름 테스트는 가짜 매처로 돈다.
`load_safety_rules()` 는 Food · Activity 와 같은 공용 판정을 쓴다 — 보호자 이름은 `read_label`,
위험 용어 월령은 `HazardAxis.level_at`, 확인 문구는 `non_food_allergies`.
TODO: 음식 용어 스캔의 한 글자 식품("콩")은 따로 고친다. 사전을 읽지 못하면 빈
사전으로 통과시키지 않고 `SafetyRulesUnavailable` 을 올린다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.agents.common.allergy import non_food_allergies, read_label, shared_book, split_label
from app.agents.common.reference import HazardTerms, allergen_terms, hazard_terms
from app.agents.growth.readouts import CAUTION_NON_FOOD_ALLERGY, READOUTS
from app.agents.growth.store.ports import SafetyEntry
from app.rules.term_match import Term, match_terms, normalize

HazardLevel = Literal["block", "warn"]
RemovedBy = Literal["food_term", "allergy_name", "hazard_block"]

# 질식 위험 음식 축. 교육 · 루틴 문장에 나오면 아이와 상관없이 뺀다
FOOD_CHOKING_AXIS = "food_choking"
# 영어 이름을 한글로 옮긴 별칭. 공용 사전에는 검사지 이름을 읽으려고 있지만(#264), Growth 문장에서는
# 다른 뜻으로 더 자주 나와서 문장 스캔에서만 뺀다 — 포크(식사 도구) · 피치(음높이) · 에그 셰이커 ·
# 크랩 워크 · 비프 소리. 라틴 문자 별칭(pork · egg)도 같다.
# 보호자가 이 이름으로 등록한 걸 읽을 때는 쓴다
_LOANWORD_ALIASES = frozenset(
    {"에그", "밀크", "피넛", "크랩", "쉬림프", "포크", "피치", "월넛", "비프", "파인넛"}
)


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
# (text, months) -> 걸린 위험 용어. 월령으로 block/warn 을 정한다. 0–17개월은 경고도 차단이다
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


def allergy_cautions(entries: Sequence[SafetyEntry]) -> tuple[str, ...]:
    """식품 사전에 없는 알레르기 이름이 active 로 있으면 확인 문구를 낸다. 교육 추천에 붙는다.

    고르는 규칙은 Activity 와 같은 공용 판정(`common/allergy.non_food_allergies`)이다 — `allergy`
    active 행 중 이름에 식품 사전이 못 찾은 조각이 있는 것, 같은 이름은 한 번. `environmental`
    (고소공포 같은 것)에는 붙이지 않는다. 식품 알레르기는 이름 대조와 음식 용어 스캔이 뺀다.

    이름 사전은 `load_safety_rules` 가 게이트에서 먼저 읽는다 — 못 읽었으면 교육이 이미 닫혔다.
    """
    return tuple(
        READOUTS.render(CAUTION_NON_FOOD_ALLERGY, label=label).body
        for label in non_food_allergies(entries, shared_book())
    )


# ── 사전과 공용 판정으로 만든 SafetyRules ────────────────────────────────────────
# 보호자가 적은 이름은 Food · Activity 와 같은 공용 `read_label` 로 읽고, 위험 용어의 월령은
# 공용 `HazardAxis.level_at` 이 정한다(0–17개월 승격 포함). 문장 스캔에 쓸 별칭만 Growth 가 고른다.


def load_safety_rules() -> SafetyRules:
    """사전(`allergen_terms.yaml` · `hazard_terms.yaml`)과 공용 판정으로 만든 `SafetyRules`.

    읽지 못하거나 모양이 틀리면 `SafetyRulesUnavailable` — 빈 사전으로 통과시키지 않는다.
    """
    try:  # 어떤 실패든 "사전을 못 읽음" 하나로 올린다
        hazards = hazard_terms()
        raw_allergens = allergen_terms()
        # 보호자가 적은 이름을 읽는 사전. 확인 문구(`allergy_cautions`)도 이 사전으로 고른다
        book = shared_book()
    except Exception as exc:
        raise SafetyRulesUnavailable("안전 사전을 읽지 못했다") from exc

    allergens = _scan_aliases_only(raw_allergens)
    food_terms = (*allergens, *_food_choking_terms(hazards))
    # 19종 코드 → 문장 대조에 쓰는 별칭. 두 글자 이상 한국어 이름만("밀" 등록 아이의 "공 밀기" 는
    # 통과)이다. 보호자가 "밀" · "pork" 라고만 적었어도 `read_label` 이 코드로 읽는다
    kept = {term.key: term for term in allergens}

    def registered_terms(entry: SafetyEntry) -> tuple[Term, ...]:
        # 한 칸에 여러 이름("키위, 꽃가루")도 조각마다, 띄어 쓴 이름("고양이 털")은 낱말까지 읽는다.
        # 19종 · 묶음 이름(갑각류)은 사전 별칭으로 넓히고, 사전에 없는 이름은 적은 글자 그대로 본다.
        # 한 글자는 보호자가 그 글자만 적었을 때(쑥 · 개)만 쓴다 — 띄어 쓴 이름에서 떨어져 나온
        # 한 글자(털 · 곳)로 막으면 과차단이다. TODO: 한 글자 판정은 #305 에서 Food · Activity 와 맞춘다
        named, rest = read_label(entry.label, book)
        pieces = set(split_label(entry.label))
        coded = tuple(kept[key] for code in sorted(named.codes) if (key := str(code)) in kept)
        literal = tuple(
            Term(key=word, aliases=(word,)) for word in rest if len(word) >= 2 or word in pieces
        )
        return (*coded, *_scan_aliases_only(named.terms), *literal)

    def scan_food(text: str) -> tuple[str, ...]:
        return match_terms(text, food_terms)

    def scan_hazard(text: str, months: int) -> tuple[HazardHit, ...]:
        hits: list[HazardHit] = []
        for label in match_terms(text, hazards.terms):
            axis = hazards.axis_of(label)
            level = axis.level_at(months)
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
        return tuple(entry for entry in entries if match_terms(text, registered_terms(entry)))

    return SafetyRules(
        food_terms=scan_food,
        hazards=scan_hazard,
        registered_names=scan_registered,
    )


def _scan_aliases_only(terms: Sequence[Term]) -> tuple[Term, ...]:
    """문장 스캔에 쓰지 않는 별칭을 뗀다.

    한 글자(밀 · 게 · 닭 · 잣)는 놀이 문장의 "공 밀기" · "카드 게임" 이 걸리고,
    외래어(포크 · pork)는 식사 도구 · 노래 이름으로 걸린다.
    """
    kept: list[Term] = []
    for term in terms:
        aliases = tuple(alias for alias in term.aliases if _scans(alias))
        if aliases:
            kept.append(Term(key=term.key, aliases=aliases, guards=term.guards))
    return tuple(kept)


def _scans(alias: str) -> bool:
    name = normalize(alias)
    return (
        len(name) >= 2
        and name not in _LOANWORD_ALIASES
        and not any("a" <= ch <= "z" for ch in name)
    )


def _food_choking_terms(hazards: HazardTerms) -> tuple[Term, ...]:
    return tuple(t for t in hazards.terms if hazards.term_axis[t.key] == FOOD_CHOKING_AXIS)
