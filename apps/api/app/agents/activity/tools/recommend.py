"""놀이 추천의 출력 (모델 tool)."""

import logging
from datetime import timedelta

from app.agents.activity.context import ActivityContext
from app.agents.activity.result import ToolResult, fail, ok
from app.agents.activity.review import error_code, explain, review_candidates
from app.agents.activity.rules import DUPLICATE_WINDOW_DAYS
from app.agents.activity.schemas.recommend import ProposeActivityCandidatesArgs
from app.agents.activity.tools.filters import safety_terms
from app.agents.common.suggestion import check_count

_NAME = "propose_activity_candidates"

logger = logging.getLogger(__name__)


async def propose_activity_candidates(
    context: ActivityContext, args: ProposeActivityCandidatesArgs
) -> ToolResult:
    """놀이 후보 3개를 검증하고 suggestion 초안으로 바꾼다 (설계 3-3).

    - 모델이 낸 개수(3개)는 인자 검증이 이미 막았다.
    - 걸린 후보가 하나라도 있으면 사유와 함께 돌려준다. 모델은 고쳐서 3개를 다시 낸다 —
      같은 진입 안의 루프라 model_calls 가 늘지 않는다. 오류 코드는 지어낸 근거 id 가 있으면
      EVIDENCE_REQUIRED, 아니면 CANDIDATE_REJECTED (`review.error_code`).
    - 통과하면 `context.state.suggestions` 에 담는다. 저장 · 발송 · 예약은 하지 않는다.
      status 와 expires_at 은 인자에 없다.

    - 🚨 안전 필터는 build_gate 가 run state 에 담은 health_safety 행만 쓴다. 여기서 다시
      읽지 않는다 — 읽으면 같은 run 안에서 게이트와 필터가 다른 행을 볼 수 있다.
    - 안전 필터에 걸린 후보는 거절이 아니라 풀에서 빠진다. 모델에게 돌려주지 않고 사유도 알리지
      않는다 — 어휘 회피를 가르치게 된다 (§5-2). 통과한 초안과 빠진 후보(`excluded`)를 run state
      에 담고 끝낸다. 3개가 안 되면 재호출 1회는 run() 이 제외 목록과 함께 한다 —
      재호출 결과를 합친 뒤 `check_count(..., exhausted=True)` → `count_notice(len)` 순서다.
    - 경고 문구(위험 용어 사전 상수)는 추천과 같은 순서로 `state.warnings` 에 담는다.
    - suggestion.allergens · items 는 `review._build` 가 코드로 채운다 (#232).
    """
    gate = context.state.gate
    entries = context.state.safety_entries
    if gate is None or entries is None:
        raise RuntimeError(
            "Gate · 안전 정보 없이 출력 tool 이 불렸다 — run() 이 먼저 build_gate 를 부른다"
        )

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
        safety=safety_terms(entries),
        outdoor_ok=gate.outdoor_ok,
    )
    if review.rejections:
        # 거절만 모델에게 돌려준다. 같이 빠진 후보는 말하지 않는다 — 다시 내면 또 빠진다
        code = error_code(review.rejections)
        return fail("propose", _NAME, code, explain(review.rejections))

    if review.removed:
        # 로그에는 건수와 축 이름만. 활동명 · 알레르기 이름은 남기지 않는다
        logger.info(
            "Activity 안전 필터 run_id=%s removed=%d axes=%s",
            context.run_id,
            len(review.removed),
            sorted({hit for removal in review.removed for hit in removal.hits}),
        )
        context.state.excluded = tuple(args.candidates[r.index].content for r in review.removed)
    else:
        check_count(review.drafts)
        context.state.excluded = ()
    context.state.suggestions = review.drafts
    context.state.warnings = review.warnings
    return ok("propose", _NAME, count=len(review.drafts), kinds=[d.kind for d in review.drafts])
