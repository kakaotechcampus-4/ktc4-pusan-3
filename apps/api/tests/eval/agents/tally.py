"""라이브 eval 회차를 C1 · 일반으로 쌓고 측정 이력 칸 값 · CI 빨간불을 낸다 (#816).

테스트는 입력 하나를 한 번 돌릴 때마다 `mark` 로 결과를 jsonl 에 한 줄 남긴다. 요청 · 답 원문은
넣지 않는다 — 실패 이유 문장도 파일에는 건수만 의미 있고, job 요약에는 문장을 싣지 않는다.

회차 id 는 `세션 번호.안쪽 번호` 다. 세션 번호는 `EVAL_ATTEMPT`(CI 는 pytest 를 3번 돌린다),
안쪽 번호는 테스트 안의 `TEST_REPEAT` 루프 번호다. 안에서 반복하지 않는 eval 도 세션을 여러 번
돌리면 회차로 합쳐서 센다.

    uv run python -m tests.eval.agents.tally                  # suite 마다 측정 이력 칸 값
    uv run python -m tests.eval.agents.tally --reset          # 결과 파일 지우기 (세션 반복 전에)
    uv run python -m tests.eval.agents.tally --fail-on stable --summary "$GITHUB_STEP_SUMMARY"

측정 이력 표(`docs/agents/eval/*_EVAL.md` §1)는 사람이 쓴다 — 이 모듈은 칸에 옮길 값만 낸다.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.memory.bundles import MUTATING_PREFIXES

RESULTS = Path(__file__).with_name("live_verdicts_results.jsonl")

# 되돌리기 어려운 쓰기 tool. 복약 중단(`stop_`)은 Memory 의 쓰기 묶음에는 없지만 쓰기다
WRITE_PREFIXES: tuple[str, ...] = (*MUTATING_PREFIXES, "stop_")


def is_write_tool(name: str) -> bool:
    return name.startswith(WRITE_PREFIXES)


class C1Failure(AssertionError):  # noqa: N818 — assert 처럼 보이는 실패
    """C1 로 세는 판정이 낸 실패. assert 로 판정하는 eval 이 일반 실패와 구분해 던진다."""


@dataclass(frozen=True)
class Mark:
    """입력 하나를 한 회차 돌린 결과. `c1` · `general` 은 실패 이유 문장(없으면 통과)."""

    suite: str
    input_id: str
    attempt: str
    c1: tuple[str, ...] = ()
    general: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return bool(self.c1 or self.general)


def attempt_id(inner: int = 1, session: int | None = None) -> str:
    """세션 번호와 안쪽 반복 번호를 한 입력의 회차 id 로 합친다."""
    if session is None:
        session = int(os.getenv("EVAL_ATTEMPT") or 1)
    return f"{session}.{inner}"


_written = 0


def written() -> int:
    """이번 프로세스에서 `mark` 를 부른 횟수 — conftest 가 "이 테스트가 직접 남겼나" 를 본다."""
    return _written


def mark(
    suite: str,
    input_id: str,
    *,
    inner: int = 1,
    c1: Sequence[str] = (),
    general: Sequence[str] = (),
    tools: Sequence[str] = (),
    path: Path | None = None,
    session: int | None = None,
) -> None:
    global _written
    row = Mark(suite, input_id, attempt_id(inner, session), tuple(c1), tuple(general), tuple(tools))
    target = path or RESULTS
    with target.open("a", encoding="utf-8") as out:
        out.write(
            json.dumps(
                {
                    "suite": row.suite,
                    "input_id": row.input_id,
                    "attempt": row.attempt,
                    "c1": list(row.c1),
                    "general": list(row.general),
                    "tools": list(row.tools),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    _written += 1


def load(path: Path | None = None) -> list[Mark]:
    """같은 `(suite, input_id, attempt)` 는 나중 줄이 이긴다 — 같은 번호로 다시 돌린 것이다."""
    target = path or RESULTS
    if not target.exists():
        return []
    latest: dict[tuple[str, str, str], Mark] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        row = Mark(
            raw["suite"],
            raw["input_id"],
            raw["attempt"],
            tuple(raw["c1"]),
            tuple(raw["general"]),
            tuple(raw["tools"]),
        )
        latest[(row.suite, row.input_id, row.attempt)] = row
    return list(latest.values())


@dataclass(frozen=True)
class InputVerdict:
    suite: str
    input_id: str
    failed: int  # 실패한 회차 수
    attempts: int
    c1: bool  # C1 실패가 한 번이라도 있었나
    tools: tuple[tuple[str, ...], ...]  # 회차별로 부른 tool

    @property
    def outcome(self) -> str:
        if self.failed == 0:
            return "pass"
        return "stable_wrong" if self.failed == self.attempts else "unstable"


def _attempt_key(attempt: str) -> tuple[int, ...]:
    """`세션.안쪽` 을 숫자로 센다 — 문자열로 정렬하면 `1.10` 이 `1.2` 앞에 온다."""
    return tuple(int(part) if part.isdigit() else 0 for part in attempt.split("."))


def by_input(marks: Sequence[Mark]) -> list[InputVerdict]:
    groups: dict[tuple[str, str], list[Mark]] = defaultdict(list)
    for row in marks:
        groups[(row.suite, row.input_id)].append(row)
    return [
        InputVerdict(
            suite=suite,
            input_id=input_id,
            failed=sum(r.failed for r in rows),
            attempts=len(rows),
            c1=any(r.c1 for r in rows),
            tools=tuple(r.tools for r in sorted(rows, key=lambda r: _attempt_key(r.attempt))),
        )
        for (suite, input_id), rows in groups.items()
    ]


@dataclass(frozen=True)
class Tally:
    suite: str
    inputs: int
    c1_inputs: int
    stable_wrong: int
    unstable: int
    repetitions: int

    @property
    def c1_cell(self) -> str:
        return f"{self.c1_inputs}/{self.inputs}"


def summarize(marks: Sequence[Mark]) -> list[Tally]:
    suites: dict[str, list[InputVerdict]] = defaultdict(list)
    for verdict in by_input(marks):
        suites[verdict.suite].append(verdict)
    return [
        Tally(
            suite=suite,
            inputs=len(verdicts),
            c1_inputs=sum(v.c1 for v in verdicts),
            stable_wrong=sum(v.outcome == "stable_wrong" for v in verdicts),
            unstable=sum(v.outcome == "unstable" for v in verdicts),
            repetitions=max(v.attempts for v in verdicts),
        )
        for suite, verdicts in suites.items()
    ]


def _condition(suite: str, meta: Mapping[str, Any]) -> str:
    profile = (meta.get("models") or {}).get(suite) or {}
    model = profile.get("model") or "모델 미기록"
    effort = profile.get("reasoning_effort")
    return f"{model}/{effort}" if effort else str(model)


def _commit(meta: Mapping[str, Any]) -> str:
    commit = str(meta.get("commit") or "")[:8] or "?"
    return f"{commit} dirty" if meta.get("dirty") else commit


def _print_short(suite: str, meta: Mapping[str, Any]) -> str:
    return str((meta.get("prompts") or {}).get(suite) or "")[:12] or "?"


def history_line(t: Tally, meta: Mapping[str, Any]) -> str:
    """`*_EVAL.md` §1 표에 붙일 한 줄. 버전 · 무엇을 왜 바꿨나는 사람이 채운다."""
    cells = [
        "v?",
        f"`{_commit(meta)}`",
        f"`{_print_short(t.suite, meta)}`",
        f"{_condition(t.suite, meta)} · R{t.repetitions}",
        "(무엇을 왜 바꿨나)",
        t.c1_cell,
        str(t.stable_wrong),
        str(t.unstable),
    ]
    return "| " + " | ".join(cells) + " |"


_LABEL = {"pass": "통과", "unstable": "불안정", "stable_wrong": "안정 오답"}


def summary_markdown(verdicts: Sequence[InputVerdict], meta: Mapping[str, Any]) -> str:
    """job 요약. 요청 · 답 · 실패 문장(모델 출력 조각이 들어 있다)은 싣지 않는다."""
    lines = ["| suite | 입력 | 통과 회차 | 판정 | C1 | 부른 tool | 지문 | 조건 |"]
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for v in sorted(verdicts, key=lambda v: (v.suite, v.input_id)):
        called = " / ".join(", ".join(sorted(set(run))) or "-" for run in v.tools)
        lines.append(
            f"| {v.suite} | {v.input_id} | {v.attempts - v.failed}/{v.attempts}"
            f" | {_LABEL[v.outcome]} | {'C1' if v.c1 else '-'} | {called}"
            f" | `{_print_short(v.suite, meta)}` | {_condition(v.suite, meta)} |"
        )
    return "\n".join(lines) + "\n"


def read_meta() -> dict[str, Any]:
    from tests.eval.agents.run_meta import META_PATH

    try:
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="라이브 eval 회차 집계 (#816)")
    parser.add_argument("--reset", action="store_true", help="결과 파일을 지운다")
    parser.add_argument(
        "--fail-on",
        action="append",
        choices=["stable", "c1"],
        help="stable: 안정 오답이 있으면 종료 코드 1. c1: 불안정해도 C1 이 한 번이라도 있으면 1",
    )
    parser.add_argument(
        "--min-inputs",
        type=int,
        default=0,
        help="기록된 입력이 이보다 적으면 종료 코드 1 (건너뛴 케이스가 초록불이 되지 않게)",
    )
    parser.add_argument("--summary", type=Path, help="job 요약을 이 파일에 덧붙인다")
    args = parser.parse_args(argv)

    if args.reset:
        RESULTS.unlink(missing_ok=True)
        return 0

    marks = load()
    meta = read_meta()
    verdicts = by_input(marks)
    for t in summarize(marks):
        print(history_line(t, meta))
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as out:
            out.write(summary_markdown(verdicts, meta))
    if args.fail_on:
        if not verdicts:  # 키가 없어 전부 건너뛴 실행이 초록불이 되면 안 된다
            print("회차 기록이 없다 — 테스트가 하나도 돌지 않았다", file=sys.stderr)
            return 1
        if len(verdicts) < args.min_inputs:
            print(f"입력 {len(verdicts)}/{args.min_inputs} 만 기록됐다", file=sys.stderr)
            return 1
        if "stable" in args.fail_on and any(v.outcome == "stable_wrong" for v in verdicts):
            return 1
        if "c1" in args.fail_on and any(v.c1 for v in verdicts):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
