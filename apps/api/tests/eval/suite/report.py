"""eval 결과 jsonl → 요약 md (docs/eval/eval-v1.md §9).

    uv run python -m tests.eval.suite.report             # 가장 최근 raw jsonl → raw/<같은 이름>.md
    uv run python -m tests.eval.suite.report <jsonl> --out ../../eval/results/2026-11-11.md
        # 위는 기준선 출력 경로 예시

기본 출력은 gitignore 된 raw/ 다 — 커밋하는 것은 기준선 · 정기 실행만 --out 으로 results/ 에 둔다.

표본이 작다는 것을 숫자로 적는다 — 통과율에는 Wilson 95% 구간, 실패 0건이면 "실패율 < 3/n".
"""

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tests.eval.suite.schema import CASES_DIR
from tests.eval.suite.verdict import case_passed

RAW_DIR = CASES_DIR.parent / "results" / "raw"
KST = ZoneInfo("Asia/Seoul")


def wilson(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = passed / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom))


def percentile(values: Sequence[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


@dataclass
class CaseResult:
    case: str
    ability: str
    severity: str
    headline: int | None
    repeat: int
    passes: list[bool] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return case_passed(self.severity, self.passes, self.repeat)


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, CaseResult]:
    cases: dict[str, CaseResult] = {}
    for row in rows:
        result = cases.setdefault(
            row["case"],
            CaseResult(
                row["case"], row["ability"], row["severity"], row["headline"], row["repeat"]
            ),
        )
        result.passes.append(bool(row["passed"]))
        result.violations += row["violations"]
        result.misses += row["misses"]
    return cases


def _rate(passed: int, n: int) -> str:
    low, high = wilson(passed, n)
    if passed == n:
        return f"{passed}/{n} | 실패율 < {3 / n:.0%} (3의 규칙) |"
    return f"{passed}/{n} | {low:.0%} ~ {high:.0%} |"


def _cost(rows: Sequence[dict[str, Any]]) -> list[str]:
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_kind[row["input_kind"]].append(row["trace"])
    lines = [
        "| 입력 종류 | run | API 호출 p50/p95 | 입력 토큰 p50/p95 | 출력 토큰 p50/p95 "
        "| 지연 ms p50/p95 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    total = 0
    for kind, traces in sorted(by_kind.items()):
        calls = [len(t["usage"]) for t in traces]
        prompt = [sum(u["prompt_tokens"] for u in t["usage"]) for t in traces]
        completion = [sum(u["completion_tokens"] for u in t["usage"]) for t in traces]
        latency = [int(t["latency_ms"]) for t in traces]
        total += sum(prompt) + sum(completion)
        lines.append(
            f"| {kind} | {len(traces)} | {percentile(calls, 0.5)}/{percentile(calls, 0.95)} "
            f"| {percentile(prompt, 0.5)}/{percentile(prompt, 0.95)} "
            f"| {percentile(completion, 0.5)}/{percentile(completion, 0.95)} "
            f"| {percentile(latency, 0.5)}/{percentile(latency, 0.95)} |"
        )
    partial = sum(1 for row in rows if row["trace"]["partial"])
    lines += ["", f"- 토큰 합계 {total:,} · 부분 결과 {partial}/{len(rows)} run"]
    models = sorted({f"{u['role']}={u['model']}" for r in rows for u in r["trace"]["usage"]})
    lines.append(f"- 역할별 모델: {', '.join(models) or '기록 없음'}")
    return lines


def render(rows: Sequence[dict[str, Any]], *, source: str, sha: str, day: str) -> str:
    cases = summarize(rows)
    repeat = max((row["repeat"] for row in rows), default=0)
    headline = sorted((c for c in cases.values() if c.headline), key=lambda c: c.headline or 0)
    lines = [
        f"# eval 결과 {day}",
        "",
        f"- 커밋 `{sha}` · 원본 `{source}` · 반복 {repeat} · 케이스 {len(cases)} · run {len(rows)}",
        "",
        f"## 헤드라인 {sum(c.passed for c in headline)}/{len(headline)}",
        "",
        "| # | 케이스 | 통과 |",
        "| --- | --- | --- |",
        *(
            f"| {c.headline} | {c.case} | {'✅' if c.passed else '❌'} {sum(c.passes)}/{c.repeat} |"
            for c in headline
        ),
        "",
        "## 불변식 위반 (0 이어야 한다)",
        "",
    ]
    violations = [(c.case, v) for c in cases.values() for v in c.violations]
    lines += [f"- {case}: {v}" for case, v in violations] or ["- 없음"]
    lines += ["", "## 능력별", "", "| 능력 | 통과 케이스 | 95% 구간 |", "| --- | --- | --- |"]
    abilities: dict[str, list[CaseResult]] = defaultdict(list)
    for c in cases.values():
        abilities[c.ability].append(c)
    for ability, group in abilities.items():
        lines.append(f"| {ability} | {_rate(sum(c.passed for c in group), len(group))}")
    failing = [c for c in cases.values() if not c.passed]
    lines += ["", "## 실패한 케이스", "", "| 케이스 | 통과 | 첫 이유 |", "| --- | --- | --- |"]
    lines += [
        f"| {c.case} | {sum(c.passes)}/{c.repeat} | {(c.violations + c.misses or ['-'])[0]} |"
        for c in failing
    ] or ["| 없음 | | |"]
    lines += ["", "## 비용 · 지연", "", *_cost(rows), ""]
    lines += [
        "## 실패 분석",
        "",
        "원인은 하나로 적는다 — 프롬프트 · 규칙 코드 · 케이스 기대가 틀림 · 하네스 · "
        "모델 흔들림 (eval-v1 §7)",
        "",
        "| 케이스 | 원인 | 담당 | 이슈 |",
        "| --- | --- | --- | --- |",
        *(f"| {c.case} | | | |" for c in failing),
        "",
    ]
    return "\n".join(lines)


def _latest() -> Path:
    files = sorted(RAW_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"{RAW_DIR} 에 결과가 없다 — make eval-smoke 먼저")
    return files[-1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="eval jsonl → 요약 md")
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    source: Path = args.source or _latest()
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    day = datetime.now(KST).date().isoformat()
    md = render(rows, source=source.name, sha=sha or "unknown", day=day)
    out: Path = args.out or source.with_suffix(".md")
    out.write_text(md, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
