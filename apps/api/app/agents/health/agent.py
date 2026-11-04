"""Health Agent 진입점 — 라벨 · 게이트 · 코드 닫힘 · 라벨 경로.

순서
  ① 라벨이 비었거나 모르는 값이면 아무것도 읽지 않고 `unsupported` — 짐작하지 않는다
  ② build_gate 로 월령 · 동의 · 안전 조회를 모은다
  ③ 동의가 없으면 전 라벨이 `blocked.consent` 코드 문구로 끝 — 모델 0회
  ④ 구현된 라벨 경로가 없으면 `unsupported` — 모델 0회. 병원 찾기만 준비 중 문구 한 줄이 붙는다
  ⑤ 구현된 라벨은 `_LABEL_RUNNERS` 의 경로가 처리한다

라벨 경로는 라벨 이슈가 `_LABEL_RUNNERS` 와 `registry.IMPLEMENTED_TASKS` 에 같이 더한다
(지금은 `schedule_check` · `medication` · `care_handoff` · `visit_summary` 가 있다).
저장은 하지 않는다 — 복약 초안(`medication_drafts`)과 일정 초안(`event_requests`)은 결과로만
나가고, 보호자가 제출해야 백엔드가 쓴다. 복약 중단만 예외다 — 한 번 물어 '네' 를 받은 다음 run 이
`medication` 경로에서 쓴다.
"""

import logging
from collections.abc import Awaitable, Callable

from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.health.context import HealthContext, build_gate
from app.agents.health.envelope import result as _result
from app.agents.health.labels.handoff import run_care_handoff
from app.agents.health.labels.medication import is_dose_report, run_medication
from app.agents.health.labels.schedule import run_schedule_check
from app.agents.health.labels.visit import run_visit_summary
from app.agents.health.readouts import PLACE_PREPARING, READOUTS
from app.agents.health.registry import HOOK_HANDLERS, IMPLEMENTED_TASKS, closed_readout_key
from app.agents.health.schemas.task import HealthTaskType, task_type_of

logger = logging.getLogger(__name__)

__all__ = [
    "is_dose_report",
    "registered_titles",
    "run",
    "run_hooks",
]

LabelRunner = Callable[[DomainTask, HealthContext, LLMClient | None], Awaitable[DomainAgentResult]]
# 라벨 이슈가 더한다. 키는 `registry.IMPLEMENTED_TASKS` 와 같아야 한다 (테스트가 본다)
_LABEL_RUNNERS: dict[HealthTaskType, LabelRunner] = {
    HealthTaskType.SCHEDULE_CHECK: run_schedule_check,
    HealthTaskType.MEDICATION: run_medication,
    HealthTaskType.CARE_HANDOFF: run_care_handoff,
    HealthTaskType.VISIT_SUMMARY: run_visit_summary,
}

# 라벨 경로가 아직 없지만 보호자에게 이유를 말해 줄 라벨. 경로가 붙으면 여기서 뺀다
_PREPARING: dict[HealthTaskType, str] = {HealthTaskType.PLACE_LOOKUP: PLACE_PREPARING}


async def run(
    task: DomainTask, context: HealthContext, *, client: LLMClient | None = None
) -> DomainAgentResult:
    """Health 요청 하나를 처리한다. 결과는 공통 `DomainAgentResult` 다."""
    task_type = task_type_of(task)
    if task_type is None:
        return _result(None, "unsupported")

    gate = await build_gate(context, task_type)
    context.state.gate = gate

    key = closed_readout_key(task_type, gate)
    if key is not None:
        return _result(task_type, "blocked", readouts=(READOUTS.render(key),))

    if task_type not in IMPLEMENTED_TASKS:
        preparing = _PREPARING.get(task_type)
        readouts = () if preparing is None else (READOUTS.render(preparing),)
        return _result(task_type, "unsupported", readouts=readouts)

    return await _LABEL_RUNNERS[task_type](task, context, client)


async def run_hooks(context: HealthContext) -> DomainAgentResult:
    """Memory 가 `observation_health` 를 저장한 직후 pipeline 이 부르는 hook 묶음.

    `HOOK_HANDLERS` 를 등록된 순서대로 돌려 readout 을 모은다(응급이 먼저). 라벨이 없어 `task_type`
    은 None 이고 모델은 부르지 않는다. 쓰지 않는다. hook.py 가 registry 를 부르면 순환이라 hook.py
    가 아니라 여기에 둔다. hook 마다 따로 잡는다 — 하나가 터졌다고 다른 hook 이 이미 만든 안내
    (응급 readout)까지 버리면 안 된다. 예외 메시지에는 생일 · 기록이 섞일 수 있어 종류만 남긴다.
    """
    readouts: list[Readout] = []
    for name, handler in HOOK_HANDLERS.items():
        try:
            readouts.extend(await handler(context))
        except Exception as exc:
            logger.error("Health hook 실패 hook=%s error=%s", name, type(exc).__name__)
    return _result(None, "completed", readouts=tuple(readouts))


async def registered_titles(context: HealthContext) -> tuple[str, ...] | None:
    """이 아이의 진행 중 복약 코스 이름들. 읽지 못했으면 None.

    pipeline 이 먹인 약 문장을 등록된 약과 대조하려고 기록 단계 전에 부른다. "진행 중" 은 복약 화면
    목록이 싣는 코스와 같은 조건이다. 동의가 없으면 읽지 않는다. 조회가 실패하면 None 이다 — 그러면
    pipeline 은 대조하지 않고 Memory 가 먹인 약을 평소처럼 기록한다. 예외 메시지에는 약 이름이
    섞일 수 있어 종류만 남긴다.
    """
    try:
        if not await context.ports.consent.child_health_granted(child_id=context.child_id):
            return None
        courses = await context.ports.medication.active_courses(
            child_id=context.child_id, today=context.today
        )
    except Exception as exc:
        logger.error("등록된 약 조회 실패 error=%s", type(exc).__name__)
        return None
    return tuple(course.title for course in courses)
