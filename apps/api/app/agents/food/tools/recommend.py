"""식단 추천의 출력 (모델 tool)"""

import logging

from app.agents.common.age import life_stage
from app.agents.common.suggestion import SuggestionRejected, check_count, count_notice
from app.agents.food.context import FoodContext, require_safety
from app.agents.food.meal_review import error_code, explain, review_candidates
from app.agents.food.readouts import POOL_EMPTY, READOUTS
from app.agents.food.result import ErrorCode, ToolResult, fail, ok
from app.agents.food.schemas.recommend import ProposeMealCandidatesArgs

_NAME = "propose_meal_candidates"

logger = logging.getLogger(__name__)


async def propose_meal_candidates(
    context: FoodContext, args: ProposeMealCandidatesArgs
) -> ToolResult:
    """모델이 고른 후보를 검증하고 추천 초안(`SuggestionDraft`)으로 바꾼다.

    검증 순서와 이유는 `meal_review` 머리말에 있다. 여기서는 그 결과를 run state 에 담고
    개수를 본다.

    - 🚨 안전 필터는 `build_gate` 가 run state 에 담은 health_safety 행만 쓴다. 여기서 다시 읽지
      않는다 — 읽으면 같은 run 안에서 게이트와 필터가 다른 행을 볼 수 있다.
    - 거절(샘플 밖 menu_key · reason 문장 · 조회하지 않은 근거 · 기피 이유)이 하나라도 있으면
      그 사유만 모델에게 돌려주고 아무것도 담지 않는다. 조회하지 않은 근거는
      `EVIDENCE_REQUIRED` 다.
    - 안전 필터에 걸린 후보는 거절이 아니라 풀에서 빠진다. 모델에게 돌려주지 않고 사유도
      알리지 않는다 — 어휘 회피를 가르치게 된다(§5-2). 빠진 `menu_key` 를 `state.blocked_keys`
      에 담아 run() 이 풀에서 빼고 재샘플링한다. 로그에는 건수만 남긴다.
    - 개수는 `check_count` 로 본다. 더 채울 길이가 있는데(`state.exhausted` 가 아님) 안전 필터로
      빠졌으면 재호출 결과와 합치기 전이라 개수를 따지지 않고 안내도 붙이지 않는다. 재호출을 했거나
      풀이 처음부터 모자라면(`exhausted`) 있는 만큼 내고 `count_notice` 안내를 붙인다 — 0개면 추천
      없이 `pool.empty` 다.
    - 메뉴 이름 · 재료(`items`)는 카탈로그 행에서 오고 `allergens` 는 `SafetyVerdict.allergens` 와
      같다. 통과한 추천은 `state.picks` 에 담는다. 저장 · 발송 · 예약은 하지 않는다. status 와
      expires_at 은 인자에 없다.
    """
    entries = require_safety(context)
    state = context.state
    birth_date = await context.ports.profile.birth_date(child_id=context.child_id)
    life = life_stage(birth_date, context.today)

    review = review_candidates(
        args.candidates,
        sampled=state.sampled,
        seen=state.seen_evidence,
        docs=state.seen_docs,
        entries=entries,
        stage=life.stage,
        months=life.months,
        today=context.today,
    )
    if review.rejections:
        return fail("propose", _NAME, error_code(review.rejections), explain(review.rejections))

    if review.removed:
        # 로그에는 건수만. 메뉴 이름 · 걸린 알레르기 항목은 남기지 않는다
        logger.info("Food 안전 필터 run_id=%s removed=%d", context.run_id, len(review.removed))
        state.blocked_keys.update(review.removed)

    picks = review.picks
    if review.removed and not state.exhausted:
        state.picks = picks
        state.notice = None
    else:
        try:
            check_count(tuple(pick.draft for pick in picks), exhausted=state.exhausted)
        except SuggestionRejected as exc:
            return fail("propose", _NAME, ErrorCode.INVALID_ARGS, str(exc))
        state.picks = picks
        state.notice = count_notice(len(picks), empty=READOUTS.get(POOL_EMPTY))
    return ok("propose", _NAME, count=len(picks), kinds=[pick.draft.kind for pick in picks])
