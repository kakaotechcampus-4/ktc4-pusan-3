"""식단 추천의 출력(suggestion form)"""

from app.agents.food.context import FoodContext
from app.agents.food.result import ToolResult
from app.agents.food.schemas.recommend import ProposeMealCandidatesArgs


async def propose_meal_candidates(
    context: FoodContext, args: ProposeMealCandidatesArgs
) -> ToolResult:
    """식사나 간식 추천 후보를 검증하고 최종 결과 형태로 변환한다.

    DB 연결 후:
    - 사전에 조회한 안전 정보를 기준으로 각 후보의 ingredients를 검사한다.
    주의 식품이 포함된 후보는 제외하고, 모든 후보가 제외되면 빈 결과를 반환한다.
    - evidence가 이번 실행에서 실제로 조회한 (kind, id)인지 확인한다.
    유효하지 않은 ref는 제거한다.
    - 유효한 evidence가 없으면 일반 추천으로 처리하고, 또래 기준 추천임을 표시한다.
    개인화 추천인데 evidence가 하나도 남지 않는 경우는 오류로 본다.
    - 오래된 선호 정보만 근거로 사용된 후보는 일반 추천으로 처리한다.
    - 최종 결과는 suggestion 형식으로 변환한다.
    agent, content, reason, source_refs, status를 채우고 expires_at은 백엔드에서 설정한다.
    ingredients는 suggestion 결과에 포함하지 않는다.
    - 이 tool에서는 결과만 반환하며 저장, 발송, 예약은 처리하지 않는다.

    TODO: suggestion.allergens · items · ingredient_checks 를 채운다 (#264 · #267).
    - allergens 는 filter_food_safety 의 `allergens[menu_key]` 를 그대로 쓴다(#264 PM 결정) —
      들어 있거나 제품에 따라 들어 있을 수 있는 19종 이름과 19종 밖 이름이다. 카탈로그
      allergen_codes 나 menu_codes 로 채우면 막는 판단과 묻는 목록이 어긋난다
    - needs_check 행은 `checks[menu_key]` 를 ingredient_checks 로 실어 "확인 필요" 로 내보낸다.
      실을 칸(후속 이슈)이 없으면 내보내지 않는다
    - items 는 일정 준비물로 쓸 재료 이름(된장 · 두부). 위의 "ingredients는 suggestion 결과에
      포함하지 않는다" 와 어긋나니 구현할 때 같이 정리한다. 사전에 없는 성분은 필터도 채택
      질문도 놓치므로, 마지막에 보호자가 재료를 보고 알아챌 수 있게 재료를 보여 주는 쪽으로
      정리한다(출시 전 고려)
    """
    raise NotImplementedError("DB 연결 후 구현")
