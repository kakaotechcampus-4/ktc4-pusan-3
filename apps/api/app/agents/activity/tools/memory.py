"""놀이 기억 검색 (모델 tool).

지금은 구현부가 주석이라 호출시 NotImplementedError.
"""

from app.agents.activity.context import ActivityContext
from app.agents.activity.result import ToolResult
from app.agents.activity.schemas.memory import SearchActivityMemoryArgs


async def search_activity_memory(
    context: ActivityContext, args: SearchActivityMemoryArgs
) -> ToolResult:
    """아이의 최근 놀이 기록과 놀이 관심을 근거 순서대로 돌려준다.

    DB 연결 후:
    - 관찰은 최근 14일(`OBSERVATION_WINDOW_DAYS`)을 읽는다. `affinity_id=NULL` 인 관찰도 읽는다.
    - `gating.reads_affinity(gate)` 가 참일 때만 profile_affinity(domain=activity)를 읽는다.
      18개월 미만은 affinity 가 구조적으로 0행이라 조회를 건너뛴다.
    - `common/evidence.rank_evidence()` 로 줄 세운다. 순서를 모델에게 맡기지 않는다.
      기피(−1)도 근거로 남는다 — 후보에서 지우는 것은 안전 필터뿐이다 (D4).
    - 상위 10개만 돌려주고 `context.state.seen_evidence` 에 id → (근거, updated_at) 을 적는다.
      출력 tool 이 이 표에 없는 id 를 인용하면 후보를 거절한다.
    - 결과에는 id, tier, label, polarity, 날짜만 싣는다. raw_text 는 싣지 않는다.
    - 0행이면 NO_RECORDS. 호출부는 일반 추천으로 간다 — 추천을 질문으로 대신하지 않는다.
    """
    raise NotImplementedError("DB 연결 후 구현")
