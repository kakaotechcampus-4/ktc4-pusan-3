"""섭취 확인 답 · 식단 추천 요청 안내 — 음식 판정을 보호자에게 하는 말로 바꾼다.

판정(음식 낱말 찾기 · 등록 알레르기 · 연령 규칙 대조)은 `common/food_words` 에 있다 (#582).
이 모듈은 그 결과를 코드 문구(`food.readout.yaml`)로 만든다. 모델을 부르지 않는다.

- 🚨 못 찾았다는 것은 괜찮다는 뜻이 아니다. 사전에 없는 음식(마라탕)과 여러 재료가 든 음식
  (치킨의 튀김옷)은 낱말이 안 잡히거나 재료를 모른다. 낱말을 하나도 못 찾으면 확인하지 못했다고
  말하고, 찾았어도 재료 확인 안내를 늘 붙인다.
- 질식 주의(`caution`)는 섭취 확인 답에만 한 줄 낸다(#575 PM 결정) — 보호자가 그 음식을 콕 집어
  묻는 곳이라, 말하지 않으면 "알레르기만 못 봤고 나머지는 괜찮다" 로 읽힌다. 추천 목록 · 급식 ·
  요청 안내에는 내지 않는다(10-13 결정 그대로).
- 문구의 조사는 받침에 맞춘다("땅콩소스는 · 견과류 알레르기와"). 한글로 끝나지 않으면 받침을
  몰라 "은(는)" 처럼 괄호로 둔다.
"""

import re
from collections.abc import Sequence
from functools import cache

from app.agents.common.allergy import read_label
from app.agents.common.food_safety import INACTIVE_STATUSES, age_rules_by_key, food_book
from app.agents.common.food_words import FoodVerdict, check_foods, phrase_of, points_at_food
from app.agents.common.readout import Readout
from app.agents.common.reference import AgeRule, allergen_terms
from app.agents.food.readouts import (
    CHECK_AGE_BLOCK,
    CHECK_ASK_FOOD,
    CHECK_CHOKING,
    CHECK_CONFLICT,
    CHECK_INGREDIENTS_CAVEAT,
    CHECK_NO_CONSENT,
    CHECK_NONE_REGISTERED,
    CHECK_NOT_FOUND,
    CHECK_PEANUT_IN_NUTS,
    CHECK_UNKNOWN_FOOD,
    CHECK_UNKNOWN_FOOD_PLAIN,
    MEAL_REQUEST_AGE_BLOCK,
    MEAL_REQUEST_CONFLICT,
    READOUTS,
)
from app.agents.food.store.ports import SafetyEntry
from app.rules.age import Stage
from app.rules.particle import has_final, topic_particle
from app.rules.term_match import TermMatcher, prepare_fields

_PEANUT_CODE = "4"
# 등록 이름 뒤에 보호자가 붙여 적은 꼬리말 — 문구가 "알레르기" 를 다시 붙인다
_LABEL_TAIL = re.compile(r"\s*(?:알레르기|알러지|allergy)\s*$", re.IGNORECASE)


def label_text(entry: SafetyEntry) -> str:
    """등록 행을 문구에 넣는 이름 — 보호자가 적은 그대로. 알레르기면 "알레르기" 를 붙인다."""
    name = _LABEL_TAIL.sub("", entry.label.strip()) or entry.label.strip()
    return f"{name} 알레르기" if entry.kind == "allergy" else name


def _joined(word: str) -> str:
    """받침에 맞는 "과" · "와". 한글로 끝나지 않으면 받침을 몰라 "와(과)" 다."""
    final = has_final(word)
    return "와(과)" if final is None else ("과" if final else "와")


def when_text(rule: AgeRule) -> str:
    """연령 규칙이 막는 때 — 월령 기준이 없는 규칙(날음식 · 카페인 · 술)은 모든 아이다."""
    return f"{rule.below_month}개월 전에는" if rule.below_month is not None else "아이에게는"


