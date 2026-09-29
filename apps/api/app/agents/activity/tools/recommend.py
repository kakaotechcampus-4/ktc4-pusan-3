"""놀이 추천의 출력 (모델 tool).

지금은 구현부가 주석이라 호출시 NotImplementedError.
"""

from app.agents.activity.context import ActivityContext
from app.agents.activity.result import ToolResult
from app.agents.activity.schemas.recommend import ProposeActivityCandidatesArgs


async def propose_activity_candidates(
    context: ActivityContext, args: ProposeActivityCandidatesArgs
) -> ToolResult:
    """놀이 후보 3개를 검증하고 suggestion 초안으로 바꾼다.

    검증 순서 (3-3 — 순서가 결과를 바꾼다):
    1. 안전 필터 `filter_activity_safety` — 걸린 후보는 풀에서 뺀다. 고쳐서 통과시키지 않는다.
    2. 금지 표현 — content · why_this · why_now · note.
    3. Activity 출력 검증 — 기피 근거와 merge_key 가 같은 후보 거절 · note 의 반복 표현은
       티어 1 근거일 때만 · 최근 창 안에 한 활동 거절 · 0–17개월은 together 만 ·
       evidence id 가 `context.state.seen_evidence` 안에 있는가.
    4. `common/suggestion.build()` — EvidencePick 을 EvidenceCitation 으로 바꿔 넘긴다.
       kind 는 여기서 정해진다. 모델이 고르지 않는다.
    5. `check_count()` — 안전 필터로 모자라면 재호출 1회. 재호출 때 걸러진 사유는 주지 않는다.

    저장 · 발송 · 예약은 하지 않는다. status 와 expires_at 은 인자에 없다.
    """
    raise NotImplementedError("출력 검증 구현 후")
