"""모델에 system 프롬프트를 보내는 자리가 지문에 등록된 글만 보내는지 AST 로 훑는다 (#770 BE 리뷰).

지문(`prompt_fingerprint`)은 등록된 함수 · 상수로 만든 글이다.
실제로 모델에 가는 `messages` 가 그 글과 이어져 있지 않으면, system 메시지를 다른 상수로 바꾸거나
뒤에 문장을 붙여도 지문 테스트가 통과한다.
그래서 `app/` 의 모든 system 메시지 자리를 찾아 내용이 아래 표의 식 중 하나인지 본다.

- system 메시지 자리: `{"role": "system", "content": X}` · `dict(role="system", content=X)`,
  판정기의 `{"instructions": X}`
- X 가 함수 안 변수면 그 함수 안의 대입을 따라가 그 값을 본다. 모듈 상수는 이름으로 본다
- 호출은 인자를 빼고 비교한다(`build_system_prompt(...)`) — 인자는 run 마다 다르다. 대신 그 함수를
  어느 모듈에서 import 했는지까지 본다

새 자리를 만들면 그 Agent 에서 `register_prompt` 하고, 평가 문서(`docs/agents/eval/`)를 더하고,
이 표에 한 줄을 더한다.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.agents.prompt_registry import PROMPT_NAMES

APP = Path(__file__).resolve().parents[4] / "app"
BUILD = "build_system_prompt(...)"


@dataclass(frozen=True)
class Site:
    prompt: str  # 등록 이름
    allowed: frozenset[str]  # 허용하는 content 식 (호출은 인자를 뺀 모양)
    builder_module: str | None = None  # build_system_prompt 를 import 해야 하는 모듈


SITES: dict[str, Site] = {
    "agents/supervisor/agent.py": Site(
        "supervisor", frozenset({BUILD}), "app.agents.supervisor.prompt"
    ),
    "agents/memory/agent.py": Site("memory", frozenset({BUILD}), "app.agents.memory.prompt"),
    "agents/food/agent.py": Site("food", frozenset({BUILD}), "app.agents.food.prompt"),
    "agents/activity/agent.py": Site("activity", frozenset({BUILD}), "app.agents.activity.prompt"),
    "agents/activity/event_schedule.py": Site(
        "activity_event_schedule", frozenset({"SYSTEM_PROMPT"})
    ),
    "agents/growth/agent.py": Site("growth", frozenset({BUILD}), "app.agents.growth.prompt"),
    "agents/health/labels/medication.py": Site(
        "health", frozenset({BUILD}), "app.agents.health.prompt"
    ),
    "agents/health/labels/visit.py": Site("health", frozenset({BUILD}), "app.agents.health.prompt"),
    "agents/curator/embedding/jev.py": Site("curator_judge", frozenset({"self._prompt"})),
    "providers/meal_plan/image_ocr.py": Site("meal_ocr", frozenset({"SYSTEM_PROMPT"})),
    "providers/notice/extractor.py": Site(
        "notice_ocr", frozenset({"SYSTEM_PROMPT", "SYSTEM_PROMPT + IMAGE_ADDENDUM"})
    ),
}

# 모델 SDK 를 직접 부르는 곳. 이 밖에서 부르면 위 표를 거치지 않는 길이 생긴다
SDK_CALLERS = frozenset(
    {
        "agents/common/llm_client.py",
        "providers/meal_plan/image_ocr.py",
        "providers/notice/extractor.py",
    }
)


def _shape(node: ast.expr) -> str:
    """content 식의 모양. 호출은 인자를 뺀다."""
    if isinstance(node, ast.Call):
        return f"{ast.unparse(node.func)}(...)"
    return ast.unparse(node)


def _assignments(scope: ast.AST) -> dict[str, list[ast.expr]]:
    found: dict[str, list[ast.expr]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            found.setdefault(node.target.id, []).append(node.value)
    return found


def _contents(tree: ast.Module) -> list[tuple[int, ast.expr]]:
    """(줄 번호, content 식) — system 메시지 · 판정기 instructions 자리마다."""
    found: list[tuple[int, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            pairs = {
                key.value: value
                for key, value in zip(node.keys, node.values, strict=True)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            role = pairs.get("role")
            if isinstance(role, ast.Constant) and role.value == "system" and "content" in pairs:
                found.append((node.lineno, pairs["content"]))
            if "instructions" in pairs:
                found.append((node.lineno, pairs["instructions"]))
        elif isinstance(node, ast.Call) and ast.unparse(node.func) == "dict":
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            role = kwargs.get("role")
            if isinstance(role, ast.Constant) and role.value == "system" and "content" in kwargs:
                found.append((node.lineno, kwargs["content"]))
    return found


def _resolved(tree: ast.Module, line: int, expr: ast.expr) -> list[ast.expr]:
    """이름이면 그 줄을 감싼 함수 안의 대입 값들로 바꾼다. 모듈 상수는 이름 그대로 둔다."""
    if not isinstance(expr, ast.Name):
        return [expr]
    scope: ast.AST = tree
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.lineno <= line <= (
            node.end_lineno or node.lineno
        ):
            scope = node  # 가장 안쪽 함수가 마지막에 남는다
    if scope is tree:
        return [expr]  # 모듈 상수는 이름 그대로 본다 — 상수 자체는 지문에 들어간다
    return _assignments(scope).get(expr.id) or [expr]


def _builder_module(tree: ast.Module) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "build_system_prompt" and alias.asname is None for alias in node.names
        ):
            return node.module
    return None


def _python_files() -> list[Path]:
    return sorted(path for path in APP.rglob("*.py") if "__pycache__" not in path.parts)


def _rel(path: Path) -> str:
    return path.relative_to(APP).as_posix()


def test_system_메시지를_만드는_자리는_등록_목록에_있다():
    unknown = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _contents(tree) and _rel(path) not in SITES:
            unknown.append(_rel(path))
    assert unknown == [], (
        f"모델에 system 프롬프트를 보내는 새 자리가 있습니다: {unknown}. "
        "그 Agent 에서 register_prompt 하고 평가 문서(docs/agents/eval/)를 더한 뒤 "
        "이 테스트의 SITES 에 한 줄을 더하세요."
    )


@pytest.mark.parametrize("rel", sorted(SITES))
def test_system_메시지는_등록된_함수나_상수만_보낸다(rel):
    site = SITES[rel]
    path = APP / rel
    tree = ast.parse(path.read_text(encoding="utf-8"))
    contents = _contents(tree)
    assert contents, f"{rel}: system 메시지 자리를 못 찾았습니다 — 옮겼으면 SITES 를 고치세요."

    bad = [
        f"{line}: {_shape(value)}"
        for line, expr in contents
        for value in _resolved(tree, line, expr)
        if _shape(value) not in site.allowed
    ]
    assert bad == [], (
        f"{rel}: system 메시지가 지문에 등록된 글이 아닙니다 {bad}. 허용: {sorted(site.allowed)}. "
        "프롬프트를 바꾸려면 등록된 함수 · 상수를 고치고 평가 문서에 행을 더하세요."
    )
    if site.builder_module:
        assert _builder_module(tree) == site.builder_module, (
            f"{rel}: build_system_prompt 를 {site.builder_module} 에서 import 해야 합니다."
        )


def test_등록_목록은_프롬프트_10곳을_빠짐없이_가리킨다():
    assert {site.prompt for site in SITES.values()} == set(PROMPT_NAMES)


def test_모델_SDK_는_정해진_곳에서만_부른다():
    callers = {
        _rel(path)
        for path in _python_files()
        if any(
            isinstance(node, ast.Attribute)
            and node.attr == "create"
            and ast.unparse(node.value).endswith("chat.completions")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    }
    assert callers <= SDK_CALLERS, (
        f"모델 SDK 를 직접 부르는 새 자리: {sorted(callers - SDK_CALLERS)}. LLMClient 를 거치거나, "
        "지문 등록 · 평가 문서 · SITES 를 같이 더하세요."
    )


# ── 검사기 자체 ─────────────────────────────────────────────────────


def _check(source: str, allowed: set[str]) -> list[str]:
    tree = ast.parse(source)
    return [
        _shape(value)
        for line, expr in _contents(tree)
        for value in _resolved(tree, line, expr)
        if _shape(value) not in allowed
    ]


def test_검사기는_다른_상수로_바꾼_system_메시지를_잡는다():
    source = 'def run():\n    return [{"role": "system", "content": NEW_SYSTEM_PROMPT}]\n'
    assert _check(source, {BUILD}) == ["NEW_SYSTEM_PROMPT"]


def test_검사기는_뒤에_문장을_붙인_system_메시지를_잡는다():
    source = (
        "def run(c):\n"
        '    return [{"role": "system", "content": build_system_prompt(c) + "\\n추가 지시"}]\n'
    )
    assert _check(source, {BUILD}) != []


def test_검사기는_변수를_거친_content_도_따라간다():
    tail = '    return [{"role": "system", "content": system}]\n'
    ok = "def run(c):\n    system = build_system_prompt(c)\n" + tail
    bad = 'def run(c):\n    system = OTHER + "!"\n' + tail

    assert _check(ok, {BUILD}) == []
    assert _check(bad, {BUILD}) != []


def test_검사기는_dict_호출_모양도_찾는다():
    source = 'def run():\n    return [dict(role="system", content=OTHER)]\n'
    assert _check(source, {BUILD}) == ["OTHER"]
