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
    """
    raise NotImplementedError("DB 연결 후 구현")