def answer_food_check(
    text: str, entries: Sequence[SafetyEntry], *, consent: bool, stage: Stage, months: int
) -> tuple[tuple[Readout, ...], str | None]:
    """섭취 확인의 답 — (화면 문구들, 되묻는 한 줄 또는 None). 전부 코드 문구다.

    🚨 허가 표현(먹여도 돼요 · 괜찮아요)을 쓰지 않는다. 겹치는 걸 못 찾았다는 것은 괜찮다는 뜻이
    아니다 — 사전에 없는 음식과 조합 음식의 재료는 못 본다. 그래서 못 찾음에는 재료 확인을 붙이고,
    음식을 알아보지 못했으면 등록 · 동의와 상관없이 그렇다고 말한다. 문구는 보호자가 쓴 어절로
    말하고, 어절 하나에는 한 가지만 말한다 — 겹치는 어절에 못 찾음을 같이 말하지 않는다.
    """
    verdicts = check_foods(text, entries, stage=stage, months=months)
    if not verdicts and points_at_food(text):
        return (), READOUTS.render(CHECK_ASK_FOOD).body

    active = [entry for entry in entries if entry.status not in INACTIVE_STATUSES]
    food_entries = [entry for entry in active if _food_related(entry)]
    labels = " · ".join(dict.fromkeys(label_text(entry) for entry in food_entries))

    readouts: list[Readout] = []
    said: set[tuple[str, str]] = set()
    for verdict in verdicts:
        phrase = phrase_of(text, verdict.word)
        for conflict in verdict.conflicts:
            label = label_text(conflict.entry)
            if (phrase, label) not in said:
                said.add((phrase, label))
                readouts.append(
                    READOUTS.render(
                        CHECK_CONFLICT,
                        food=phrase,
                        은는=topic_particle(phrase),
                        label=label,
                        와과=_joined(label),
                    )
                )
    for phrase, rule in _strictest_age_blocks(text, verdicts).items():
        readouts.append(
            READOUTS.render(
                CHECK_AGE_BLOCK, food=phrase, 은는=topic_particle(phrase), when=when_text(rule)
            )
        )
    if any(_peanut_in_nuts(verdict) for verdict in verdicts):
        readouts.append(READOUTS.render(CHECK_PEANUT_IN_NUTS))
    for phrase, rule in _cautions(text, verdicts).items():
        readouts.append(
            READOUTS.render(
                CHECK_CHOKING, food=phrase, 은는=topic_particle(phrase), when=when_text(rule)
            )
        )

    if not verdicts:
        # 음식 이름을 알아보지 못했다 — 겹치지 않는다고 말하면 확인한 것처럼 읽힌다
        if consent and food_entries:
            readouts.append(
                READOUTS.render(CHECK_UNKNOWN_FOOD, labels=labels, 와과=_joined(labels))
            )
        else:
            readouts.append(READOUTS.render(CHECK_UNKNOWN_FOOD_PLAIN))
    if not consent:
        readouts.append(READOUTS.render(CHECK_NO_CONSENT))
    elif not food_entries:
        readouts.append(READOUTS.render(CHECK_NONE_REGISTERED))
    elif verdicts:
        blocked = {phrase_of(text, v.word) for v in verdicts if v.blocked}
        unblocked = dict.fromkeys(
            phrase
            for phrase in (phrase_of(text, v.word) for v in verdicts if not v.blocked)
            if phrase not in blocked
        )
        for phrase in unblocked:
            readouts.append(
                READOUTS.render(CHECK_NOT_FOUND, food=phrase, labels=labels, 와과=_joined(labels))
            )
        if unblocked:
            readouts.append(READOUTS.render(CHECK_INGREDIENTS_CAVEAT))
    return tuple(readouts), None


