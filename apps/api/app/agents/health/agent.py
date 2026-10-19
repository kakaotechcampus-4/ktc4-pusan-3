"""Health Agent 진입점 — 라벨 · 게이트 · 코드 닫힘 · 라벨 경로.

순서
  ① 라벨이 비었거나 모르는 값이면 아무것도 읽지 않고 `unsupported` — 짐작하지 않는다
  ② build_gate 로 월령 · 동의 · 안전 조회를 모은다
  ③ 동의가 없으면 전 라벨이 `blocked.consent` 코드 문구로 끝 — 모델 0회
  ④ 구현된 라벨 경로가 없으면 `unsupported` — 모델 0회, readout 없음
  ⑤ 구현된 라벨은 `_LABEL_RUNNERS` 의 경로가 처리한다

라벨 경로는 라벨 이슈가 `_LABEL_RUNNERS` 와 `registry.IMPLEMENTED_TASKS` 에 같이 더한다
(지금은 `schedule_check` 만 있다). 저장은 하지 않는다 — 복약 초안(`medication_drafts`)과 일정
초안(`event_requests`)은 결과로만 나가고, 보호자가 제출해야 백엔드가 쓴다. pipeline 에는 아직
연결하지 않았다 — 연결 이슈(docs/agents/health/health_plan.md §16).
"""

from collections.abc import Awaitable, Callable

from app.agents.common.llm_client import LLMClient
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.health.context import HealthContext, build_gate
from app.agents.health.envelope import result as _result
from app.agents.health.labels.schedule import run_schedule_check
from app.agents.health.readouts import READOUTS
from app.agents.health.registry import IMPLEMENTED_TASKS, closed_readout_key
from app.agents.health.schemas.task import HealthTaskType, task_type_of

LabelRunner = Callable[[DomainTask, HealthContext, LLMClient | None], Awaitable[DomainAgentResult]]
# 라벨 이슈가 더한다. 키는 `registry.IMPLEMENTED_TASKS` 와 같아야 한다 (테스트가 본다)
_LABEL_RUNNERS: dict[HealthTaskType, LabelRunner] = {
    HealthTaskType.SCHEDULE_CHECK: run_schedule_check,
}


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
        return _result(task_type, "unsupported")

    return await _LABEL_RUNNERS[task_type](task, context, client)
