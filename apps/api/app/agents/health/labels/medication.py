"""`medication` — 복약 초안 · 중단 · 체크 안내 · 조회. 코드가 먼저 거르고, 나머지만 모델 한 번.

순서 (앞에서 걸리면 거기서 끝난다)
  ① 이어받은 되묻기 맥락(`context.pending`)이 있으면 kind 별로 이어 간다 — **전부 모델 0회**
  ② 조회 표현("다음 약 언제") → 체크된 회차 기준 사실만 (`dose.status`) — 모델 0회
  ③ 복용 표현("먹였어" · "못 먹였어") → 아무것도 쓰지 않고 복약 화면 체크 안내 — 모델 0회
  ④ 그 밖에는 tool loop — 새 약 · 바꾸기 · 끊기 · 표에 안 걸린 체크 현황 · 용량 질문. 모델 1회

Agent 가 쓰는 것은 한 번 물어 보호자가 확인한 **중단 하나**뿐이다. 생성 · 수정은 초안까지만이고
(보호자가 카드에서 제출해야 백엔드가 쓴다), 복용 체크는 보호자가 화면에서 한다.

되묻고 멈추는 자리는 세 곳이다(중단 확인 · 대상 약 · 복용 시점). 묻는 run 은 `pending` 에 이어서
처리할 것을 싣고 끝나고, 다음 run 이 받으면 모델 없이 답을 가른다. 못 가린 답은 아무것도 쓰지
않고 다시 묻지도 않는다 — 같은 질문을 반복하면 보호자가 막힌다.

표(`QUERY_WORDS` · `DOSE_WORDS` · `YES_WORDS` · `STOP_STEMS` · `NO_WORDS`)가 커지면
결국 의도 분류를 코드로 하는 셈이다. 그래서 표를 짧게 두고, 못 걸리는 말은 모델이 본다.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Literal
from uuid import UUID

from app.agents.common.llm_client import LLMClient
from app.agents.common.result import DomainAgentResult, DomainPending
from app.agents.common.result import MedicationDraft as ChannelDraft
from app.agents.common.schemas.task import DomainTask
from app.agents.common.tool_loop import run_tool_loop
from app.agents.common.tool_runtime import ToolResult
from app.agents.health.context import HealthContext
from app.agents.health.envelope import result
from app.agents.health.prompt import build_system_prompt
from app.agents.health.readouts import (
    MED_NO_CHANGE,
    MED_PERIOD_UNCLEAR,
    MED_STOP_KEPT,
    MED_STOPPED,
    MED_TIMING_UNCLEAR,
    MED_WHICH_UNCLEAR,
    NOTICE_NO_ACTIVE_MED,
    READOUTS,
)
from app.agents.health.registry import OUTPUT_TOOLS, execute_tool, specs_for, tools_for
from app.agents.health.result import ErrorCode
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.ports import MedicationCourse
from app.agents.health.tools.doses import DOSE_WORDS, check_on_screen, dose_status_readouts
from app.agents.health.tools.drafts import (
    KIND_STOP,
    KIND_TIMING,
    KIND_WHICH,
    ask_stop,
    child_months,
    finish_create,
    finish_update,
    update_for,
)
from app.agents.health.tools.exits import blocked_exit, log_rejected_exit
from app.agents.health.tools.spans import normalize_spaces
from app.agents.health.tools.timing import (
    anchors_named,
    resolve_dose_answer,
    resolve_dose_timing,
)
from app.core.medication_draft import MedicationDraft

logger = logging.getLogger(__name__)

TASK = HealthTaskType.MEDICATION
# 대화 한 번의 왕복 상한. 등록된 약 조회 한 번 + 초안 + 거절 뒤 재제출
MAX_STEPS = 6
MAX_COMPLETION_TOKENS = 1000

# 조회 표현. 걸리면 모델을 부르지 않고 체크된 회차 기준 사실만 낸다
QUERY_WORDS: tuple[str, ...] = ("다음 약", "언제 먹", "또 먹여도", "먹여도 돼", "더 먹여도")
# 등록 · 수정 · 중단을 뜻하는 말. 있으면 위 표(조회 · 복용)가 먼저 받지 않고 모델이 본다
ACTION_WORDS: tuple[str, ...] = (
    "끊",
    "그만",
    "멈춰",
    "중단",
    "삭제",
    "바꿔",
    "바꿀",
    "변경",
    "옮겨",
    "수정",
    "등록",
    "새로",
    "새 약",
)
# 중단 확인에 대한 답. 말 하나(공백 · 문장부호로 자른 조각)가 이 말이거나 이 말 + 어미일 때만 센다 —
# "그래서" · "반응이" 처럼 앞글자만 같은 말을 '네' 로 읽으면 약이 멈춘다
YES_WORDS: tuple[str, ...] = ("응", "네", "예", "그래", "멈춰", "끊어", "맞아", "좋아")
NO_WORDS: tuple[str, ...] = ("아니", "아냐", "취소", "계속", "그대로", "말고")
# 멈추겠다는 말. 어미가 붙어야 센다 — "그만" · "중단" 만으로는 "그만 먹여도 돼?" 에도 걸린다
STOP_STEMS: tuple[str, ...] = ("중단", "그만둘", "그만", "끊을", "멈출")
_ENDINGS = "(?:요|용|야|줘|줘요|주세요|에요|예요|해|해줘|해요|둬|둬요)?"
_STOP_ENDINGS = (
    "(?:할게요|할게|할래요|할래|하겠어요|하겠습니다|해줘요|해줘|해주세요|해요|해|게요|게|래요|래)"
)
_YES = re.compile(
    rf"^(?:{'|'.join(YES_WORDS)}|(?:{'|'.join(STOP_STEMS)}){_STOP_ENDINGS})+{_ENDINGS}$"
)
_NO = re.compile(rf"^(?:{'|'.join(NO_WORDS)})+{_ENDINGS}$")
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")
# "안 끊을게요" · "못 멈출게요" 는 멈추지 않겠다는 말이다. 어간 + 어미만 보면 '네' 가 되어
# 반대로 쓴다. 부정 말이 한 토큰으로 있으면 '네' 로 세지 않는다
_NEGATIONS = ("안", "못")
# "네 번 남았어요" 의 네는 숫자다. 이것을 '네' 로 읽으면 중단 쓰기가 일어난다. 세는 말이 뒤따를 때만
# 숫자로 본다 — "네 알겠어요" 의 네는 대답이다
_NUMERAL_NE = re.compile(r"네\s*(?:번|개|회|가지|시간|시(?:에|쯤|경|까지|부터))")
# "기침이 끊이지 않아" · "열이 끊어지지 않아" 는 약을 끊는다는 말이 아니다. 걸러 내지 않으면
# "끊" 한 글자에 걸려 체크 안내가 모델로 새고, 묻지 않은 중단 질문이 나간다
_NOT_STOP_REQUEST = re.compile(r"끊이|끊어지|끊기지|끊겨|끊긴|끊김")


class _BadPending(Exception):
    """이어받은 맥락의 모양이 틀렸다 — 아무것도 쓰지 않고 실패로 끝낸다."""


def confirm_answer(texts: Sequence[str]) -> Literal["yes", "no", "unclear"]:
    """'네' · '아니요' 를 표현 표로 가른다.

    아니요가 하나라도 있으면 아니요, 네만 있으면 네, 그 밖은 모른다. 못 알아들은 답은 "그대로
    둔다" 쪽이다 — 알림이 멈추는 것은 보호자가 모르고 지나가기 쉽다. 숫자 "네 번" 은 네가 아니고,
    "끊을게요" · "중단해 주세요" 같은 분명한 말은 네다.
    """
    tokens = [token for text in texts for token in _TOKEN.findall(_NUMERAL_NE.sub(" ", text))]
    if any(_NO.match(token) for token in tokens):
        return "no"
    if any(token in _NEGATIONS for token in tokens):
        return "unclear"
    if any(_YES.match(token) for token in tokens):
        return "yes"
    return "unclear"


async def run_medication(
    task: DomainTask, context: HealthContext, client: LLMClient | None
) -> DomainAgentResult:
    context.state.request_texts = tuple(task.request_texts)
    if context.pending is not None:
        return await _resume(context, context.pending, task.request_texts)

    text = _NOT_STOP_REQUEST.sub(" ", " ".join(task.request_texts))
    # 등록 · 수정 · 중단 말이 같이 있으면 표가 가로채지 않는다 — "먹였는데 이제 끊을게" 가 체크
    # 안내로 가면 보호자의 요청이 아무 말 없이 사라진다. 그런 말은 모델이 본다
    if not any(word in text for word in ACTION_WORDS):
        if any(word in text for word in QUERY_WORDS):
            context.state.readouts += await dose_status_readouts(context)
            return _finish(context)
        if any(word in text for word in DOSE_WORDS):
            context.state.readouts.append(await check_on_screen(context))
            return _finish(context)
    return await _converse(task, context, client)


async def _converse(
    task: DomainTask, context: HealthContext, client: LLMClient | None
) -> DomainAgentResult:
    """모델 한 번. 출력 tool(create · update · stop)이 통과하면 끝난다 — 되묻는 것도 통과다."""
    gate = context.state.gate
    assert gate is not None  # run() 이 채운다
    allowed = tools_for(TASK, gate)
    llm = client or LLMClient(role="health")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context, TASK)},
        {"role": "user", "content": "\n".join(task.request_texts)},
    ]

    async def execute(name: str, arguments: dict[str, Any]) -> ToolResult:
        # 읽기 전용 출구가 같은 응답의 쓰기 요청을 가리지 않게 한다
        blocked = blocked_exit(name, messages, output_tools=OUTPUT_TOOLS[TASK])
        if blocked is not None:
            log_rejected_exit(context.run_id, name, "same_response")
            return blocked
        return await execute_tool(name, arguments, context, allowed=allowed)

    loop = await run_tool_loop(
        llm,
        messages,
        tools=specs_for(allowed),
        execute=execute,
        output_tools=OUTPUT_TOOLS[TASK],
        max_steps=MAX_STEPS,
        tool_choice="required",
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )
    if not loop.completed:
        logger.warning(
            "Health 복약 라벨이 결과를 내지 못했다 run_id=%s ended_by=%s",
            context.run_id,
            loop.ended_by,
        )
        return result(TASK, "failed", model_calls=1)
    return _finish(context, model_calls=1)


async def _resume(
    context: HealthContext, pending: DomainPending, texts: Sequence[str]
) -> DomainAgentResult:
    """이어받은 되묻기 맥락을 모델 없이 이어 간다. 모르는 맥락 · 망가진 맥락은 쓰지 않고 실패다."""
    handlers = {KIND_STOP: _resume_stop, KIND_WHICH: _resume_which, KIND_TIMING: _resume_timing}
    handler = handlers.get(pending.kind) if pending.agent == "health" else None
    if handler is None:
        logger.warning(
            "Health 가 이어받을 수 없는 맥락이다 run_id=%s kind=%s", context.run_id, pending.kind
        )
        return result(TASK, "failed")
    try:
        outcome = await handler(context, pending.payload, texts)
    except _BadPending:
        logger.warning(
            "Health 되묻기 맥락이 망가졌다 run_id=%s kind=%s", context.run_id, pending.kind
        )
        return result(TASK, "failed")
    if outcome is not None and not outcome.success:
        # 초안을 못 냈다 — 지금과 같은 값이거나, 시각이 겹쳐 화면에 못 나가는 초안이다
        no_change = outcome.error is not None and outcome.error["code"] == ErrorCode.NO_CHANGE
        # 기간을 못 읽어서 바뀐 게 없는 것이면 그 안내가 이미 이유를 말한다
        explained = no_change and READOUTS.render(MED_PERIOD_UNCLEAR) in context.state.readouts
        if not explained:
            context.state.readouts.append(
                READOUTS.render(MED_NO_CHANGE if no_change else MED_TIMING_UNCLEAR)
            )
    return _finish(context)


async def _resume_stop(
    context: HealthContext, payload: Mapping[str, Any], texts: Sequence[str]
) -> ToolResult | None:
    """중단 확인에 대한 답. '네' 면 멈추고 중단 시각은 이 run 의 now 다. 그 밖에는 그대로 둔다.

    '네' 를 받았을 때 그 코스가 **지금도 진행 중인지 다시 읽는다**(`_resume_which` 와 같다).
    오늘 끝나는 코스를 23:55 에 묻고 00:05 에 '네' 를 받으면 이미 끝난 코스가 `stopped` 로
    바뀌기 때문이다. 이 아이의 진행 중 코스에서만 찾으므로 맥락의 id 가 다른 아이의 코스여도
    멈추지 않는다.
    """
    try:
        schedule_id, title = UUID(payload["schedule_id"]), str(payload["title"])
    except (KeyError, ValueError, TypeError) as exc:
        raise _BadPending from exc

    if confirm_answer(texts) != "yes":
        context.state.readouts.append(READOUTS.render(MED_STOP_KEPT, title=title))
        return
    if not any(course.id == schedule_id for course in await _active(context)):
        context.state.readouts.append(READOUTS.render(NOTICE_NO_ACTIVE_MED))  # 끝났거나 멈췄다
        return
    outcome = await context.ports.medication_writer.stop_course(
        schedule_id=schedule_id, stopped_at=context.now
    )
    if outcome == "missing":  # 그 사이 코스가 사라졌다
        context.state.readouts.append(READOUTS.render(NOTICE_NO_ACTIVE_MED))
    else:  # "already" 는 시각을 바꾸지 않았다 — 멈춰 있다는 말은 그대로 맞다
        context.state.readouts.append(READOUTS.render(MED_STOPPED, title=title))


async def _resume_which(
    context: HealthContext, payload: Mapping[str, Any], texts: Sequence[str]
) -> ToolResult | None:
    """어느 약인지에 대한 답. 답에 이름이 들어 있는 약이 **딱 하나**일 때만 그 약으로 이어 간다."""
    try:
        op = payload["op"]
        candidate_ids = {str(c["schedule_id"]) for c in payload["candidates"]}
        args = dict(payload["args"])
        if op not in ("stop", "update"):
            raise ValueError(op)
    except (KeyError, ValueError, TypeError) as exc:
        raise _BadPending from exc

    # 이어받을 때 진행 중 코스를 다시 읽는다 — 그 사이 멈춘 약은 후보에서 빠진다
    active = await _active(context)
    answer = normalize_spaces(" ".join(texts))
    named = [
        course
        for course in active
        if str(course.id) in candidate_ids and normalize_spaces(course.title) in answer
    ]
    if len(named) != 1:
        context.state.readouts.append(READOUTS.render(MED_WHICH_UNCLEAR))
        return
    if op == "stop":
        ask_stop(context, named[0])  # 중단 확인은 다른 질문이다
        return
    return await update_for(
        context,
        named[0],
        dosage_span=args.get("dosage_span"),
        timing_spans=args.get("timing_spans"),
        period_span=args.get("period_span"),
    )


async def _resume_timing(
    context: HealthContext, payload: Mapping[str, Any], texts: Sequence[str]
) -> ToolResult | None:
    """언제 먹이는지에 대한 답. 원래 말한 시점 표현에 답을 이어 붙여 사전으로 푼다."""
    try:
        op = payload["op"]
        spans = [str(span) for span in payload["timing_spans"]]
        draft = dict(payload["draft"])
        starts_on, ends_on = _date(draft.get("starts_on")), _date(draft.get("ends_on"))
        if op not in ("create", "update"):
            raise ValueError(op)
        schedule_id = payload.get("schedule_id")
        same = payload.get("same_time", False)
        if not isinstance(same, bool):
            raise TypeError(same)
    except (KeyError, ValueError, TypeError) as exc:
        raise _BadPending from exc

    answer = " ".join(texts)
    months = await child_months(context)
    if same:
        # 같은 시각이라 하나씩 물었다 — 답이 새 시각 전부다. 원래 말에 이어 붙이면 또 겹친다.
        # 바꿀 회차는 원래 말로만 가린다(답의 "저녁 8시" 의 저녁은 저녁 회차를 가리키지 않는다)
        timing = resolve_dose_timing([answer], months=months)
    else:
        timing = resolve_dose_answer(spans, answer, months=months)
        spans = [*spans, answer]
    if timing is None:
        context.state.readouts.append(READOUTS.render(MED_TIMING_UNCLEAR))
        return
    if op == "create":
        return finish_create(
            context,
            title=draft.get("title"),
            dosage=draft.get("dosage"),
            indication_text=draft.get("indication_text"),
            starts_on=starts_on,
            ends_on=ends_on,
            timing=timing,
        )
    course = next((c for c in await _active(context) if str(c.id) == schedule_id), None)
    if course is None:  # 그 사이 멈춘 코스는 고치지 않는다
        context.state.readouts.append(READOUTS.render(MED_WHICH_UNCLEAR))
        return
    return finish_update(
        context,
        course,
        dosage=draft.get("dosage"),
        ends_on=ends_on or course.ends_on,
        timing=timing,
        named=anchors_named(spans),
    )


def _finish(context: HealthContext, *, model_calls: int = 0) -> DomainAgentResult:
    """run state 에 쌓인 것을 결과로 옮긴다. 저장은 없다."""
    state = context.state
    return result(
        TASK,
        "completed",
        readouts=tuple(state.readouts),
        question=state.question,
        medication_drafts=tuple(_channel(draft) for draft in state.drafts),
        pending=state.pending,
        model_calls=model_calls,
    )


def _channel(draft: MedicationDraft) -> ChannelDraft:
    """검증을 통과한 초안 모델 → 화면으로 나가는 채널 모양."""
    payload = draft.to_payload()
    return ChannelDraft(
        draft_id=draft.draft_id,
        op=draft.op,
        source="utterance",
        schedule=payload["schedule"],
        doses=tuple(payload["doses"]),
        notice_times=tuple(draft.notice_times),
        missing=tuple(draft.missing),
        schedule_id=UUID(draft.schedule_id) if draft.schedule_id else None,
        before=payload.get("before"),
    )


def _date(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


async def _active(context: HealthContext) -> list[MedicationCourse]:
    return await context.ports.medication.active_courses(
        child_id=context.child_id, today=context.today
    )
