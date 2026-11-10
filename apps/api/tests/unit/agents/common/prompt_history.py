"""평가 문서의 측정 이력 표를 읽고 검사한다 (#770). `test_prompt_history.py` 가 쓴다."""

import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
DOC_DIR = REPO_ROOT / "docs" / "agents" / "eval"

_SECTION = re.compile(r"^##\s+1\.\s*측정 이력\s*$")
_NEXT_SECTION = re.compile(r"^##\s")
_ROW = re.compile(r"^\|\s*v\d+\s*\|")
_SHA = re.compile(r"[0-9a-f]{12}")
# 커밋 칸 — 잰 코드의 커밋 해시 7~40자, 작업 트리가 더러웠으면 뒤에 dirty
_COMMIT = re.compile(r"[0-9a-f]{7,40}( dirty)?")
_C1 = re.compile(r"(\d+)/(\d+)")
UNMEASURED = "미측정"
# C1 정의 자체가 정해지지 않은 프롬프트. 문서에 `<!-- c1: 미결 -->` 이 있을 때만 쓸 수 있다
UNDECIDED = "미결"
_UNDECIDED_MARK = re.compile(r"<!--\s*c1:\s*미결\s*-->")
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
        commit, sha = (cell.replace("`", "").strip() for cell in cells[1:3])
        rows.append(HistoryRow(cells[0], commit, sha, *cells[3:]))
    if not rows:
        raise HistoryError("측정 이력 표에 `v0` 같은 행이 없습니다")
    return rows


def _rate(row: HistoryRow) -> Fraction | None:
    """C1 칸 `a/b` 의 비율. 형식이 아니거나 b 가 0 이거나 a > b 면 None."""
    match = _C1.fullmatch(row.c1)
    if not match:
        return None
    count, total = int(match.group(1)), int(match.group(2))
    if total == 0 or count > total:
        return None
    return Fraction(count, total)


def _measured(row: HistoryRow) -> bool:
    """잰 행인가 — C1 이 `a/b` 이거나, C1 정의가 미결인 문서의 `미결`."""
    return _rate(row) is not None or row.c1 == UNDECIDED


def check_history(name: str, text: str, current_short: str) -> list[str]:
    """실패 이유 목록. 비어 있으면 통과.

    - 마지막 행 지문 == 지금 지문
    - v0 다음 행은 C1 을 `a/b` 로 잰다 (C1 정의가 미결인 문서만 `미결`)
    - 프롬프트를 바꾼 행(바로 앞 행과 지문이 다른 마지막 행)은 같은 조건으로 잰
      바꾸기 전 행이 있어야 하고, 그 행보다 C1 비율이 늘면 안 된다.
      조건만 바꿔 같은 프롬프트를 다시 잰 행은 비교하지 않는다
    """
    path = f"docs/agents/eval/{name.upper()}_EVAL.md"
    try:
        rows = parse_history(text)
    except HistoryError as exc:
        return [f"{name}: {exc} ({path})"]
    undecided_ok = bool(_UNDECIDED_MARK.search(text))

    errors: list[str] = []
    for row in rows:
        if not _SHA.fullmatch(row.sha):
            errors.append(
                f"{name}: {row.version} 의 지문 `{row.sha}` 이 앞 12자 16진수가 아닙니다 ({path})"
            )
        if not _COMMIT.fullmatch(row.commit):
            errors.append(
                f"{name}: {row.version} 의 커밋 `{row.commit}` 이 커밋 해시(7~40자, 더러웠으면 "
                f"뒤에 dirty)가 아닙니다 ({path})"
            )
        if not row.condition or not row.note:
            errors.append(f"{name}: {row.version} 의 조건 · 무엇을 왜 칸이 비었습니다 ({path})")
        if row.version == "v0":
            continue
        if row.c1 == UNMEASURED:
            errors.append(
                f"{name}: {row.version} 의 C1 이 미측정입니다 — v0 이후 행은 재서 적습니다 ({path})"
            )
        elif row.c1 == UNDECIDED:
            if not undecided_ok:
                errors.append(
                    f"{name}: {row.version} 의 C1 `미결` 은 "
                    f"`<!-- c1: 미결 -->` 표시가 있는 문서에서만 씁니다 ({path})"
                )
        elif _rate(row) is None:
            errors.append(f"{name}: {row.version} 의 C1 `{row.c1}` 이 a/b 형식이 아닙니다 ({path})")

    last = rows[-1]
    if last.sha != current_short:
        errors.append(
            f"{name}: 프롬프트가 마지막 측정 뒤 바뀌었습니다. 바뀐 프롬프트로 재고 측정 이력에 "
            f"{current_short} 행을 더하세요 ({path})."
        )

    # 프롬프트를 바꾼 행이면 C1 이 `미결` 이어도 같은 조건으로 잰 바꾸기 전 행을 요구한다
    prompt_changed = len(rows) >= 2 and rows[-2].sha != last.sha
    measured_before = next(
        (
            row
            for row in reversed(rows[:-1])
            if row.condition == last.condition and row.sha != last.sha and _measured(row)
        ),
        None,
    )
    if prompt_changed and measured_before is None:
        errors.append(
            f"{name}: {last.version} 와 같은 조건으로 잰 바꾸기 전 행이 없습니다 — 바꾸기 전 "
            f"프롬프트를 같은 조건으로 재서 그 행을 먼저 더하세요 ({path})."
        )

    last_rate = _rate(last)
    if last_rate is None:
        return errors
    before = next(
        (
            row
            for row in reversed(rows[:-1])
            if row.condition == last.condition and _rate(row) is not None
        ),
        None,
    )
    if before is None:
        return errors
    before_rate = _rate(before)
    if before_rate is not None and last_rate > before_rate:
        errors.append(
            f"{name}: C1 비율이 {before.c1} → {last.c1} 로 늘었습니다 "
            f"({before.version} → {last.version}, 같은 조건). "
            f"C1 이 늘어난 프롬프트 변경은 머지하지 않습니다 ({path})."
        )
    return errors
