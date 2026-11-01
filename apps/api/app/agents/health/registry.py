"""Health task와 Gate 조건에 따라 모델에 노출할 tool과 조기 종료 여부를 결정한다.

어떤 tool을 사용할 수 있는지는 모델이 직접 판단하지 않고 이 코드에서 결정한다.

표는 Health_Tool_명세.md §1 전체를 처음부터 담는다 — 다음 이슈들이 표를 고치지 않고 정의와
핸들러만 더하게 하려는 것이다. **실제로 열리는 것은 구현된 tool 뿐이다**: `tools_for` 는 표에 있는
이름 중 `TOOL_HANDLERS` 에도 있는 것만 내고, 라벨이 `IMPLEMENTED_TASKS` 에 없으면 아무것도 열지
않는다.

task별 모델 tool (코드 tool 은 모델에 노출하지 않는다 — 노출하면 Gate 를 우회할 수 있다)

- visit_summary   search_health_observations(기간 · 증상 하나를 정한다) ·
                  compose_symptom_timeline(출력, 인자 없음). 모델은 줄을 고르지 않는다 — 식사 ·
                  복약 체크는 코드 tool 이 늘 넣는다(모델에게 고르게 하면 건너뛴다). 조회 결과는
                  줄 수뿐이다.
                  show_fever_timeline(출력, 인자 없음)은 열 표현 표에 안 걸린 열 질문의 출구다
- medication      search_medication_schedules · create / update / stop_medication_schedule
                  (생성 · 수정 · 중단이 출력) · show_dose_status · defer_dose_question(인자 없는
                  읽기 전용 출력 — 표에 안 걸린 현황 · 용량 질문의 출구). 복용 체크는 tool 이
                  아니라 보호자 화면이다
- schedule_check · place_lookup · care_handoff   모델을 부르지 않는다

hook(`check_symptom_repetition` · `lookup_condition_guidance` · `check_infant_fever`)은 Memory 가
`observation_health` 를 저장한 직후 pipeline 이 부른다. 라벨이 아니라 `HOOK_TOOLS` 에 따로 둔다.

닫힘은 하나다 — 건강정보 동의가 없으면 **전 라벨**이 `blocked.consent` 로 닫힌다. 다루는 값이 전부
민감정보라 예외를 두지 않는다. 안전 조회 실패(`safety_ok=False`)로는 닫지 않는다 — 진료 준비는
경고 줄을 붙여 진행하고, 응급 카드는 알레르기 칸에 조회 실패를 적는다. 위치 없음 · 복약 0건 ·
체온 0건처럼 라벨 안의 닫힘은 각 라벨 경로가 맡는다.
"""

from collections.abc import Awaitable, Callable, Collection
from typing import Any

from pydantic import ValidationError

from app.agents.common.gate import Gate
from app.agents.common.tool_schema import ToolDefinition, build_tool_specs
from app.agents.health.context import HealthContext
from app.agents.health.hook import check_infant_fever, check_symptom_repetition
from app.agents.health.readouts import BLOCKED_CONSENT
from app.agents.health.result import ErrorCode, ToolResult, fail
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.schemas.tool_defs import TOOL_DEFINITIONS
from app.agents.health.tools.doses import (
    defer_dose_question,
    search_medication_schedules,
    show_dose_status,
)
from app.agents.health.tools.drafts import (
    create_medication_schedule,
    stop_medication_schedule,
    update_medication_schedule,
)
from app.agents.health.tools.fever import show_fever_timeline
from app.agents.health.tools.records import (
    search_health_observations,
)
from app.agents.health.tools.timeline import compose_symptom_timeline

ToolHandler = Callable[[HealthContext, Any], Awaitable[ToolResult]]

# 명세 §1 "모델 tool" 칸
MODEL_TOOLS: dict[HealthTaskType, frozenset[str]] = {
    HealthTaskType.VISIT_SUMMARY: frozenset(
        {
            "search_health_observations",
            "compose_symptom_timeline",
            "show_fever_timeline",
        }
    ),
    HealthTaskType.SCHEDULE_CHECK: frozenset(),
    HealthTaskType.PLACE_LOOKUP: frozenset(),
    HealthTaskType.MEDICATION: frozenset(
        {
            "search_medication_schedules",
            "create_medication_schedule",
            "update_medication_schedule",
            "stop_medication_schedule",
            "show_dose_status",
            "defer_dose_question",
        }
    ),
    HealthTaskType.CARE_HANDOFF: frozenset(),
}

# 명세 §1 "코드 tool" 칸. 모델에게 보이지 않고 코드가 정해진 지점에서 직접 부른다
CODE_TOOLS: dict[HealthTaskType, frozenset[str]] = {
    HealthTaskType.VISIT_SUMMARY: frozenset(
        {
            "collect_meals",
            "collect_medication_checks",
            "load_health_safety",
            "normalize_symptom_term",
            "group_symptom_episodes",
            "build_fever_timeline",
            "get_next_dose",
            "get_health_phrase",
        }
    ),
    HealthTaskType.SCHEDULE_CHECK: frozenset(
        {
            "compute_checkup_schedule",
            "compute_vaccine_schedule",
            "summarize_checkup_context",
            "get_health_phrase",
        }
    ),
    HealthTaskType.PLACE_LOOKUP: frozenset({"find_pediatric_places"}),
    HealthTaskType.MEDICATION: frozenset(
        {
            "resolve_dose_timing",
            "normalize_symptom_term",
            "get_next_dose",
            "lookup_prescription_draft",
        }
    ),
    HealthTaskType.CARE_HANDOFF: frozenset(
        {
            "search_medication_schedules",
            "load_health_safety",
            "compose_daycare_med_notice",
            "build_emergency_card",
        }
    ),
}

