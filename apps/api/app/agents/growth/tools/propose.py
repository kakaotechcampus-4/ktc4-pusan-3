"""교육 · 루틴 · 도서 추천의 출력 (모델 tool 3개).

통과한 추천은 `state.suggestions` 에 담는다. 저장 · 발송은 하지 않는다 — pipeline 이 저장한다.
안전 필터 · 금지 표현으로 빠진 후보는 이름만 `state.excluded` 에 남기고 모델에게 사유를 알리지
않는다. 3개(루틴은 고른 단계 수)가 안 되면 재호출 1회는 `run()` 이 한다.

루틴은 후보를 보기 전에 코드가 끝낼 수 있다 — 의료 처치 · 증상, 연령 허용표, 근거 0(되묻기),
단계 없음. 그때는 `state.closing`(코드 문구 키) 이나 `state.question`(되묻기 한 줄)을 남기고
성공으로 돌려준다 — 루프는 끝나고 `run()` 이 그 값으로 결과를 만든다.
"""

import logging

from app.agents.common.gate import Gate
from app.agents.common.suggestion import MAX_SUGGESTIONS, check_count
from app.agents.growth.context import GrowthContext
from app.agents.growth.readouts import ASK_ROUTINE_CURRENT, DOC_NO_ROW, NEXT_STEP_CHAIN_END
from app.agents.growth.result import ErrorCode, ToolResult, fail, ok
from app.agents.growth.review import (
    error_code,
    explain,
    review_books,
    review_learning,
    review_routine,
)
from app.agents.growth.schemas.books import ProposeBooksArgs
from app.agents.growth.schemas.learning import ProposeLearningActivityArgs
from app.agents.growth.schemas.routine import ProposeRoutinePlanArgs
from app.agents.growth.tools.books import book_label, propose_books
from app.agents.growth.tools.books import explain as explain_books
from app.agents.growth.tools.closures import closure_key
from app.agents.growth.tools.memory import SELF_RELIANCE
from app.agents.growth.tools.routine import ask_question, check_routine_category, routine_gap
from app.agents.growth.tools.safety import SafetyRules

logger = logging.getLogger(__name__)

_LEARNING = "propose_learning_activity"
_ROUTINE = "propose_routine_plan"
_BOOKS = "propose_books"


def _ready(context: GrowthContext) -> tuple[Gate, SafetyRules]:
    gate, rules = context.state.gate, context.state.rules
    if gate is None or rules is None:
        raise RuntimeError(
            "Gate · 안전 사전 없이 출력 tool 이 불렸다 — run() 이 build_gate 를 먼저 부른다"
        )
    return gate, rules


async def propose_learning_activity(
    context: GrowthContext, args: ProposeLearningActivityArgs
) -> ToolResult:
    gate, rules = _ready(context)
    state = context.state
    review = review_learning(
        args.items,
        months=gate.stage.months,
        entries=state.safety_entries,
        rules=rules,
        seen=state.seen_evidence,
        docs=state.seen_docs,
        today=context.today,
    )
    if review.rejections:
        return fail("propose", _LEARNING, error_code(review.rejections), explain(review.rejections))
    _log_removed(context, len(review.removed))
    state.excluded = tuple(args.items[i].title for i in review.removed)
    if not review.removed:
        check_count(review.drafts)
    state.suggestions = review.drafts
    state.capacity = MAX_SUGGESTIONS
    return ok("propose", _LEARNING, count=len(review.drafts), kinds=[d.kind for d in review.drafts])


