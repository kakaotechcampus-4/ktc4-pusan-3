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
CHECKUP_AGED_OUT = "checkup.aged_out"  # 72개월 이상
CHECKUP_CONTEXT_FOOTER = "checkup.context_footer"

# 복약
MED_CONFIRM = "med.confirm"
MED_UPDATE_CONFIRM = "med.update_confirm"
MED_NOTICE = "med.notice"
MED_STOP_CONFIRM = "med.stop_confirm"
MED_STOPPED = "med.stopped"
MED_STOP_KEPT = "med.stop_kept"
DOSE_STATUS = "dose.status"
DOSE_LOGGED = "dose.logged"
DOSE_SKIPPED = "dose.skipped"
DOSE_DUPLICATE = "dose.duplicate"

# 열 · 응급
FEVER_NO_RECORD = "fever.no_record"
EMERGENCY = "emergency"

# 전달 서류
NOTICE_SIGNATURE = "notice.signature"
NOTICE_NO_ACTIVE_MED = "notice.no_active_med"
PRESCRIPTION_LOW_CONFIDENCE = "prescription.low_confidence"
PRESCRIPTION_NOT_FOUND = "prescription.not_found"

# 닫힘 · 기록 없음 · 조회 실패
BLOCKED_CONSENT = "blocked.consent"  # 건강정보 동의 없음 — 전 라벨
VISIT_NO_RECORD = "visit.no_record"
PLACE_UNAVAILABLE = "place.unavailable"
SAFETY_UNAVAILABLE = "safety.unavailable"

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("health.readout.yaml"))
