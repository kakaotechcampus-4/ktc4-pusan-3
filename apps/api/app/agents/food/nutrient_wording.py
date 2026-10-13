"""영양소 분석 문장의 규칙 — 모델이 쓴 문장 검사 · 코드 문장 틀 (#357 리뷰).

문장은 모델이 쓴다. 판정 · 식품군 안내 · 알레르기 식품 빼기는 코드가 이미 했고, 모델은 그 결과를
보호자에게 읽히게 옮긴다. 여기서는 그 문장이 화면에 나가도 되는지만 본다 —
주어가 **먹은 식사 · 기록**인가, 수치 · 진단 · 처방 · 아이 몸 상태로 읽히는 말이 없는가.

목록으로 보는 검사라 다 막지 못한다. 그래서 주어(식사 · 기록)를 **요구**하는 쪽으로 좁히고,
걸리면 모델이 다시 쓰거나 `run()` 이 코드 문장 틀로 바꾼다 — 오탐은 덜 자연스러운 문장으로만
틀린다.
"""

import re
import unicodedata
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from app.agents.common.readout import Readout
from app.agents.food.readouts import (
    NUTRIENT_BAND_ALL_OK,
    NUTRIENT_BAND_HIGH,
    NUTRIENT_BAND_HIGH_ACTION,
    NUTRIENT_BAND_LOW,
    NUTRIENT_BAND_LOW_ACTION,
    NUTRIENT_INSUFFICIENT,
    NUTRIENT_UNSCORED,
    READOUTS,
)

if TYPE_CHECKING:
    # nutrition_eval 이 FoodContext 를 거쳐 무겁게 import 된다 — 타입에만 쓴다
    from app.agents.food.nutrition_eval import NutrientVerdict

WordingProblem = Literal["number", "prescription", "body", "child_subject", "no_meal_subject"]

# 숫자 + 단위. "9건" 같은 기록 건수는 단위가 아니라 통과한다
_NUMBER = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|퍼센트|프로|mg|㎎|밀리그램|ml|㎖|밀리리터|g|그램|kcal|㎉|킬로칼로리|칼로리|배)",
    re.IGNORECASE,
)
# 수를 한글로 쓴 배수 · 비율("두 배" · "이십 프로"). 수 앞이 한글이면 낱말의 일부라 보지 않고
# ("식사 배달"), 과일 "배" · "배추" 는 수 다음에 오지 않아 걸리지 않는다
_KOREAN_RATIO = re.compile(
    r"(?<![가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇|여러|[일이삼사오육칠팔구십백]+)"
    r"\s?(?:배(?![추달])|프로(?![그바틴필])|퍼센트)"
)
# 숫자를 한글로 써도("백이십 밀리그램") 단위어는 남는다. "절반" 도 양을 말하는 비율이다
_UNIT_WORDS = (
    "퍼센트",
    "밀리그램",
    "밀리그람",
    "밀리리터",
    "킬로칼로리",
    "칼로리",
    "그램",
    "절반",
)
# 진단 · 처방 · 먹일 음식 제안
_PRESCRIPTION = (
    "의심",
    "결핍",
    "영양제",
    "보충제",
    "처방",
    "복용",
    "늘려",
    "드세요",
    "먹이세요",
    "챙겨",
)
# 아이 몸 상태 · 평가 · 또래 비교 · 병원 권유로 읽히는 말.
# "적은 편 · 많은 편 · 적당" 은 식사를 말하는 표현이라 쓴다
_BODY = (
    "몸",
    "체내",
    "혈중",
    "수치",
    "상태",
    "건강",
    "부족",
    "모자라",
    "모자란",
    "모자르",
    "과다",
    "과잉",
    "빈혈",
    "증상",
    "발달",
    "성장",
    "영양실조",
    "위험",
    "걱정",
    "정상",
    # 건강 결과를 예언하거나(뼈 · 치아 · 면역 …) 또래와 견주거나 병원을 권하는 말 — 진단처럼 읽힌다.
    # "키" 는 "키위" 에 걸려서 넣지 않는다
    "뼈",
    "치아",
    "충치",
    "면역",
    "비만",
    "혈압",
    "자랄",
    "또래",
    "평균",
    "병원",
    "소아과",
    "상담",
)
# "아이 칼슘" · "아이의 철분" · "아기가 단백질" — 아이가 영양소의 주인으로 읽힌다
_CHILD_SUBJECT = re.compile(
    r"(아이|아기)\S{0,3}\s*(단백질|칼슘|철분|철|탄수화물|지방|나트륨|당류|당분|포화지방|영양)"
)
# 주어가 먹은 식사 · 기록이라는 표시. 하나는 있어야 한다
_MEAL_SUBJECT = ("식사", "식단", "먹은", "기록")