def request_notices(
    texts: Sequence[str], entries: Sequence[SafetyEntry], *, stage: Stage, months: int
) -> tuple[Readout, ...]:
    """식단 추천 요청 글에 막힌 음식이 있으면 빼고 골랐다는 안내. 같은 어절은 한 번만 말한다.

    보호자가 쓴 어절만 싣고 등록한 이름은 싣지 않는다 — 추천 결과에는 등록한 알레르기 이름이
    없어야 한다. 이 안내는 모델 입력에도 가지 않는다. 모델이 후보로 보는 풀은 이미 걸러져 있다.
    빼 달라고 한 음식("견과류 빼고")은 안내하지 않는다.
    """
    notices: list[Readout] = []
    said: set[str] = set()
    for text in texts:
        verdicts = check_foods(text, entries, stage=stage, months=months)
        for verdict in verdicts:
            phrase = phrase_of(text, verdict.word)
            if not verdict.conflicts or phrase in said:
                continue
            said.add(phrase)
            what = (
                "알레르기"
                if any(c.entry.kind == "allergy" for c in verdict.conflicts)
                else "건강 정보"
            )
            notices.append(READOUTS.render(MEAL_REQUEST_CONFLICT, food=phrase, what=what))
        for phrase, rule in _strictest_age_blocks(text, verdicts).items():
            if phrase not in said:
                said.add(phrase)
                notices.append(
                    READOUTS.render(
                        MEAL_REQUEST_AGE_BLOCK,
                        food=phrase,
                        은는=topic_particle(phrase),
                        when=when_text(rule),
                    )
                )
    return tuple(notices)


def _strictest_age_blocks(text: str, verdicts: Sequence[FoodVerdict]) -> dict[str, AgeRule]:
    """어절마다 가장 오래 막는 연령 규칙 하나 — "꿀떡" 은 꿀(12개월)이 아니라 떡(48개월)으로 말한다.
    월령 기준이 없는 규칙(날음식 · 카페인 · 술)이 가장 길다."""
    strictest: dict[str, AgeRule] = {}
    for verdict in verdicts:
        if verdict.age_block is None:
            continue
        phrase = phrase_of(text, verdict.word)
        held = strictest.get(phrase)
        if held is None or _lasts(verdict.age_block) > _lasts(held):
            strictest[phrase] = verdict.age_block
    return strictest


def _cautions(text: str, verdicts: Sequence[FoodVerdict]) -> dict[str, AgeRule]:
    """어절마다 주의 규칙 하나 — 섭취 확인 답에만 쓴다. 같은 어절이 막혔으면 말하지 않는다."""
    rules = age_rules_by_key()
    blocked = {phrase_of(text, v.word) for v in verdicts if v.blocked}
    found: dict[str, AgeRule] = {}
    for verdict in verdicts:
        phrase = phrase_of(text, verdict.word)
        if verdict.caution is not None and phrase not in blocked:
            found.setdefault(phrase, rules[verdict.caution])
    return found


def _lasts(rule: AgeRule) -> float:
    return float("inf") if rule.below_month is None else rule.below_month


def _food_related(entry: SafetyEntry) -> bool:
    """음식을 막는 등록인가 — 알레르기이거나, 질환이어도 막을 식품이 매핑돼 있다(유당불내증).
    천식 · 꽃가루 같은 등록을 "겹치는 걸 못 찾았다" 의 비교 대상으로 말하지 않는다."""
    if entry.kind == "allergy":
        return True
    found = read_label(entry.label, food_book())[0]
    return bool(found.codes or found.terms)


def _peanut_in_nuts(verdict: FoodVerdict) -> bool:
    """땅콩이 견과류와 한 묶음으로 막혀서 겹친 경우 — 낱말과 등록 이름 중 한쪽만 땅콩 이름이다."""
    peanut = _peanut_matcher()
    word_is_peanut = bool(peanut.match(prepare_fields((verdict.word,))))
    return any(
        4 in conflict.codes
        and word_is_peanut != bool(peanut.match(prepare_fields((conflict.entry.label,))))
        for conflict in verdict.conflicts
    )


@cache
def _peanut_matcher() -> TermMatcher:
    return TermMatcher([term for term in allergen_terms() if term.key == _PEANUT_CODE])
