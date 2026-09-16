"""영아기 tool. 이유식 단계 보조 1개만 registry 에 올린다.

food.md 의 영아기 "분유/수유 기록" · "식재료 도입 기록" 은 쓰기 tool 이다.
기록은 지금 Memory 가 한다 — 쓰기 통로가 둘이면 혼합형 입력에서 같은 기록이 두 번 저장된다.
그래서 아래 두 함수는 스텁으로만 두고 registry 에 올리지 않는다 (열린 결정 2).
"""

from app.agents.food.context import FoodContext
from app.agents.food.result import ToolResult
from app.agents.food.schemas.infant import GuideWeaningStageArgs


async def guide_weaning_stage(context: FoodContext, args: GuideWeaningStageArgs) -> ToolResult:
    """개월 수에 맞는 이유식 단계와 참고 기준을 가져온다.

    DB 연결 후:
    - context.profile.birth_date로 개월 수를 계산하고 이유식 단계를 정한다.
    단계 경계는 참고 자료의 기준을 사용한다.
    - 단계별 입자·질감과 권장 식재료 정보를 함께 가져온다. 출처는 정해야 함
    - observation_food에서 이미 먹어본 식재료를 찾아 함께 반환한다.
    조회 방식은 search_food_memory와 동일하게 맞춘다.
    - 단계나 식재료 기준은 LLM이 임의로 만들지 않는다.
    참고 자료에 없는 내용은 "자료 없음"으로 처리한다.
    """
    raise NotImplementedError("DB 연결 후 구현")


# ── TODO: 등록 보류 ─────────────────────────────────────
async def record_feeding(context: FoodContext, args: object) -> ToolResult:
    """분유/수유 기록은 등록 보류.
    기록은 Memory가 observation_food로 한다.
    수유량을 담을 필드를 따로 두지 않고 observation_food.amount에 ml 단위로.
    """
    raise NotImplementedError("등록 보류")


async def record_ingredient_introduction(context: FoodContext, args: object) -> ToolResult:
    """식재료 도입 기록 등록 보류.
    "처음 먹여 봤다"는 관찰이라 Memory가 기록한다. 도입 단계를 담을 필드는 함께 정한다.
    """
    raise NotImplementedError("등록 보류")