WORDING_MESSAGES: dict[WordingProblem, str] = {
    "number": "findings 에 수치 · 단위를 쓰지 않는다. 구간(적은 편 · 적당 · 많은 편)으로만 말한다.",
    "prescription": "findings 에 진단 · 처방을 쓰지 않는다. 음식은 food_group_actions 의 문구를 "
    "그대로 쓸 때만 말하고, 그 밖의 음식 제안(늘려 · 챙겨 · 드세요 …)은 하지 않는다.",
    "body": "findings 에 아이 몸 상태로 읽히는 말(몸 · 부족 · 모자라 · 상태 …)을 쓰지 않는다. "
    "먹은 식사를 말한다.",
    "child_subject": "findings 의 주어를 아이로 쓰지 않는다. "
    "'기록된 식사를 보면 …' 처럼 식사를 주어로 쓴다.",
    "no_meal_subject": "findings 는 먹은 식사를 주어로 쓴다. '기록된 식사를 보면 …' · "
    "'최근 식단에서 …' 처럼 쓴다.",
}


def wording_problem(finding: str) -> WordingProblem | None:
    """화면에 나가면 안 되는 이유. 없으면 None. 검사 순서는 위 상수 순서다."""
    text = unicodedata.normalize("NFKC", finding)
    if (
        _NUMBER.search(text)
        or _KOREAN_RATIO.search(text)
        or any(word in text for word in _UNIT_WORDS)
    ):
        return "number"
    if any(word in text for word in _PRESCRIPTION):
        return "prescription"
    if any(word in text for word in _BODY):
        return "body"
    if _CHILD_SUBJECT.search(text):
        return "child_subject"
    if not any(word in text for word in _MEAL_SUBJECT):
        return "no_meal_subject"
    return None


# 화면에 쓰는 영양소 이름. 판정 항목(nutrition_eval 의 refs_for, energy 제외)을 다 덮는다
NUTRIENT_LABELS: dict[str, str] = {
    "protein": "단백질",
    "carbohydrate": "탄수화물",
    "fat": "지방",
    "calcium": "칼슘",
    "iron": "철분",
    "sodium": "나트륨",
    "sugars": "당류",
    "saturated_fat": "포화지방",
}

_BAND_KEYS: dict[tuple[str, bool], str] = {
    ("low", False): NUTRIENT_BAND_LOW,
    ("low", True): NUTRIENT_BAND_LOW_ACTION,
    ("high", False): NUTRIENT_BAND_HIGH,
    ("high", True): NUTRIENT_BAND_HIGH_ACTION,
}


def fallback_readouts(
    verdict: "NutrientVerdict", actions: Mapping[str, str]
) -> tuple[Readout, ...]:
    """모델이 출력 tool 을 못 끝냈을 때 낼 문장. 판정 · 안내는 코드가 이미 계산한 값이다.

    구간이 low · high 인 항목만 한 줄씩, 판정 순서대로 말한다. 안내 식품이 알레르기로 다 빠진
    항목(actions 에 없음)은 구간만 말한다 — 다른 급원 식품을 지어내지 않는다(#321 합의 6번).
    """
    if verdict.insufficient:
        return (READOUTS.render(NUTRIENT_INSUFFICIENT, recorded=verdict.records),)
    if not verdict.bands:
        # 기록은 충분한데 모든 항목이 빈 칸이었다 — 아무것도 안 봤는데 "고르게" 라고 하지 않는다
        return (READOUTS.render(NUTRIENT_UNSCORED, recorded=verdict.records),)
    lines = [
        READOUTS.render(
            _BAND_KEYS[(band, nutrient in actions)],
            recorded=verdict.records,
            nutrient=NUTRIENT_LABELS[nutrient],
            action=actions.get(nutrient, ""),
        ).body
        for nutrient, band in verdict.bands.items()
        if band != "ok"
    ]
    if not lines:
        return (READOUTS.render(NUTRIENT_BAND_ALL_OK, recorded=verdict.records),)
    return (Readout(kind="nutrient_analysis", body="\n".join(lines), authored_by="code"),)
