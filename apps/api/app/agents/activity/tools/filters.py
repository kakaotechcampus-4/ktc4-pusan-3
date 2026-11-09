"""코드 tool — 모델에게 보이지 않고 코드가 정해진 지점에서 직접 부른다.

모델 tool 로 만들면 모델이 스스로 통과시킬 수 있다 (3-2 "만들지 않는 것").
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.activity.readouts import (
    CAUTION_NON_FOOD_ALLERGY,
    READOUTS,
    REQUEST_FOOD_AGE_BLOCK,
    REQUEST_FOOD_CONFLICT,
)
from app.agents.activity.rules import is_recent_duplicate
from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.activity.store.ports import SafetyEntry
from app.agents.common.age import stage_of
from app.agents.common.allergy import (
    LabelBook,
    allergy_book,
    allergy_related_table,
    for_sentences,
    non_food_allergies,
    read_label,
    split_label,
)
from app.agents.common.food_safety import menu_codes
from app.agents.common.food_words import (
    ITEM,
    SENTENCE,
    FoodVerdict,
    WordRules,
    check_foods,
    phrase_of,
)
from app.agents.common.readout import Readout
from app.agents.common.reference import AgeRule, allergen_terms, hazard_terms
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.particle import topic_particle
from app.rules.term_match import Term, match_fields

# 알레르기 사전(allergen_terms.yaml)은 메뉴 이름용이라 놀이 문장에 그대로 대조하면 다른 뜻으로
# 걸린다. 대조할 때만 `common/allergy.for_sentences` 로 거르고 문장용 guard 를 더해 쓴다 — 보호자가
# 적은 이름을 읽을 때는 사전을 그대로 쓴다("에그" 로 등록하면 난류다). 한 글자는 허용 목록
# (`SINGLE_CHAR_NAMES` — 잣 · 콩 · 깨 · 쑥 · 쌀 · 팥 · 꽃)만 쓴다. Growth 의 등록 이름 대조와 같다.

# 로그와 사유에 남기는 이름. 알레르기 이름 · 활동명은 남기지 않는다
HEALTH_SAFETY = "health_safety"
# 음식 칸 · 음식 활동 문장의 연령 규칙(꿀 · 떡 …)에 걸렸다 — 알레르기와 상관없다 (#582)
FOOD_AGE = "food_age"
# 음식 칸에 사전이 모르는 음식이 왔고 아이에게 음식 알레르기가 있다 — "확인 못 함" 이라 뺀다 (#582)
FOOD_UNKNOWN = "food_unknown"


@dataclass(frozen=True)
class SafetyTerms:
    """이번 run 의 health_safety 행으로 만든 대조표. 후보마다 다시 만들지 않는다."""

    blocking: tuple[Term, ...]  # status='active' 행 — 걸리면 차단. content 대조용
    blocking_items: tuple[Term, ...]  # 같은 행의 materials · foods 대조용 — 다른 뜻 별칭도 막는다
    allergens: tuple[Term, ...]  # 놀이에 든 알레르기 항목 표시용 — 상태 무관, key 가 정식 명칭
    # 음식 칸 · 음식 활동 문장을 Food 판정(`common/food_words.check_foods`)에 넘길 행 (#582).
    # check_foods 가 retracted · none 을 스스로 거른다
    rows: tuple[SafetyEntry, ...] = ()
    # 활성 음식 알레르기가 있는가 — 사전이 모르는 음식을 뺄지 정한다(`_has_food_allergy`)
    food_allergy: bool = False


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
    - 사전에 없는 조각(키위 · 꽃가루)은 그 글자 그대로 대조한다. 허용 목록의 한 글자(쌀)는 그
      guard 와 함께 대조한다 — "쌀쌀한 날씨" 는 걸리지 않는다.
    - 대조에 쓰는 이름은 `for_sentences` 로 거른다 — 한 글자는 `SINGLE_CHAR_NAMES`, 다른 뜻이 흔한
      별칭(포크 · 돼지)은 뺀다. 19종 밖 식품(`allergy_book()` 의 별칭 표)과 글자 그대로 대조하는
      이름은 문장용 guard 도 더한다("은행" 으로 등록해도 "은행놀이" 는 통과). 문장에서 다른 뜻이
      더 흔한 별칭(오리)은 content 에서만 빼고 materials 에서는 막는다 — "오리처럼 걷기" 는
      통과하고, 준비물에 "오리" 를 적은 활동은 막힌다.
    """
    canonical = _canonical_allergens()
    book = allergy_book()

    blocking: list[Term] = []
    blocking_items: list[Term] = []
    registered: list[Term] = []

    def add(found: list[Term], term: Term | None) -> None:
        if term is not None and term not in found:
            found.append(term)

    for entry in entries:
        named, rest = read_label(entry.label, book)
        coded = [canonical.get(str(code)) for code in sorted(named.codes)]
        # 별칭 표의 식품과 적은 글자 그대로의 이름 — 둘 다 문장용 guard 를 쓴다. "은행" 처럼
        # 묶음 별칭에만 있는 이름은 글자 그대로 오는데, 빠지면 "은행놀이" 가 막힌다 (#556 2차 리뷰)
        literal = tuple(Term(key=word, aliases=(word,)) for word in rest)
        foods = (*named.terms, *literal)
        if entry.kind == "allergy":
            for term in literal:
                add(registered, for_sentences(term))
        if entry.status == "active":
            for term in (*coded, *(for_sentences(t) for t in foods)):
                add(blocking, term)
            for term in (*coded, *(for_sentences(t, scope="items") for t in foods)):
                add(blocking_items, term)
    return SafetyTerms(
        blocking=tuple(blocking),
        blocking_items=tuple(blocking_items),
        allergens=(*canonical.values(), *registered),
        rows=tuple(entries),
        food_allergy=_has_food_allergy(entries, book),
    )


