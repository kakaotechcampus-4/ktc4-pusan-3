"""안전 정보는 task에서 한 번만 읽는다.

health_safety를 읽는 곳은 `build_gate` 하나. 읽은 값은 `FoodRunState.safety`에 남고,
필터를 부르는 곳(후보 풀 사전 필터 · 응답 뒤 재필터 · 급식 조회 · 급식 갱신 뒤 재검사)은
그 값만 쓴다.

나중에 tool을 더해도 걸리도록 Food 코드에서 `SafetyReader` 호출을 찾는다. 주석 · docstring 은
걸리지 않게 AST로 실제 호출만 본다.
"""

import ast
from pathlib import Path

import app.agents.food as food_package

FOOD_DIR = Path(food_package.__file__).parent
# 읽어도 되는 곳 강제: build_gate(context.py)와 포트 정의·구현(store/)
_ALLOWED = {FOOD_DIR / "context.py"}
_ALLOWED_DIRS = {FOOD_DIR / "store"}


def _reads_safety_port(source: str) -> bool:
    """`<무엇>.food_safety(...)` 호출이나 `<무엇>.ports.safety` 접근이 있는가."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            if node.attr == "food_safety":
                return True
            if (
                node.attr == "safety"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "ports"
            ):
                return True
    return False


def test_검사기는_포트를_다시_읽는_코드를_잡는다() -> None:
    # 검사기가 아무것도 못 잡으면 아래 테스트는 늘 통과한다. 먼저 잡히는지 확인
    assert _reads_safety_port("entries = await context.ports.safety.food_safety(child_id=c)")
    assert _reads_safety_port("reader = context.ports.safety")
    assert not _reads_safety_port('"""context.ports.safety.food_safety 를 다시 읽지 않는다"""')
    assert not _reads_safety_port("entries = context.state.safety")


def test_build_gate_밖의_Food_코드는_안전_포트를_읽지_않는다() -> None:
    files = [
        path
        for path in FOOD_DIR.rglob("*.py")
        if path not in _ALLOWED and not any(d in path.parents for d in _ALLOWED_DIRS)
    ]
    assert files  # 훑을 파일이 없으면 검사가 빈 목록을 보고 통과한다

    offenders = [
        str(path.relative_to(FOOD_DIR))
        for path in files
        if _reads_safety_port(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], "context.state.safety 를 쓴다 — 포트를 다시 읽지 않는다"
