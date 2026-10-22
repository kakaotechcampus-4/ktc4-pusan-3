"""`visit_summary` — 진료 준비 정리 · 열 타임라인. 코드가 먼저 거르고, 정리만 모델 한 번.

순서 (앞에서 걸리면 거기서 끝난다 — 단 ①은 끝내지 않고 맨 앞에 얹는다)
  ① 3개월 미만 + 최근 48시간 발열 → `emergency` 를 결과 맨 앞에 둔다. 타임라인보다 먼저다
  ② 열 표현("열 언제부터" · "몇 도")  → 열 타임라인(`build_fever_timeline`) — 모델 0회
  ③ 최근 14일 건강 기록 0건 → `visit.no_record` — 모델 0회. 빈 타임라인을 만들지 않는다
  ④ 그 밖에는 tool loop — 조회 tool 넷 + 출력 `compose_symptom_timeline`. `model_calls=1`

④의 왕복(조회 → compose, 거절되면 고쳐 한 번 더)은 같은 loop 안이라 `model_calls` 가 늘지 않는다.
왕복 수는 loop 의 `steps` 다 (`common/tool_loop.py`). 모델은 정리만 하고, ref · 약 이름 · 해석
금지어 · 알레르기 줄은 출력 tool 이 코드로 거르고 붙인다(`tools/timeline.py` · `review.py`).

열 표현 표(`FEVER_WORDS`)는 `medication` 의 조회 표현 표처럼 짧게 둔다. 못 걸리는 말은 정리로
간다 — 열 질문이 정리에 섞여 나가는 쪽이 정리 요청이 열 타임라인으로 새는 쪽보다 낫다. 같은 이유로
정리 표현(`SUMMARY_WORDS`)이 같이 있으면 열 표현이 있어도 정리다. 알려진 틈: "열나는데 언제부터였지"
처럼 표에 없는 말은 정리로 간다(모델 1회).

저장하지 않는다. 안전 조회 실패는 닫지 않는다 — 정리 맨 위에 경고 줄이 붙는다(출력 tool 몫).
열 타임라인 · 기록 없음 안내에는 알레르기 줄이 없어서 경고도 붙이지 않는다.
"""

import logging
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.result import AgentStatus, DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.common.tool_loop import run_tool_loop
from app.agents.common.tool_runtime import ToolResult
from app.agents.health.context import HealthContext
from app.agents.health.envelope import result
from app.agents.health.prompt import build_system_prompt
from app.agents.health.readouts import EMERGENCY, READOUTS, VISIT_NO_RECORD
from app.agents.health.registry import OUTPUT_TOOLS, execute_tool, specs_for, tools_for
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.ports import HealthObservation
from app.agents.health.tools.fever import WINDOW, build_fever_timeline, infant_fever

logger = logging.getLogger(__name__)

TASK = HealthTaskType.VISIT_SUMMARY
# 조회 한 번 + 식사 · 복약 조회 + 정리 + 거절 뒤 재제출
MAX_STEPS = 6
MAX_COMPLETION_TOKENS = 2000
# 진료 준비가 거슬러 보는 기간. 조회 tool 의 기본 기간(14d)과 같다 — 오늘 포함 N일
RECORD_DAYS = 14

# 열 질문 표현. 걸리면 모델을 부르지 않고 체온 · 체크된 복약 · 먹인 약을 나열만 한다.
# 공백을 없애고 견준다 — "열 언제" 와 "열언제" 를 같은 말로 본다
FEVER_WORDS: tuple[str, ...] = ("열 언제", "열이 언제", "몇 도", "체온")
# 정리를 원한다는 표현. 열 표현과 같이 있으면 정리로 간다
SUMMARY_WORDS: tuple[str, ...] = ("정리", "요약", "한눈에")


def _squash(text: str) -> str:
    return "".join(text.split())


def is_fever_question(texts: Sequence[str]) -> bool:
    """열 표현이 있고 정리 표현은 없으면 참. 표 말고는 보지 않는다.

    열 말과 정리 말이 같이 있으면("열이 언제부터인지랑 기침도 정리해줘") 정리로 간다 — 열 질문
    하나로 읽으면 같이 달라고 한 증상 · 식사가 말없이 빠진다. 그래서 "체온 정리해줘" 도 정리다.
    """
    squashed = "".join(_squash(text) for text in texts)
    if any(_squash(word) in squashed for word in SUMMARY_WORDS):
        return False
    return any(_squash(word) in squashed for word in FEVER_WORDS)


