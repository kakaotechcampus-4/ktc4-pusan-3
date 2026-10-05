"""놀이 추천의 출력 (모델 tool)."""

from datetime import timedelta

from app.agents.activity.context import ActivityContext
from app.agents.activity.result import ToolResult, fail, ok
from app.agents.activity.review import error_code, explain, review_candidates
from app.agents.activity.rules import DUPLICATE_WINDOW_DAYS
from app.agents.activity.schemas.recommend import ProposeActivityCandidatesArgs
from app.agents.common.suggestion import check_count

_NAME = "propose_activity_candidates"


async def propose_activity_candidates(
    context: ActivityContext, args: ProposeActivityCandidatesArgs
) -> ToolResult:
    """놀이 후보 3개를 검증하고 suggestion 초안으로 바꾼다 (설계 3-3).

    - 개수(정확히 3개)는 인자 검증이 이미 막았다.
    - 걸린 후보가 하나라도 있으면 사유와 함께 돌려준다. 모델은 고쳐서 3개를 다시 낸다 —
      같은 진입 안의 루프라 model_calls 가 늘지 않는다. 오류 코드는 지어낸 근거 id 가 있으면
      EVIDENCE_REQUIRED, 아니면 CANDIDATE_REJECTED (`review.error_code`).
    - 통과하면 `context.state.suggestions` 에 담는다. 저장 · 발송 · 예약은 하지 않는다.
      status 와 expires_at 은 인자에 없다.

    안전 필터는 아직 없다 (위험 용어 사전 PR). 붙으면 걸린 후보는 거절이 아니라 풀에서 빠지고,
    3개 미만이면 Agent 가 재호출 1회를 한다 — 그때는 걸린 사유를 모델에게 주지 않는다.

    TODO: suggestion.allergens · items 를 채운다 (10/4 스키마 이슈).
    채워서 넘기는 곳은 `review._build` 의 `build()` 호출이다.
    - allergens 는 19종 밖(쑥 등)도 이름으로 담는다
    - items 는 준비물(수영복 등)
    """
    gate = context.state.gate
    if gate is None:
        raise RuntimeError("Gate 없이 출력 tool 이 불렸다 — run() 이 먼저 build_gate 를 부른다")

    today = context.today
    observations = await context.ports.memory.observations(
        child_id=context.child_id,
        # 오늘을 포함한 7일 — 오늘과 앞 6일. 포트는 양끝을 포함한다
        date_from=today - timedelta(days=DUPLICATE_WINDOW_DAYS - 1),
        date_to=today,
    )
    review = review_candidates(
        args.candidates,
        months=gate.stage.months,
        seen=context.state.seen_evidence,
        places=context.state.seen_places,
        recent_activities=[row.activity for row in observations],
    )
    if review.rejections:
        code = error_code(review.rejections)
        return fail("propose", _NAME, code, explain(review.rejections))

    check_count(review.drafts)
    context.state.suggestions = review.drafts
    return ok("propose", _NAME, count=len(review.drafts), kinds=[d.kind for d in review.drafts])
