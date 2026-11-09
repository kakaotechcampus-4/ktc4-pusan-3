"""새 마이그레이션이 expand-only 인지 검사한다 (#782). CI(ci-api.yml)가 돌린다.

    uv run python -m scripts.check_expand_only            # alembic/versions
    uv run python -m scripts.check_expand_only DIR

배포가 실패하면 이미지만 직전 것으로 되돌리고 DB 는 안 되돌린다(docs/ops/deploy-v1.md). 그래서
`upgrade()` 가 직전 코드를 깨면 롤백해도 서비스가 죽는다. `upgrade()` 와 거기서 부르는 같은 파일의
함수에서 아래를 찾으면 실패한다.

- `drop_column` · `drop_table` · `rename_table`
- `alter_column(new_column_name=…)` · `alter_column(nullable=False)`
- `add_column` 의 NOT NULL(`primary_key=True` 포함) 컬럼에 `server_default` 가 없다(`None` 포함)
- `alter_column(server_default=None)` — 그 컬럼이 NOT NULL 인 게 보일 때만(`nullable=False` ·
  `existing_nullable=False` · 같은 upgrade 에서 NOT NULL 로 추가). 안 보이면 nullable 일 수 있어
  넘긴다
- 같은 이름의 `drop_constraint` + `create_check_constraint` — CHECK 교체. enum 이 CHECK 라
  값 빼기 · 바꾸기가 이 모양이다. 넓히기인지는 값이 상수 · 헬퍼 인자로 흘러와 코드로 못 가린다
- `alter_column(type_=…)` — VARCHAR(n) → VARCHAR(m ≥ n) · TEXT 처럼 넓히기가 확실한 것만 통과
- `op.execute` 원문 SQL(상수 · 파일 안 변수 · `sa.text`)의 DROP TABLE, ALTER TABLE 의
  DROP [COLUMN] · RENAME · ALTER [COLUMN] … TYPE · SET NOT NULL

`drop_index` · `drop_constraint` 만 있는 것은 보지 않는다 — 기존 마이그레이션에서 직전 코드를 깨지
않는 오탐만 나왔다.

넘기는 표시는 그 연산 바로 위 줄이나 같은 줄의 주석이다. 그 연산 하나만 넘긴다.
- `# contract: #<앞 배포 PR>` — 두 번째 배포(앞 배포로 코드가 이미 안 쓰는 것을 지우는 것)
- `# expand-ok: <이유>` — CHECK 교체 · `type_` 만. 넓히기라 직전 코드가 그대로 돈다고 사람이 봤다

검사 대상은 `BASELINE` 과 그 조상을 뺀 revision 전부다. git diff 를 쓰지 않는 이유 — CI 는
fetch-depth 1 이고, 배포가 부른 실행에는 비교할 base 가 없다.
"""

import argparse
import ast
import io
import re
import sys
import tokenize
from collections.abc import Sequence
from pathlib import Path

from alembic.script import ScriptDirectory

# 이 revision 과 그 조상은 검사하지 않는다 — 검사를 넣기 전에 이미 배포된 것들이다
BASELINE = "d5e8a1c4b937"
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
CONTRACT = re.compile(r"#\s*contract:\s*#[1-9]\d*\b")
EXPAND_OK = re.compile(r"#\s*expand-ok:\s*\S")

# 원문 SQL — 공백을 한 칸으로 줄인 문장 하나에 건다
_DROP_TABLE = re.compile(r"^DROP TABLE\b", re.IGNORECASE)
_ALTER_TABLE = re.compile(r"^ALTER TABLE (?:IF EXISTS )?(?:ONLY )?\S+ (.*)", re.IGNORECASE)
# ALTER TABLE 의 동작은 쉼표로 이어진다. 동작 머리만 본다 — ALTER COLUMN c DROP NOT NULL 은 아니다.
# DROP [COLUMN] c · RENAME [COLUMN] a TO b · RENAME TO t · ALTER [COLUMN] c TYPE · SET NOT NULL
_BREAKING_ACTION = re.compile(
    r"(?:^|, )(?:DROP|RENAME) (?!CONSTRAINT\b)"
    r"|(?:^|, )ALTER (?:COLUMN )?\S+ (?:SET DATA )?TYPE\b"
    r"|\bSET NOT NULL\b",
    re.IGNORECASE,
)
_VARCHAR = ("String", "VARCHAR", "Unicode")


