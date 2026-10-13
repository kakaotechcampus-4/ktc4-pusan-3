"""Food 의 코드 상수 문구. 문구는 `food.readout.yaml` 에 있고 여기는 키만 둔다.

문구는 Food_Tool_명세.md §4 와 글자 단위로 같다 (tests/unit/agents/food/test_food_readouts.py).
"""

from pathlib import Path

from app.agents.common.readout import ReadoutCatalog

# 게이트 · 라벨 규칙이 닫는다 (모델 0회)
UNSUPPORTED_MILK_MEAL = "unsupported.milk_meal"  # 식단 추천 + 수유기(0–3개월)
UNSUPPORTED_INFANT_NUTRIENT = "unsupported.infant_nutrient"  # 영양소 분석 + 0–11개월
BLOCKED_SAFETY = "blocked.safety"  # 안전 조회 실패 — 식단 추천 · 영양소 분석
CLOSED_NO_DAYCARE = "closed.no_daycare"  # daycare_meal 라벨인데 급식 행이 없음
NOT_READY_MEAL = "not_ready.meal"  # 식단 추천 — 후보 풀 · 기록 조회가 붙기 전 (#357 리뷰 4-②)
NOT_READY_DAYCARE = "not_ready.daycare"  # 급식 조회 · 갱신 · 삭제 — tool 이 붙기 전

# 식단 추천 후보 풀
POOL_EMPTY = "pool.empty"
GENERAL_REASON_TODDLER = "general.reason.toddler"
GENERAL_REASON_WEANING = "general.reason.weaning"
FOLLOWUP_PREFERENCE = "followup.preference"
UNSUPPORTED_MILK_AMOUNT = "unsupported.milk_amount"
UNSUPPORTED_WEANING_OVER12 = "unsupported.weaning_over12"
UNSUPPORTED_WEIGHT_DIET = "unsupported.weight_diet"
GUIDE_CHOKING_CAUTION = "guide.choking_caution"

# 영양소 분석
NUTRIENT_INSUFFICIENT = "nutrient.insufficient"
NUTRIENT_TODAY_EMPTY = "nutrient.today_empty"
NUTRIENT_DAYCARE_AMOUNT_UNKNOWN = "nutrient.daycare_amount_unknown"
# 모델이 출력 tool 을 못 끝냈을 때 run() 이 대신 내는 문장 틀 (#357 리뷰). 주어는 늘 기록된 식사다
NUTRIENT_BAND_LOW = "nutrient.band.low"
NUTRIENT_BAND_LOW_ACTION = "nutrient.band.low_action"
NUTRIENT_BAND_HIGH = "nutrient.band.high"
NUTRIENT_BAND_HIGH_ACTION = "nutrient.band.high_action"
NUTRIENT_BAND_ALL_OK = "nutrient.band.all_ok"

# 급식 조회 · 갱신 · 삭제
DAYCARE_ASK_SUBSTITUTE = "daycare.ask_substitute"
DAYCARE_UNCHECKED = "daycare.unchecked"
DAYCARE_REFERENCE_MENU = "daycare.reference_menu"
DAYCARE_UPDATED = "daycare.updated"
DAYCARE_ABSENT = "daycare.absent"
DAYCARE_AMBIGUOUS = "daycare.ambiguous"
DAYCARE_SLOT_AMBIGUOUS = "daycare.slot_ambiguous"
DAYCARE_ALLERGY_CONFLICT = "daycare.allergy_conflict"
DAYCARE_OUT_OF_RANGE = "daycare.out_of_range"
DAYCARE_NOT_FOUND = "daycare.not_found"
DAYCARE_REPLACED_UNKNOWN = "daycare.replaced_unknown"

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("food.readout.yaml"))
