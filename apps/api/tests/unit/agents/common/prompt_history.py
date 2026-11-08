"""평가 문서의 측정 이력 표를 읽고 검사한다 (#770). `test_prompt_history.py` 가 쓴다."""

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
DOC_DIR = REPO_ROOT / "docs" / "agents" / "eval"

_SECTION = re.compile(r"^##\s+1\.\s*측정 이력\s*$")
_NEXT_SECTION = re.compile(r"^##\s")
_ROW = re.compile(r"^\|\s*v\d+\s*\|")
_SHA = re.compile(r"[0-9a-f]{12}")
_C1 = re.compile(r"(\d+)/(\d+)")
UNMEASURED = "미측정"
COLUMNS = 8


@dataclass(frozen=True)
class HistoryRow:
    version: str
    commit: str
    sha: str
    condition: str
    note: str
    c1: str
    stable: str
    unstable: str


class HistoryError(ValueError):
    pass


def doc_path(name: str) -> Path:
    return DOC_DIR / f"{name.upper()}_EVAL.md"


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_history(text: str) -> list[HistoryRow]:
    """`## 1. 측정 이력` 절의 표에서 행을 읽는다. 절이나 행이 없거나 표가 깨졌으면 HistoryError."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start = next((i for i, line in enumerate(lines) if _SECTION.match(line)), None)
    if start is None:
        raise HistoryError("`## 1. 측정 이력` 절이 없습니다")
    rows: list[HistoryRow] = []
    for line in lines[start + 1 :]:
        if _NEXT_SECTION.match(line):
            break
        if not _ROW.match(line):
            continue
        cells = _cells(line)
        if len(cells) != COLUMNS:
            raise HistoryError(
                f"표가 깨졌습니다 — 칸이 {len(cells)}개입니다 ({COLUMNS}개여야 합니다): {line[:60]}"
            )
        commit, sha = (cell.strip("`") for cell in cells[1:3])
        rows.append(HistoryRow(cells[0], commit, sha, *cells[3:]))
    if not rows:
        raise HistoryError("측정 이력 표에 `v0` 같은 행이 없습니다")
    return rows


def _c1_count(row: HistoryRow) -> int | None:
    match = _C1.fullmatch(row.c1)
    return int(match.group(1)) if match else None


def check_history(name: str, text: str, current_short: str) -> list[str]:
    """실패 이유 목록. 비어 있으면 통과."""
    path = f"docs/agents/eval/{name.upper()}_EVAL.md"
    try:
        rows = parse_history(text)
    except HistoryError as exc:
        return [f"{name}: {exc} ({path})"]

    errors: list[str] = []
    for row in rows:
        if not _SHA.fullmatch(row.sha):
            errors.append(
                f"{name}: {row.version} 의 지문 `{row.sha}` 이 앞 12자 16진수가 아닙니다 ({path})"
            )
        if row.version == "v0":
            continue
        if row.c1 == UNMEASURED:
            errors.append(
                f"{name}: {row.version} 의 C1 이 미측정입니다 — v0 이후 행은 재서 적습니다 ({path})"
            )
        elif _c1_count(row) is None:
            errors.append(f"{name}: {row.version} 의 C1 `{row.c1}` 이 a/b 형식이 아닙니다 ({path})")

    last = rows[-1]
    if last.sha != current_short:
        errors.append(
            f"{name}: 프롬프트가 마지막 측정 뒤 바뀌었습니다. 바뀐 프롬프트로 재고 측정 이력에 "
            f"{current_short} 행을 더하세요 ({path})."
        )

    last_count = _c1_count(last)
    if last_count is not None:
        before = next(
            (
                row
                for row in reversed(rows[:-1])
                if row.condition == last.condition and _c1_count(row) is not None
            ),
            None,
        )
        before_count = _c1_count(before) if before else None
        if before is not None and before_count is not None and last_count > before_count:
            errors.append(
                f"{name}: C1 이 {before_count} → {last_count} 로 늘었습니다 "
                f"({before.version} → {last.version}, 같은 조건). "
                f"C1 이 늘어난 프롬프트 변경은 머지하지 않습니다 ({path})."
            )
    return errors
