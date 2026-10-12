"""Activity 의 코드 상수 문구. 문구는 `activity.readout.yaml` 에 있고 여기는 키만 둔다."""

from pathlib import Path

from app.agents.common.readout import ReadoutCatalog

# 알레르기 조회에 실패했다 (D7)
BLOCKED_SAFETY = "blocked.safety"
# 식품 사전에 없는 알레르기 이름(꽃가루 · 라텍스)이 있다 (D7)
CAUTION_NON_FOOD_ALLERGY = "caution.non_food_allergy"

READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("activity.readout.yaml"))