def _has_food_allergy(entries: Sequence[SafetyEntry], book: LabelBook) -> bool:
    """활성 알레르기 중 음식일 수 있는 것이 있는가.

    이름 조각마다 본다. 식품 사전이 찾으면 음식이다. 사전이 못 찾은 조각은 식품 아닌 알레르기 표
    (`allergy_related_terms.yaml` — 꽃가루 · 라텍스 · 동물털)에 있을 때만 음식이 아니고, 표에도
    없으면 음식으로 본다 — 사전이 모르는 과일일 수 있어서 막는 쪽이다.
    """
    non_food = tuple(entry.names for entry in allergy_related_table())
    for entry in entries:
        if entry.kind != "allergy" or entry.status != "active":
            continue
        for piece in split_label(entry.label):
            found, rest = read_label(piece, book)
            if found.codes or found.terms:
                return True
            if rest and not match_fields((piece,), non_food):
                return True
    return False


def check_candidate(
    candidate: ActivityCandidate, *, months: int, terms: SafetyTerms
) -> SafetyCheck:
    """후보 하나를 위험 용어와 health_safety 로 판정한다.

    - `content` 와 `materials` 의 각 항목을 **따로** 대조한다(`match_fields`). 이어 붙이면
      `["작은", "블록 담는 통"]` 이 "작은블록담는통" 이 되어 없는 위험을 만든다 (3-5). 한 항목
      안에서도 별칭은 문장부호를, guard 는 띄어쓰기를 넘지 않는다.
    - 알레르기 이름은 띄어쓰기도 넘지 않는다(`cross_spaces=False`) — "장난감 자동차" 가 감자로,
      "중 하나" 가 새우(중하)로 걸린다. 위험 용어는 넘는다 — "물 놀이" 도 물놀이다.
    - 위험 용어는 걸린 축의 월령으로 차단/경고를 정한다(`HazardAxis.level_at`). 18개월 미만은
      경고도 차단이다 — 승격은 Growth 와 같이 쓰는 그 공용 판정이 한다 (D6 규칙 ②).
    - 걸린 후보는 고치지 않는다. 차단이면 통째로 뺀다 — "물놀이터" 를 "얕은 물놀이터" 로 고쳐
      통과시키지 않는다.
    """
    foods = tuple(food for food in candidate.foods if food.strip())
    texts = (candidate.content, *candidate.materials, *foods)
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
    if match_fields((candidate.content,), terms.blocking, cross_spaces=False) or match_fields(
        (*candidate.materials, *foods), terms.blocking_items, cross_spaces=False
    ):
        blocked = True
        hits.append(HEALTH_SAFETY)
    for hit in _food_hits(candidate, foods, months=months, terms=terms):
        blocked = True
        if hit not in hits:
            hits.append(hit)

    allergens = tuple(
        dict.fromkeys(
            (
                *match_fields(texts, terms.allergens, cross_spaces=False),
                *(ALLERGEN_NAMES[code] for code in sorted(menu_codes(foods) if foods else ())),
            )
        )
    )
    return SafetyCheck(
        blocked=blocked,
        hits=tuple(hits),
        warnings=() if blocked else tuple(warnings),
        allergens=allergens,
    )


