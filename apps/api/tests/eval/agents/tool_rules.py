"""라이브 eval 의 tool 금지 규칙 (#816) — 판정 도우미와 정의 검사가 같이 쓴다.

- `forbidden_failures(case, called)` — 케이스가 금지한 tool 을 불렀으면 실패 문장. Food · Health
  라이브 eval 이 실패에 더하고, `case.c1` 이면 C1 로 센다.
- `rules()` / `opened_tools()` / `check()` — LLM 없이 금지한 tool 이 그 케이스 gate 에서
  열려 있는지 본다. 오타이거나 닫힌 tool 을 금지하면 그 금지는 늘 통과한다.

조회 tool 은 필수로 두지 않는다 — Food 에서 정한 "조회 tool 은 선택이다". Memory 의 기존
`required_tools` 만 그대로 둔다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# 지워 둔 tool 인데 모델이 다시 부르는 회귀를 잡으려고 일부러 금지에 남긴 이름.
# 호출한 이름은 거절돼도 `calls` 에 남아 금지 검사에 걸린다 (Memory T06 — 65103a52 에서 지움).
# 새 이름을 여기 더하려면 이유를 적는다 — 오타를 숨기는 자리가 되면 안 된다
REMOVED_TOOLS: frozenset[str] = frozenset({"create_reminder"})


@dataclass(frozen=True)
class ToolRule:
    suite: str
    case_id: str
    required: frozenset[str]
    forbidden: frozenset[str]
    c1: bool
    ci: bool
    mutating: bool = False  # Memory — 쓰기 전부를 금지한다 (이름이 아니라 접두사로)


def forbidden_failures(case: Any, called: Sequence[str]) -> list[str]:
    """`case.forbidden_tools` 를 부른 이름. 부르지 않았으면 빈 목록."""
    used = sorted(set(called) & set(case.forbidden_tools))
    return [f"금지된 호출: {used}"] if used else []


def check(rule: ToolRule, opened: frozenset[str]) -> list[str]:
    """필수 · 금지 tool 이 그 케이스에 열려 있는지. 지워 둔 tool 은 `REMOVED_TOOLS` 만 봐준다."""
    problems = []
    for name in sorted(rule.required | rule.forbidden):
        if name in REMOVED_TOOLS and name in rule.forbidden:
            continue
        if name not in opened:
            problems.append(f"{rule.suite} {rule.case_id}: {name} 는 이 케이스에서 열리지 않는다")
    return problems


def rules() -> list[ToolRule]:
    """Memory · Food · Health · Growth 케이스에서 모은 규칙. Growth 는 CI 표시만 있다."""
    from tests.eval.agents.food import test_food_run as food
    from tests.eval.agents.growth import test_growth_live as growth
    from tests.eval.agents.health import cases as health
    from tests.eval.agents.memory import test_memory as memory

    found: list[ToolRule] = []
    for m in memory.CASES:
        mutating = memory.MUTATING in m.forbidden_tools
        found.append(
            ToolRule(
                "memory",
                m.case_id,
                frozenset(m.required_tools),
                frozenset(m.forbidden_tools - {memory.MUTATING}),
                m.c1,
                m.ci,
                mutating,
            )
        )
    found += [
        ToolRule("food", c.case_id, frozenset(), c.forbidden_tools, c.c1, c.ci) for c in food.CASES
    ]
    found += [
        ToolRule("health", c.case_id, frozenset(), c.forbidden_tools, c.c1, c.ci)
        for c in health.CASES
    ]
    found += [
        ToolRule("growth", c.case_id, frozenset(), frozenset(), False, c.ci) for c in growth.CASES
    ]
    return found


def _activity_ci() -> list[str]:
    from tests.eval.agents.activity import test_activity_live as activity

    return sorted(
        name
        for name, value in vars(activity).items()
        if name.startswith("test_")
        and any(mark.name == "tool_ci" for mark in getattr(value, "pytestmark", ()))
    )


def ci_cases() -> dict[str, list[str]]:
    return {"activity": _activity_ci()}


async def opened_tools(suite: str, case_id: str) -> frozenset[str]:
    """그 케이스가 실제로 여는 tool. LLM 은 부르지 않는다."""
    if suite == "memory":
        from app.agents.memory.bundles import tools_for as memory_tools
        from tests.eval.agents.memory import test_memory as memory

        case = next(c for c in memory.CASES if c.case_id == case_id)
        return frozenset(memory_tools(memory._task_for(case)))  # noqa: SLF001
    if suite == "food":
        from app.agents.food.context import build_gate
        from app.agents.food.registry import tools_for
        from app.agents.food.schemas.common import FoodTaskType
        from tests.eval.agents.food import test_food_run as food
        from tests.eval.agents.food.world import build_world

        case = next(c for c in food.CASES if c.case_id == case_id)
        world = build_world(case.world, now=food.NOW, run_id=f"rules-{case_id}")
        return frozenset(tools_for(FoodTaskType(case.task), await build_gate(world.context)))
    if suite == "health":
        from app.agents.health.context import build_gate
        from app.agents.health.registry import tools_for
        from tests.eval.agents.health import cases as health
        from tests.eval.agents.health.world import DAY, NIGHT, build_world

        case = next(c for c in health.CASES if c.case_id == case_id)
        world = build_world(case.world, now=NIGHT if case.night else DAY, run_id=f"rules-{case_id}")
        return frozenset(tools_for(case.label, await build_gate(world.context, case.label)))
    return frozenset()
