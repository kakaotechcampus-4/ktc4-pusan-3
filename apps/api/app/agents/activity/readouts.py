"""Activity 의 코드 상수 문구. 문구는 `activity.readout.yaml` 에 있고 여기는 키만 둔다."""

from pathlib import Path

from app.agents.common.readout import ReadoutCatalog

# 알레르기 조회에 실패했다 (D7)
BLOCKED_SAFETY = "blocked.safety"
# 식품 사전에 없는 알레르기 이름(꽃가루 · 라텍스)이 있다 (D7)
CAUTION_NON_FOOD_ALLERGY = "caution.non_food_allergy"
# 장소가 붙은 추천의 확인 문구 — 기준일자가 있는 출처 · 없는 출처 (#516)
PLACE_NOTICE = "place.notice"
PLACE_NOTICE_UNDATED = "place.notice_undated"
PLACE_NOTICE_FACILITY = "place.notice_facility"
# 행사(TourAPI)가 붙은 추천의 확인 문구. 출처를 밝히고 일정 · 관람 연령을 확인해 달라고 한다
PLACE_NOTICE_EVENT = "place.notice_event"
# 보호자가 말한 지역을 못 찾아 장소 없이 골랐다 (`areas.py`)
PLACE_AREA_UNKNOWN = "place.area_unknown"
# 앞에서 보여 준 행사뿐이라 새 행사가 없었다 (#852)
EVENTS_NO_NEW = "events.no_new"
# 보호자가 요청에 쓴 음식이 막혀 그런 놀이는 빼고 골랐다 — 알레르기 · 연령 (#582)
REQUEST_FOOD_CONFLICT = "request.food_conflict"
REQUEST_FOOD_AGE_BLOCK = "request.food_age_block"

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("activity.readout.yaml"))