# 라벨이 아니라 Memory 저장 직후 pipeline 이 부른다
HOOK_TOOLS: frozenset[str] = frozenset(
    {"check_symptom_repetition", "lookup_condition_guidance", "check_infant_fever"}
)
# 구현된 hook 함수. 키는 `HOOK_TOOLS` 안의 이름이다. 비어 있으면 eval 의 hook 케이스가 GAP 으로
# 남고, 하나라도 있는데 eval 이 아직 부르지 않으면 실패한다 (tests/eval/agents/health/judges.py)
# 순서가 곧 화면에 그려지는 순서다. 응급 안내가 증상 반복 안내보다 먼저 와야 한다
HOOK_HANDLERS: dict[str, Callable[..., Awaitable[Any]]] = {
    "check_infant_fever": check_infant_fever,
    "check_symptom_repetition": check_symptom_repetition,
}

# 모델이 마지막에 부르는 출력 tool. 이게 없는 라벨은 모델을 부르지 않는다
OUTPUT_TOOLS: dict[HealthTaskType, frozenset[str]] = {
    HealthTaskType.VISIT_SUMMARY: frozenset({"compose_symptom_timeline", "show_fever_timeline"}),
    HealthTaskType.MEDICATION: frozenset(
        {
            "create_medication_schedule",
            "update_medication_schedule",
            "stop_medication_schedule",
            "show_dose_status",
            "defer_dose_question",
        }
    ),
}
MODEL_TASKS: frozenset[HealthTaskType] = frozenset(OUTPUT_TOOLS)

# 라벨 경로가 다 붙은 라벨. 이슈가 라벨 하나를 끝낼 때 여기 더한다 (`agent._LABEL_RUNNERS` 와 같다)
IMPLEMENTED_TASKS: frozenset[HealthTaskType] = frozenset(
    {
        HealthTaskType.SCHEDULE_CHECK,
        HealthTaskType.MEDICATION,
        HealthTaskType.CARE_HANDOFF,
        HealthTaskType.VISIT_SUMMARY,
    }
)

# 같은 run 의 다른 라벨이 읽는 행을 쓰는 라벨. pipeline 이 이 라벨을 먼저 끝낸 뒤 나머지를 돌린다.
# 복약 중단이 같은 run 의 진료 준비 · 전달 서류가 읽는 코스 상태를 바꾼다
WRITING_TASKS: frozenset[HealthTaskType] = frozenset({HealthTaskType.MEDICATION})

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "search_health_observations": search_health_observations,
    "search_medication_schedules": search_medication_schedules,
    "compose_symptom_timeline": compose_symptom_timeline,
    "show_fever_timeline": show_fever_timeline,
    "create_medication_schedule": create_medication_schedule,
    "update_medication_schedule": update_medication_schedule,
    "stop_medication_schedule": stop_medication_schedule,
    "show_dose_status": show_dose_status,
    "defer_dose_question": defer_dose_question,
}
_DEFINITIONS: dict[str, ToolDefinition] = {d.name: d for d in TOOL_DEFINITIONS}
_SPECS_BY_NAME: dict[str, dict[str, Any]] = {
    spec["function"]["name"]: spec
    for spec in build_tool_specs([d for d in TOOL_DEFINITIONS if d.name in TOOL_HANDLERS])
}


def closed_readout_key(task: HealthTaskType, gate: Gate) -> str | None:
    """이 (task, gate) 가 닫혀 있으면 코드 문구 키를, 열려 있으면 None 을 돌려준다.

    닫힌 경로는 모델을 0회 부른다 — `tools_for` 도 이 값이 있으면 빈 튜플을 낸다.
    """
    if not gate.consent_child_health:
        return BLOCKED_CONSENT
    return None


def tools_for(task: HealthTaskType, gate: Gate) -> tuple[str, ...]:
    """이번 task에 모델에게 열 tool. 닫혔거나 구현 전이면 빈 튜플 — 모델을 부르지 않는다.

    순서는 이름순이다 (프롬프트가 흔들리지 않게).
    """
    if closed_readout_key(task, gate) is not None or task not in IMPLEMENTED_TASKS:
        return ()
    return tuple(sorted(MODEL_TOOLS[task] & TOOL_HANDLERS.keys()))


def specs_for(names: Collection[str]) -> list[dict[str, Any]]:
    """`tools_for` 결과 중 정의 · 핸들러가 있는 것만, `TOOL_DEFINITIONS` 순서로."""
    return [_SPECS_BY_NAME[name] for name in _SPECS_BY_NAME if name in names]


async def execute_tool(
    name: str, arguments: dict[str, Any], context: HealthContext, *, allowed: Collection[str]
) -> ToolResult:
    """tool 을 한 번 실행한다. 허용 목록 → 이름 조회 → 인자 검증 → 실행 순서.

    모델에게 안 보여준 tool 을 이름으로 불러도 실행하지 않는다. 핸들러가 낸 예외는 삼키지 않는다.
    """
    definition = _DEFINITIONS.get(name)
    handler = TOOL_HANDLERS.get(name)
    if name not in allowed or definition is None or handler is None:
        return fail(
            None,
            name,
            ErrorCode.TOOL_NOT_ALLOWED,
            f"'{name}' 은 이번 요청에서 쓸 수 없는 tool 이다. 제공된 tool 중에서 고른다.",
        )
    try:
        args = definition.args.model_validate(arguments)
    except ValidationError as exc:
        return fail(
            None, name, ErrorCode.INVALID_ARGS, f"인자가 스키마와 맞지 않는다: {_summarize(exc)}"
        )
    return await handler(context, args)


def _summarize(exc: ValidationError, limit: int = 3) -> str:
    """필드와 사유만 짧게. 값 원문은 싣지 않는다."""
    return " / ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()[:limit]
    )