async def run_visit_summary(
    task: DomainTask, context: HealthContext, client: LLMClient | None
) -> DomainAgentResult:
    context.state.request_texts = tuple(task.request_texts)
    gate = context.state.gate
    assert gate is not None  # run() 이 채운다

    # 최근 14일 건강 기록 한 번. 응급 판정 · 열 타임라인 · 기록 없음 판정이 같이 쓴다
    observations = await context.ports.memory.health_observations(
        child_id=context.child_id,
        date_from=_first_day(context),
        date_to=context.today,
    )

    # ① 응급 안내는 무엇보다 먼저다. 이후 경로가 무엇이든 맨 앞에 남는다
    infant = infant_fever(
        observations, months=gate.stage.months, now=context.now, timezone=context.timezone
    )
    if infant:
        context.state.readouts.append(READOUTS.render(EMERGENCY))

    # ② 열 질문은 모델 없이 나열만 한다
    if is_fever_question(task.request_texts):
        try:
            context.state.readouts += await _fever_readouts(context, observations)
        except Exception as exc:
            if not infant:
                raise
            return _emergency_only(context, exc)
        return _finish(context)

    # ③ 기록이 없으면 모델을 부르지 않는다. 약 행도 건강 기록이다
    if not observations:
        context.state.readouts.append(READOUTS.render(VISIT_NO_RECORD))
        return _finish(context)

    try:
        outcome = await _summarize(task, context, client)
    except Exception as exc:
        if not infant:
            raise
        return _emergency_only(context, exc)
    if infant and outcome.status == "failed":
        # failed 결과는 pipeline 이 버린다 — 응급 안내가 같이 사라지면 안 된다
        return _finish(context, model_calls=outcome.model_calls)
    return outcome


async def _summarize(
    task: DomainTask, context: HealthContext, client: LLMClient | None
) -> DomainAgentResult:
    """④ 모델 한 번(loop 한 번). 출력 tool 이 통과하면 끝난다."""
    gate = context.state.gate
    assert gate is not None
    allowed = tools_for(TASK, gate)
    llm = client or LLMClient(role="health")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context, TASK)},
        {"role": "user", "content": "\n".join(task.request_texts)},
    ]

    async def execute(name: str, arguments: dict[str, Any]) -> ToolResult:
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
            "Health 진료 준비 라벨이 정리를 내지 못했다 run_id=%s ended_by=%s",
            context.run_id,
            loop.ended_by,
        )
        return _finish(context, status="failed", model_calls=1)
    return _finish(context, model_calls=1)


async def _fever_readouts(
    context: HealthContext, observations: Sequence[HealthObservation]
) -> list[Readout]:
    """최근 48시간의 열 타임라인. 코스는 그 구간과 겹치는 것(어제 끝난 것 포함), 회차는 앞날까지."""
    first_day = (context.now - WINDOW).astimezone(context.timezone).date()
    medication = context.ports.medication
    courses = await medication.courses_overlapping(
        child_id=context.child_id, date_from=first_day, date_to=context.today
    )
    # 다음 예정은 오늘 이후 회차 행에서 나온다 — 코스 마지막 날까지 읽는다
    last_day = max([context.today, *(course.ends_on for course in courses)])
    doses = (
        await medication.doses(
            schedule_ids=[course.id for course in courses], date_from=first_day, date_to=last_day
        )
        if courses
        else []
    )
    return list(
        build_fever_timeline(
            observations, courses, doses, now=context.now, timezone=context.timezone
        )
    )


def _emergency_only(context: HealthContext, exc: Exception) -> DomainAgentResult:
    """3개월 미만 발열 run 에서 나머지가 예외로 끝났다. 이미 만든 readout(응급 안내)만 내보낸다.

    `completed` 로 낸다 — failed 는 pipeline 이 버려서 응급 안내가 사라진다. 정리 실패를 알리는
    문구는 만들지 않는다(맞는 키가 없다). `Exception` 만 잡는다: 취소 · 20초 timeout 은
    `BaseException` 이라 그대로 pipeline 으로 간다. 로그에는 예외 종류만 싣는다 — 메시지에
    원문이 들어 있을 수 있다.
    """
    logger.warning(
        "Health 진료 준비가 예외로 끝나 응급 안내만 낸다 run_id=%s error=%s",
        context.run_id,
        type(exc).__name__,
    )
    return _finish(context)


def _first_day(context: HealthContext) -> date:
    """오늘을 포함해 14일 전의 첫날 — 조회 tool 의 `_first_day` 와 같은 세는 법."""
    return context.today - timedelta(days=RECORD_DAYS - 1)


def _finish(
    context: HealthContext, *, status: AgentStatus = "completed", model_calls: int = 0
) -> DomainAgentResult:
    return result(
        TASK,
        status,
        readouts=tuple(context.state.readouts),
        model_calls=model_calls,
    )