async def propose_routine_plan(context: GrowthContext, args: ProposeRoutinePlanArgs) -> ToolResult:
    gate, rules = _ready(context)
    state = context.state
    months, today = gate.stage.months, context.today
    state.suggestions, state.excluded, state.closing, state.question = (), (), None, None

    closed = closure_key((args.subject,)) or check_routine_category(args.category, months)
    if closed is not None:
        state.closing = closed
        return ok("propose", _ROUTINE, closed=True)

    observed = tuple(state.seen_routine.values())
    gap = routine_gap(observed, category=args.category, today=today)
    if gap is not None:
        state.question = ask_question(gap, _subject(context, args.category, args.subject))
        return ok("propose", _ROUTINE, asked=True)

    if args.category not in SELF_RELIANCE:
        # 예절 · 습관 전략 행이 아직 없다(P1 행). 모델이 전략을 지어내지 않게 닫는다
        state.closing = DOC_NO_ROW
        return ok("propose", _ROUTINE, closed=True)

    picked = [p for p in state.steps.values() if p.category == args.category]
    rows = {p.step.row.id: p.step.row for p in picked if p.step.row is not None}
    if not rows:
        keys = {p.step.readout_key for p in picked}
        if ASK_ROUTINE_CURRENT in keys or not picked:
            state.question = ask_question(
                ASK_ROUTINE_CURRENT, _subject(context, args.category, args.subject)
            )
            return ok("propose", _ROUTINE, asked=True)
        state.closing = NEXT_STEP_CHAIN_END if NEXT_STEP_CHAIN_END in keys else DOC_NO_ROW
        return ok("propose", _ROUTINE, closed=True)

    # 재호출이면 이미 통과한 단계는 빼고 남은 단계만 받는다
    rows = {step_id: row for step_id, row in rows.items() if step_id not in state.done_steps}
    capacity = min(MAX_SUGGESTIONS, len(rows))
    review = review_routine(
        args.items,
        rows=rows,
        capacity=capacity,
        category=args.category,
        observations=state.seen_routine,
        rules=rules,
        seen=state.seen_evidence,
        today=today,
    )
    if review.rejections:
        return fail("propose", _ROUTINE, error_code(review.rejections), explain(review.rejections))
    if not review.removed and len(review.drafts) < capacity:
        return fail(
            "propose",
            _ROUTINE,
            ErrorCode.CANDIDATE_REJECTED,
            f"next_steps 에 있는 단계 {capacity}개를 하나씩 다 낸다.",
        )
    _log_removed(context, len(review.removed))
    state.excluded = tuple(args.items[i].next_step for i in review.removed)
    if not review.removed:
        check_count(review.drafts, exhausted=capacity < MAX_SUGGESTIONS)
    state.suggestions = review.drafts
    state.capacity = capacity
    return ok("propose", _ROUTINE, count=len(review.drafts), kinds=[d.kind for d in review.drafts])


async def propose_books_tool(context: GrowthContext, args: ProposeBooksArgs) -> ToolResult:
    """도서 출력. 검색한 책 중에서 고른 것만 낸다 — 지어낸 책 · 이미 낸 책 · 같은 책은 사유와 함께
    돌려준다(`tools/books.propose_books`).

    낼 수 있는 책(`available`, 3권까지)을 다 내야 한다. 검색 결과가 모자라면 있는 만큼, 0권이면 빈
    제출을 받는다 — 단 검색을 한 뒤에만. 금지 표현이 든 후보는 빠지고(제외 목록) 재호출 1회는
    `run()` 이 한다. 안전 필터는 없다 — 도서는 건강정보를 읽지 않는다.
    """
    state = context.state
    state.suggestions, state.excluded = (), ()
    if not state.book_searched:
        return fail(
            "propose", _BOOKS, ErrorCode.CANDIDATE_REJECTED, "먼저 search_books 로 책을 찾는다."
        )
    picked = await propose_books(context, [item.isbn for item in args.items])
    if picked.rejections:
        hint = "" if picked.available else " 낼 수 있는 책이 더 없으면 items 를 비운다."
        return fail(
            "propose", _BOOKS, ErrorCode.CANDIDATE_REJECTED, explain_books(picked.rejections) + hint
        )
    review = review_books(
        args.items, picked.accepted, seen=state.seen_evidence, today=context.today
    )
    if review.rejections:
        return fail("propose", _BOOKS, error_code(review.rejections), explain(review.rejections))
    if not review.removed and len(review.drafts) < picked.available:
        return fail(
            "propose",
            _BOOKS,
            ErrorCode.CANDIDATE_REJECTED,
            f"search_books 결과에 낼 수 있는 책이 {picked.available}권 있다. "
            f"{picked.available}권을 낸다.",
        )
    _log_removed(context, len(review.removed))
    state.excluded = tuple(book_label(picked.accepted[i]) for i in review.removed)
    if not review.removed:
        check_count(review.drafts, exhausted=picked.available < MAX_SUGGESTIONS)
    state.suggestions = review.drafts
    state.capacity = picked.available
    return ok("propose", _BOOKS, count=len(review.drafts), kinds=[d.kind for d in review.drafts])


def _subject(context: GrowthContext, category: str, fallback: str) -> str:
    """되묻기에 넣을 행동 이름 — 같은 카테고리 관찰의 subject(코드가 가진 값)가 먼저다."""
    for row in context.state.seen_routine.values():
        if row.routine_category == category:
            return row.subject
    return fallback.strip()


def _log_removed(context: GrowthContext, count: int) -> None:
    if count:
        # 건수만 남긴다. 활동명 · 알레르기 이름은 로그에 싣지 않는다
        logger.info("Growth 출력 필터 run_id=%s removed=%d", context.run_id, count)
