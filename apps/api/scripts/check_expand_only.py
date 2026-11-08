"""새 마이그레이션이 expand-only 인지 검사한다 (#782). CI(ci-api.yml)가 돌린다.

    uv run python -m scripts.check_expand_only            # alembic/versions
    uv run python -m scripts.check_expand_only DIR

배포가 실패하면 이미지만 직전 것으로 되돌리고 DB 는 안 되돌린다(docs/ops/deploy-v1.md). 그래서
`upgrade()` 가 직전 코드를 깨면 롤백해도 서비스가 죽는다. 아래를 찾으면 실패한다.

- `drop_column` · `drop_table` · `rename_table`
- `alter_column(new_column_name=…)` · `alter_column(nullable=False)`
- `add_column` 에 `server_default` 없는 NOT NULL 컬럼 — 직전 코드의 INSERT 가 깨진다
- `op.execute` 원문 SQL 의 DROP COLUMN · DROP TABLE · RENAME · SET NOT NULL · ALTER … TYPE

`drop_index` · `drop_constraint` · `type_` 바꾸기는 보지 않는다 — 기존 마이그레이션에서 직전 코드를
깨지 않는 오탐만 나왔다.

검사 대상은 `BASELINE` 의 자손 revision 뿐이다. git diff 를 쓰지 않는 이유 — CI 는 fetch-depth 1
이고, 배포가 부른 실행에는 비교할 base 가 없다. BASELINE 은 이 검사를 넣을 때의 head 로 고정한
출발점이라 올릴 일이 없다. 그 뒤 revision 은 전부 검사 대상이다.

두 번째 배포(앞 배포로 코드가 이미 안 쓰게 된 컬럼을 지우는 것)는 파일에 `# contract: #<앞 PR>`
을 적으면 통과한다.
"""

import argparse
import ast
import re
import sys
from collections.abc import Sequence
from pathlib import Path

# 이 revision 과 그 조상은 검사하지 않는다 — 검사를 넣기 전에 이미 배포된 것들이다
BASELINE = "d5e8a1c4b937"
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MARKER = re.compile(r"#\s*contract:\s*#\d+")
_SQL = re.compile(
    r"DROP\s+(TABLE|COLUMN)|RENAME|SET\s+NOT\s+NULL|ALTER\s+COLUMN\s+\S+\s+(SET\s+DATA\s+)?TYPE",
    re.IGNORECASE,
)


def _meta(tree: ast.Module, name: str) -> object:
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if getattr(target, "id", None) == name and node.value is not None:
            return ast.literal_eval(node.value)
    return None


def _is_false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def find_violations(source: str) -> list[tuple[int, str]]:
    """`upgrade()` 안에서 직전 코드를 깨는 연산 (줄, 무엇)."""
    upgrade = next(
        (
            n
            for n in ast.parse(source).body
            if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        ),
        None,
    )
    if upgrade is None:
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(upgrade):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        kw = {k.arg: k.value for k in node.keywords}
        if attr in ("drop_column", "drop_table", "rename_table"):
            found.append((node.lineno, attr))
        elif attr == "alter_column" and "new_column_name" in kw:
            found.append((node.lineno, "alter_column(new_column_name=…) — 컬럼 이름 바꾸기"))
        elif attr == "alter_column" and _is_false(kw.get("nullable")):
            found.append((node.lineno, "alter_column(nullable=False) — NOT NULL 로 조이기"))
        elif attr == "add_column":
            for col in node.args:
                ckw = {k.arg: k.value for k in col.keywords} if isinstance(col, ast.Call) else {}
                if _is_false(ckw.get("nullable")) and "server_default" not in ckw:
                    found.append((node.lineno, "add_column — server_default 없는 NOT NULL 컬럼"))
        elif attr == "execute" and (m := _SQL.search(ast.unparse(node))):
            found.append((node.lineno, f"execute — 원문 SQL 의 {m.group(0)}"))
    return found


def new_migrations(versions_dir: Path, baseline: str = BASELINE) -> list[Path]:
    """baseline 의 자손 revision 파일. merge revision(down_revision 이 튜플)도 따라간다."""
    children: dict[str, list[tuple[str, Path]]] = {}
    revisions = set()
    for path in sorted(versions_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rev, down = _meta(tree, "revision"), _meta(tree, "down_revision")
        revisions.add(rev)
        for parent in down if isinstance(down, (tuple, list)) else [down]:
            children.setdefault(parent, []).append((rev, path))
    if baseline not in revisions:
        # 조용히 0개로 통과하면 검사가 꺼진 것과 같다
        raise SystemExit(f"BASELINE {baseline} 이 {versions_dir} 에 없다")
    seen: dict[str, Path] = {}
    stack = [baseline]
    while stack:
        for rev, path in children.get(stack.pop(), []):
            if rev not in seen:
                seen[rev] = path
                stack.append(rev)
    return sorted(seen.values())


def check(paths: Sequence[Path]) -> list[tuple[Path, int, str]]:
    bad = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if MARKER.search(source):
            continue
        bad.extend((path, line, what) for line, what in find_violations(source))
    return bad


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("versions_dir", nargs="?", type=Path, default=VERSIONS_DIR)
    args = parser.parse_args(argv)
    bad = check(new_migrations(args.versions_dir))
    for path, line, what in bad:
        print(f"{path.name}:{line}: expand-only 위반 — {what}")
    if bad:
        print(
            "\n롤백은 DB 를 안 되돌려서, 직전 코드가 새 스키마에서 돌아야 한다"
            " (docs/ops/deploy-v1.md).\n"
            "고치는 법: 배포를 두 번에 나눈다 — 먼저 코드가 그 컬럼을 안 쓰게(또는 값을 채우게)\n"
            "배포하고, 다음 PR 의 마이그레이션에서 스키마를 바꾼다. 두 번째 마이그레이션 파일에\n"
            "`# contract: #<앞 배포 PR 번호>` 를 적으면 이 검사를 통과한다."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
