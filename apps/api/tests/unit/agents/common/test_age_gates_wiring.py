"""월령 숫자가 `age_gates.yaml` 에서만 온다 (#372).

값이 같은지만 보면 코드에 숫자가 그대로 남아 있어도 통과한다. 상수의 오른쪽이 숫자 리터럴이 아닌지를
소스에서 직접 본다 — 파일을 바꿔도 코드의 숫자가 그대로 남는 것을 막으려는 것이다.
"""

import ast
import importlib
import inspect

import pytest

from app.agents.common.reference import age_gates

# (모듈, 상수 이름, 파일의 값을 꺼내는 함수)
WIRED = [
    ("app.agents.curator.embedding.linker", "AFFINITY_MIN_MONTH", lambda g: g.affinity_min_month),
    ("app.agents.activity.gating", "AFFINITY_MIN_MONTH", lambda g: g.affinity_min_month),
    (
        "app.agents.activity.review",
        "TOGETHER_ONLY_BELOW_MONTH",
        lambda g: g.together_only_below_month,
    ),
    ("app.agents.growth.gating", "EDUCATION_MIN_MONTH", lambda g: g.education_min_month),
    ("app.agents.growth.gating", "STEP_MIN_MONTH", lambda g: g.step_min_month),
    ("app.agents.growth.gating", "MANNER_MIN_MONTH", lambda g: g.manner_min_month),
    ("app.agents.growth.gating", "HABIT_MIN_MONTH", lambda g: g.habit_min_month),
    ("app.agents.common.reference_docs", "DOC_MONTH_LIMIT", lambda g: g.v1_month_limit),
]


@pytest.mark.parametrize(("module", "name", "pick"), WIRED)
def test_상수는_파일의_값이다(module, name, pick):
    assert getattr(importlib.import_module(module), name) == pick(age_gates())


@pytest.mark.parametrize(("module", "name", "pick"), WIRED)
def test_상수에_숫자_리터럴이_남아_있지_않다(module, name, pick):
    tree = ast.parse(inspect.getsource(importlib.import_module(module)))
    assigned = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and name
        in {
            t.id
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(t, ast.Name)
        }
    ]
    assert len(assigned) == 1, f"{module}.{name} 대입이 하나가 아니다"
    assert not isinstance(assigned[0].value, ast.Constant), f"{module}.{name} 이 숫자 리터럴이다"


@pytest.mark.parametrize(
    "module",
    ["app.rules.age", "app.agents.activity.gating", "app.agents.growth.gating"],
)
def test_age_gates_TODO_가_남아_있지_않다(module):
    assert "age_gates" not in _todo_lines(importlib.import_module(module))


def _todo_lines(module) -> str:
    return "\n".join(line for line in inspect.getsource(module).splitlines() if "TODO" in line)


def test_rules_age_에_단계_경계_숫자가_남아_있지_않다():
    from app.rules import age

    assert not hasattr(age, "_STAGE_BOUNDARIES")
    assert not hasattr(age, "V1_MONTH_LIMIT")