def _sql_violation(sql: str) -> str | None:
    for statement in sql.split(";"):
        statement = " ".join(statement.replace(",", ", ").split())
        if _DROP_TABLE.match(statement):
            return "DROP TABLE"
        if (alter := _ALTER_TABLE.match(statement)) and (
            m := _BREAKING_ACTION.search(alter.group(1))
        ):
            return m.group(0).strip(", ")
    return None


def _strings(node: ast.expr | None, names: dict[str, list[ast.expr]], seen=()) -> list[str]:
    """SQL 로 쓰일 문자열 값. 이름은 파일 안의 대입을 따라가고, f-string 의 {…} 는 x 로 둔다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return ["".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values)]
    if isinstance(node, ast.Name) and node.id not in seen:
        return [s for v in names.get(node.id, []) for s in _strings(v, names, (*seen, node.id))]
    if isinstance(node, ast.Call) and _name(node.func) == "text" and node.args:
        return _strings(node.args[0], names, seen)
    return []


def _name(node: ast.expr) -> str | None:
    return getattr(node, "attr", getattr(node, "id", None))


def _is(node: ast.expr | None, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _widens(new: ast.expr | None, old: ast.expr | None) -> bool:
    """VARCHAR(n) → VARCHAR(m ≥ n) · VARCHAR(n) → TEXT 만 넓히기로 본다."""

    def parse(t: ast.expr | None) -> tuple[str | None, object]:
        """(타입 이름, 길이). 길이가 없으면 None, 상수가 아니면 "?" — 넓히기로 보지 않는다."""
        if not isinstance(t, ast.Call):
            return None, None
        # 위치 인자 String(8) 와 autogenerate 가 쓰는 키워드 String(length=8) 둘 다
        kw = {k.arg: k.value for k in t.keywords}
        node = t.args[0] if t.args else kw.get("length")
        if node is None:
            return _name(t.func), None
        return _name(t.func), node.value if isinstance(node, ast.Constant) else "?"

    (new_kind, new_len), (old_kind, old_len) = parse(new), parse(old)
    if old_kind not in _VARCHAR:
        return False
    if new_kind in ("Text", "TEXT"):
        return True
    if "?" in (new_len, old_len):
        return False
    return new_kind in _VARCHAR and (
        new_len is None or (old_len is not None and new_len >= old_len)
    )


def _reached(tree: ast.Module) -> list[ast.FunctionDef]:
    """upgrade 와 거기서 (거쳐) 부르는 같은 파일의 모듈 함수."""
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "upgrade" not in funcs:
        return []
    seen, stack = {"upgrade"}, ["upgrade"]
    while stack:
        for n in ast.walk(funcs[stack.pop()]):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id in funcs
                and n.func.id not in seen
            ):
                seen.add(n.func.id)
                stack.append(n.func.id)
    return [funcs[name] for name in seen]


def find_violations(source: str) -> list[tuple[int, str]]:
    """`upgrade()` 쪽에서 직전 코드를 깨는 연산 중 표시로 넘기지 않은 것 (줄, 무엇)."""
    tree = ast.parse(source)
    reached = _reached(tree)
    # 모듈 상수와 upgrade 쪽 함수 안의 대입만 — downgrade 의 `sql = "… DROP …"` 가 섞이면 안 된다
    names: dict[str, list[ast.expr]] = {}
    scopes = [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))]
    scopes += [n for f in reached for n in ast.walk(f)]
    for n in scopes:
        targets = n.targets if isinstance(n, ast.Assign) else [getattr(n, "target", None)]
        for t in targets:
            if isinstance(t, ast.Name) and getattr(n, "value", None) is not None:
                names.setdefault(t.id, []).append(n.value)

    def key(node: ast.expr | None) -> str:
        """제약 · 컬럼 이름. 상수 하나에 묶인 이름이면 그 값으로 맞춘다."""
        values = _strings(node, names) if isinstance(node, ast.Name) else []
        if len(values) == 1:
            return values[0]
        return node.value if isinstance(node, ast.Constant) else ast.unparse(node) if node else ""

    calls = [
        (n, {k.arg: k.value for k in n.keywords})
        for f in reached
        for n in ast.walk(f)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]

    def columns(call: ast.Call, kw: dict) -> list[tuple[ast.Call, dict]]:
        cols = [*call.args, kw.get("column")]
        return [
            (c, {k.arg: k.value for k in c.keywords})
            for c in cols
            if isinstance(c, ast.Call) and _name(c.func) == "Column"
        ]

    def constraint(call: ast.Call, kw: dict) -> str:
        return key(call.args[0] if call.args else kw.get("constraint_name"))

    dropped = {constraint(n, kw) for n, kw in calls if n.func.attr == "drop_constraint"}
    added_not_null = set()
    found: list[tuple[ast.Call, str, bool]] = []  # (연산, 무엇, expand-ok 로 넘길 수 있나)
    for n, kw in calls:
        attr = n.func.attr
        if attr in ("drop_column", "drop_table", "rename_table"):
            found.append((n, attr, False))
        elif attr == "add_column":
            for col, ckw in columns(n, kw):
                not_null = _is(ckw.get("nullable"), False) or (
                    _is(ckw.get("primary_key"), True) and not _is(ckw.get("nullable"), True)
                )
                if not_null and ("server_default" not in ckw or _is(ckw["server_default"], None)):
                    found.append((n, "add_column — server_default 없는 NOT NULL 컬럼", False))
                elif not_null and col.args:
                    added_not_null.add(key(col.args[0]))
        elif attr == "create_check_constraint":
            if constraint(n, kw) in dropped:
                found.append((n, "CHECK 교체 — 값을 빼거나 바꾸면 직전 코드의 쓰기가 깨진다", True))
        elif attr == "execute" and n.args:
            for sql in _strings(n.args[0], names):
                if what := _sql_violation(sql):
                    found.append((n, f"execute — 원문 SQL 의 {what}", False))
                    break
    for n, kw in calls:
        if n.func.attr != "alter_column":
            continue
        column = n.args[1] if len(n.args) > 1 else kw.get("column_name")
        if "new_column_name" in kw:
            found.append((n, "alter_column(new_column_name=…) — 컬럼 이름 바꾸기", False))
        elif _is(kw.get("nullable"), False):
            found.append((n, "alter_column(nullable=False) — NOT NULL 로 조이기", False))
        elif _is(kw.get("server_default"), None) and (
            _is(kw.get("existing_nullable"), False) or key(column) in added_not_null
        ):
            found.append(
                (n, "alter_column(server_default=None) — NOT NULL 컬럼의 기본값 떼기", False)
            )
        if "type_" in kw and not _widens(kw["type_"], kw.get("existing_type")):
            found.append((n, "alter_column(type_=…) — 넓히기인지 모르는 타입 바꾸기", True))

    own_line, trailing = {}, {}
    for t in tokenize.generate_tokens(io.StringIO(source).readline):
        if t.type == tokenize.COMMENT:
            (own_line if t.line.lstrip().startswith("#") else trailing)[t.start[0]] = t.string

    def marked(n: ast.Call, widen: bool) -> bool:
        comments = [own_line.get(n.lineno - 1, "")]
        comments += [
            c for line, c in (own_line | trailing).items() if n.lineno <= line <= n.end_lineno
        ]
        return any(CONTRACT.match(c) or (widen and EXPAND_OK.match(c)) for c in comments)

    return sorted((n.lineno, what) for n, what, widen in found if not marked(n, widen))


def new_migrations(versions_dir: Path, baseline: str = BASELINE) -> list[Path]:
    """baseline 과 그 조상을 뺀 revision 파일. baseline 앞에서 갈라져 merge 된 것도 들어간다."""
    script = ScriptDirectory(versions_dir.parent, version_locations=[str(versions_dir)])
    every = {s.revision: Path(s.path) for s in script.walk_revisions()}
    if baseline not in every:
        # 조용히 0개로 통과하면 검사가 꺼진 것과 같다
        raise SystemExit(f"BASELINE {baseline} 이 {versions_dir} 에 없다")
    done = {s.revision for s in script.iterate_revisions(baseline, "base")}
    return sorted(path for rev, path in every.items() if rev not in done)


def check(paths: Sequence[Path]) -> list[tuple[Path, int, str]]:
    return [
        (path, line, what)
        for path in paths
        for line, what in find_violations(path.read_text(encoding="utf-8"))
    ]


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
            "배포하고, 다음 PR 의 마이그레이션에서 스키마를 바꾼다. 그 연산 바로 위 줄에\n"
            "`# contract: #<앞 배포 PR 번호>` 를 적으면 그 연산은 통과한다.\n"
            "CHECK 교체 · type_ 가 넓히기(값 더하기 · 길이 늘리기)뿐이면 `# expand-ok: <이유>`."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
