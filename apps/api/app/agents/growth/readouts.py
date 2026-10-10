"""Growth 의 코드 상수 문구. 문구는 `growth.readout.yaml` 에 있고 여기는 키만 둔다.

문구는 Growth_Tool_명세.md §4 와 글자 단위로 같다
(tests/unit/agents/growth/test_growth_readouts.py).
"""

from pathlib import Path

from app.agents.common.readout import ReadoutCatalog

# 게이트 · 라벨 규칙이 닫는다 (모델 0회)
CLOSED_INFANT_LEARNING = "closed.infant_learning"  # 교육 + 0–11개월
CLOSED_HABIT_UNDER36 = "closed.habit_under36"  # 습관 교정 + 36개월 미만
CLOSED_MANNER_UNDER24 = "closed.manner_under24"  # 예절 + 24개월 미만
CLOSED_BOOK_API = "closed.book_api"  # 도서 API 장애
CLOSED_CONSENT = "closed.consent"  # growth_review + 건강정보 동의 없음
CLOSED_MEDICAL_ROUTINE = "closed.medical_routine"  # 약 · 처치를 루틴으로 코칭하려는 요청
CLOSED_SYMPTOM_HABIT = "closed.symptom_habit"  # 증상처럼 보이는 행동을 습관으로 교정하려는 요청
BLOCKED_SAFETY = "blocked.safety"  # 교육 + 알레르기 조회 실패 (Activity 와 같은 문구)

# 성장 추이
DELTA_NEED_MORE = "delta.need_more"
DELTA_ONE_METRIC = "delta.one_metric"
DELTA_POSTURE_HINT = "delta.posture_hint"
DELTA_CHECKUP_HINT = "delta.checkup_hint"

# 안전 · 근거 안내
CAUTION_NON_FOOD_ALLERGY = "caution.non_food_allergy"  # 식품 사전에 없는 알레르기 (꽃가루 · 라텍스)
DOC_NO_ROW = "doc.no_row"
RHYTHM_NO_ROW = "rhythm.no_row"
NEXT_STEP_CHAIN_END = "next_step.chain_end"

# 되묻기 — needs_observation 한 줄
ASK_HABIT_TRIGGER = "ask.habit_trigger"
ASK_HABIT_CURRENT = "ask.habit_current"
ASK_ROUTINE_CURRENT = "ask.routine_current"

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("growth.readout.yaml"))
