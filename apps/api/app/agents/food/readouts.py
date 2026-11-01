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

# 식단 추천 후보 풀
POOL_EMPTY = "pool.empty"
GENERAL_REASON_TODDLER = "general.reason.toddler"
GENERAL_REASON_WEANING = "general.reason.weaning"
FOLLOWUP_PREFERENCE = "followup.preference"
UNSUPPORTED_MILK_AMOUNT = "unsupported.milk_amount"
UNSUPPORTED_WEANING_OVER12 = "unsupported.weaning_over12"
UNSUPPORTED_WEIGHT_DIET = "unsupported.weight_diet"

# 영양소 분석
NUTRIENT_INSUFFICIENT = "nutrient.insufficient"
NUTRIENT_TODAY_EMPTY = "nutrient.today_empty"
NUTRIENT_DAYCARE_AMOUNT_UNKNOWN = "nutrient.daycare_amount_unknown"
# 1차 출력 — 기록된 날 중 식품군이 나온 날 수. 영양소 분석 문장은 이것뿐이다(N-17 · N-18)
NUTRIENT_APPEARANCE = "nutrient.appearance"
# 구간 문장 틀 (#357 리뷰). 1차 출력에서는 화면에 내지 않는다 — 구간을 다시 낼 때 쓴다. 주어는 늘
# 기록된 식사다
NUTRIENT_BAND_LOW = "nutrient.band.low"
NUTRIENT_BAND_LOW_ACTION = "nutrient.band.low_action"
NUTRIENT_BAND_HIGH = "nutrient.band.high"
NUTRIENT_BAND_HIGH_ACTION = "nutrient.band.high_action"
NUTRIENT_BAND_ALL_OK = "nutrient.band.all_ok"
# 기록은 충분한데 모든 항목이 빈 칸이라 구간이 하나도 안 나왔을 때 — "고르게" 라고 말하지 않는다
NUTRIENT_UNSCORED = "nutrient.unscored"

# 섭취 확인(food_check) — 코드가 등록 알레르기 · 연령 규칙으로 답한다. 허가 표현은 쓰지 않는다
CHECK_BLOCKED_SAFETY = "check.blocked_safety"  # 안전 조회 실패 — 답하지 않는다
CHECK_CONFLICT = "check.conflict"
CHECK_PEANUT_IN_NUTS = "check.peanut_in_nuts"  # 땅콩과 견과류가 한 묶음으로 막힌다는 설명
CHECK_AGE_BLOCK = "check.age_block"
# 질식 주의 — 섭취 확인 답에만 낸다(#575 PM 결정). 추천 목록 · 급식에는 내지 않는다(10-13).
# 문구에 "통째로" 를 넣지 않는다 — 땅콩버터는 통째가 아니라 끈적여서 걸린다
CHECK_CHOKING = "check.choking"
CHECK_NOT_FOUND = "check.not_found"
CHECK_UNKNOWN_FOOD = "check.unknown_food"  # 음식 이름을 알아보지 못했다 — 연령 · 등록 대조 못 함
CHECK_UNKNOWN_FOOD_PLAIN = "check.unknown_food_plain"  # 대조할 등록이 없거나 동의가 없다
CHECK_INGREDIENTS_CAVEAT = "check.ingredients_caveat"  # 못 찾음에는 늘 붙는다
CHECK_NONE_REGISTERED = "check.none_registered"
CHECK_NO_CONSENT = "check.no_consent"
CHECK_ASK_FOOD = "check.ask_food"  # 음식 이름이 없을 때 되묻는 한 줄

# 식단 추천 요청 글에 알레르기 · 연령 금지 음식이 있을 때의 안내. 등록한 이름은 싣지 않는다
MEAL_REQUEST_CONFLICT = "meal.request_conflict"
MEAL_REQUEST_AGE_BLOCK = "meal.request_age_block"

# 급식 조회 · 갱신 · 삭제
DAYCARE_ASK_SUBSTITUTE = "daycare.ask_substitute"
DAYCARE_UNCHECKED = "daycare.unchecked"
DAYCARE_REFERENCE_MENU = "daycare.reference_menu"
DAYCARE_UPDATED = "daycare.updated"
DAYCARE_AMOUNT_UPDATED = "daycare.amount_updated"  # 메뉴는 그대로 두고 양만 반영했을 때
DAYCARE_ABSENT = "daycare.absent"
DAYCARE_AMBIGUOUS = "daycare.ambiguous"
DAYCARE_SLOT_AMBIGUOUS = "daycare.slot_ambiguous"
DAYCARE_ALLERGY_CONFLICT = "daycare.allergy_conflict"
DAYCARE_OUT_OF_RANGE = "daycare.out_of_range"
DAYCARE_NOT_FOUND = "daycare.not_found"
DAYCARE_REPLACED_UNKNOWN = "daycare.replaced_unknown"
DAYCARE_UNCHANGED = "daycare.unchanged"

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("food.readout.yaml"))
