"""Health 의 코드 상수 문구. 문구는 `health.readout.yaml` 에 있고 여기는 키만 둔다.

문구는 Health_Tool_명세.md §4 와 글자 단위로 같다
(tests/unit/agents/health/test_health_readouts.py).
"""

from pathlib import Path

from app.agents.common.readout import ReadoutCatalog

# 증상 반복 안내 (hook)
SYMPTOM_REPEAT = "symptom.repeat"
SYMPTOM_REPEAT_CONDITION = "symptom.repeat_condition"  # 등록된 만성질환과 맞을 때

# 검진 · 접종
VACCINE_DISCLAIMER = "vaccine.disclaimer"
VACCINE_DUE = "vaccine.due"
VACCINE_NONE_NOW = "vaccine.none_now"
VACCINE_NEXT = "vaccine.next"  # 지금 뒤에 처음 열리는 접종 칸
CHECKUP_CURRENT = "checkup.current"
CHECKUP_NEXT = "checkup.next"
CHECKUP_ORAL = "checkup.oral"  # 일반검진 기간 안의 구강검진
CHECKUP_ORAL_ONLY = "checkup.oral_only"  # 일반검진 기간이 아닌데 구강검진 기간일 때
CHECKUP_AGED_OUT = "checkup.aged_out"  # 72개월 이상
CHECKUP_CONTEXT_FOOTER = "checkup.context_footer"

# 복약
MED_CONFIRM = "med.confirm"
MED_UPDATE_CONFIRM = "med.update_confirm"
MED_NOTICE = "med.notice"
MED_STOP_CONFIRM = "med.stop_confirm"
MED_STOPPED = "med.stopped"
MED_STOP_KEPT = "med.stop_kept"
ASK_DOSE_TIMING = "ask.dose_timing"
ASK_WHICH_MED = "ask.which_med"
ASK_DOSE_CLOCK = "ask.dose_clock"  # 시각이 모호하다 — 오전 · 오후까지 묻는다
ASK_DOSE_SAME_TIME = "ask.dose_same_time"  # 다른 회차가 같은 시각 — 하나씩 묻는다
MED_WHICH_UNCLEAR = "med.which_unclear"  # 되묻기 답을 못 가렸다 — 쓰지 않는다
MED_TIMING_UNCLEAR = "med.timing_unclear"
MED_NO_CHANGE = "med.no_change"  # 수정인데 지금 값과 같다
MED_PERIOD_UNCLEAR = "med.period_unclear"  # 수정할 기간을 못 읽었다
MED_ONE_AT_A_TIME = "med.one_at_a_time"  # 되묻기는 한 번에 하나
DOSE_STATUS = "dose.status"
DOSE_CHECK_ON_SCREEN = "dose.check_on_screen"

# 열 · 응급
FEVER_NO_RECORD = "fever.no_record"
EMERGENCY = "emergency"

# 전달 서류
NOTICE_SIGNATURE = "notice.signature"
NOTICE_NO_ACTIVE_MED = "notice.no_active_med"
NOTICE_HEADER = "notice.header"
NOTICE_LINE_TITLE = "notice.line_title"
NOTICE_LINE_TIME = "notice.line_time"
NOTICE_LINE_STORAGE = "notice.line_storage"
NOTICE_LINE_INDICATION = "notice.line_indication"
NOTICE_LINE_NOTE = "notice.line_note"
CARD_HEADER = "card.header"
CARD_CHILD = "card.child"
CARD_ALLERGY = "card.allergy"
CARD_CHRONIC = "card.chronic"
CARD_MEDS = "card.meds"
CARD_EMPTY = "card.empty"  # 등록한 것이 없다
CARD_SAFETY_UNAVAILABLE = "card.safety_unavailable"  # 조회 실패 — "없음" 이 아니다
PRESCRIPTION_LOW_CONFIDENCE = "prescription.low_confidence"
PRESCRIPTION_NOT_FOUND = "prescription.not_found"

# 닫힘 · 기록 없음 · 조회 실패
BLOCKED_CONSENT = "blocked.consent"  # 건강정보 동의 없음 — 전 라벨
VISIT_NO_RECORD = "visit.no_record"
PLACE_UNAVAILABLE = "place.unavailable"
SAFETY_UNAVAILABLE = "safety.unavailable"

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("health.readout.yaml"))