def _food_hits(
    candidate: ActivityCandidate, foods: tuple[str, ...], *, months: int, terms: SafetyTerms
) -> tuple[str, ...]:
    """음식 칸 · 음식 활동 문장을 Food 판정으로 본다 (#582). 걸린 축 이름들 — 없으면 빈 튜플.

    - 음식 칸의 항목 하나는 그 음식이다(`ITEM`) — "쿠키" 가 밀인 걸 Food 사전이 안다. 놀이 문장의
      알레르기 이름 대조로는 요리 이름(쿠키 · 소시지 · 떡)을 못 막았다.
    - 사전이 모르는 음식(빈 판정)은 "괜찮다" 가 아니라 "확인 못 함" 이다 — 음식 알레르기가 있으면
      뺀다. 없으면 둔다 (#582 합의 10-28).
    - 음식 활동이면 문장 · 준비물도 Food 음식 어휘로 본다(`SENTENCE`). 음식 활동이 아니면 보지
      않는다 — "종이로 쿠키 모양 오려 꾸미기" 는 쿠키를 만지지도 먹지도 않는다.
    - 연령 규칙(꿀 · 떡 …)은 알레르기와 상관없이 본다.
    """
    if not foods and not candidate.involves_food:
        return ()
    stage = stage_of(months)
    hits: list[str] = []

    def judge(text: str, rules: WordRules) -> tuple[FoodVerdict, ...]:
        return check_foods(text, terms.rows, stage=stage, months=months, rules=rules)

    def add(verdicts: Sequence[FoodVerdict]) -> None:
        for verdict in verdicts:
            if verdict.conflicts and HEALTH_SAFETY not in hits:
                hits.append(HEALTH_SAFETY)
            if verdict.age_block is not None and FOOD_AGE not in hits:
                hits.append(FOOD_AGE)

    for item in foods:
        verdicts = judge(item, ITEM)
        if not verdicts and terms.food_allergy and FOOD_UNKNOWN not in hits:
            hits.append(FOOD_UNKNOWN)
        add(verdicts)
    if candidate.involves_food:
        for text in (candidate.content, *candidate.materials):
            add(judge(text, SENTENCE))
    return tuple(hits)


def request_notices(
    texts: Sequence[str], entries: Sequence[SafetyEntry], *, months: int
) -> tuple[Readout, ...]:
    """보호자가 요청에 쓴 음식이 이 아이에게 막혔으면 그런 놀이는 빼고 골랐다는 안내 (#582).

    Food 식단 요청 안내(`food_check.request_notices`)와 같은 방식이다 — 보호자가 쓴 어절만 싣고
    등록한 알레르기 이름은 싣지 않는다. 이 안내는 모델 입력에 가지 않는다. 빼 달라는 말("땅콩
    빼고")에 나온 음식은 안내하지 않는다(`QUESTION` 규칙). 같은 어절은 한 번만 말하고, 연령 규칙은
    어절마다 가장 오래 막는 것 하나로 말한다("꿀떡" 은 떡). 추천 개수와 상관없이 붙는다 — 0개여도
    왜 없는지 이 줄이 말한다.
    """
    stage = stage_of(months)
    notices: list[Readout] = []
    said: set[str] = set()
    for text in texts:
        verdicts = check_foods(text, entries, stage=stage, months=months)
        for verdict in verdicts:
            phrase = phrase_of(text, verdict.word)
            if not verdict.conflicts or phrase in said:
                continue
            said.add(phrase)
            allergy = any(conflict.entry.kind == "allergy" for conflict in verdict.conflicts)
            what = "알레르기" if allergy else "건강 정보"
            notices.append(READOUTS.render(REQUEST_FOOD_CONFLICT, food=phrase, what=what))
        strictest: dict[str, AgeRule] = {}
        for verdict in verdicts:
            if verdict.age_block is None:
                continue
            phrase = phrase_of(text, verdict.word)
            held = strictest.get(phrase)
            if held is None or _lasts(verdict.age_block) > _lasts(held):
                strictest[phrase] = verdict.age_block
        for phrase, rule in strictest.items():
            if phrase in said:
                continue
            said.add(phrase)
            notices.append(
                READOUTS.render(
                    REQUEST_FOOD_AGE_BLOCK,
                    food=phrase,
                    은는=topic_particle(phrase),
                    # 월령 기준이 없는 규칙(날음식 · 카페인)은 모든 아이다(Food when_text)
                    when=(
                        f"{rule.below_month}개월 전에는"
                        if rule.below_month is not None
                        else "아이에게는"
                    ),
                )
            )
    return tuple(notices)


def _lasts(rule: AgeRule) -> float:
    return float("inf") if rule.below_month is None else rule.below_month


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
        for label in non_food_allergies(entries, allergy_book())
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
        usable = for_sentences(Term(key=name, aliases=term.aliases, guards=term.guards))
        if usable is not None:
            result[term.key] = usable
    return result
