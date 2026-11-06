"""common 은 도메인 Agent 패키지를 import 하지 않는다 (docs/agents/README.md §6).

의존 방향은 `food · activity · growth → common` 하나다. 공용 판정을 common 으로 올릴 때
Agent 의 포트(`food.store.ports`)나 어휘를 끌고 오면 그 방향이 거꾸로 뒤집힌다 — 구조적으로
맞는 Protocol 로 받는다.
"""

import ast
from pathlib import Path

import pytest

import app.agents.common as common

COMMON_DIR = Path(common.__file__).parent
# common 이 읽을 수 있는 Agent 쪽 패키지는 없다. 한 패키지만 허용한다
ALLOWED = "app.agents.common"
AGENTS = "app.agents"


def _package_of(path: Path) -> str:
    """파일이 든 패키지 — `common/a/b.py` 는 `app.agents.common.a`."""
    return ".".join([ALLOWED, *path.relative_to(COMMON_DIR).parent.parts])


def _relative_base(package: str, level: int) -> str:
    """`from .x` (level 1)는 그 패키지, `from ..x` (level 2)는 한 단계 위."""
    parts = package.split(".")
    return ".".join(parts[: len(parts) - (level - 1)])


def _agent_imports(path: Path, package: str | None = None) -> list[str]:
    package = package or _package_of(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            base = _relative_base(package, node.level)
            if node.module:
                names = [f"{base}.{node.module}"]
            else:
                names = [f"{base}.{alias.name}" for alias in node.names]
        for name in names:
            inside = name == AGENTS or name.startswith(AGENTS + ".")
            allowed = name == ALLOWED or name.startswith(ALLOWED + ".")
            if inside and not allowed:
                found.append(name)
    return found


@pytest.mark.parametrize(
    "path", sorted(COMMON_DIR.rglob("*.py")), ids=lambda p: str(p.relative_to(COMMON_DIR))
)
def test_common_은_다른_Agent_패키지를_import_하지_않는다(path):
    assert _agent_imports(path) == []


def test_경계_검사가_상대_import_도_잡는다(tmp_path):
    """`from ..food.store.ports import X` 는 level 2 라 절대 import 만 보면 놓친다 (#582 리뷰)."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from ..food.store.ports import SafetyEntry\n"
        "from .. import activity\n"
        "from . import allergy\n"
        "from .allergy import read_label\n",
        encoding="utf-8",
    )
    # common 안의 파일(app.agents.common 패키지)로 본다
    assert _agent_imports(bad, package="app.agents.common") == [
        "app.agents.food.store.ports",
        "app.agents.activity",
    ]


def test_경계_검사가_import_를_실제로_잡는다(tmp_path):
    """검사기가 아무것도 못 잡는 껍데기가 아닌지 — 위반 파일을 만들어 본다."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from app.agents.food.store.ports import SafetyEntry\n"
        "import app.agents.activity.tools.filters\n"
        "from app.agents.common.allergy import read_label\n",
        encoding="utf-8",
    )
    assert _agent_imports(bad, package="app.agents.common") == [
        "app.agents.food.store.ports",
        "app.agents.activity.tools.filters",
    ]
