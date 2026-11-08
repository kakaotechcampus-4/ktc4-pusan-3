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
from tests.eval.suite.verdict import case_passed, case_unstable

RAW_DIR = CASES_DIR.parent / "results" / "raw"
KST = ZoneInfo("Asia/Seoul")
LIMITS = (5, 8, 10)  # W 한도 분석이 보는 보호자별 하루 입력 한도 N (spec §5-4)
RULE_OF_THREE_MIN = 30  # 이보다 작은 표본에서 "실패율 < 3/n" 은 말이 안 된다 (n=1 → 300%)
# 옛 불변식 I14 — §2 가 아니라 NF-01 이라 비용 경고로 옮겼다 (리뷰 11-08). 제품 예산은 정상 4 ·
# 다시 나눈 run 만 +1 인데(eval/faults.md IDEM-D) Trace 에 다시 나누기 표시가 없어
# 5 를 모든 run 에 건다
MODEL_CALL_WARN = 5


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

    @property
    def unstable(self) -> bool:
        return case_unstable(self.passes)

    @property
    def mark(self) -> str:
        return "✅" if self.passed else ("⚠️ 불안정" if self.unstable else "❌")


def _scored(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """건너뛴 행(토큰 상한 등)은 점수 · 비용에서 뺀다."""
    return [row for row in rows if "skipped" not in row]


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, CaseResult]:
    cases: dict[str, CaseResult] = {}
    for row in _scored(rows):
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


def _cell(text: object) -> str:
    """md 표 칸에 넣을 글자 — | 와 줄바꿈이 표를 깨지 않게 한다."""
    return " ".join(str(text).replace("|", "\\|").split())


def _rate(passed: int, n: int) -> str:
    low, high = wilson(passed, n)
    if passed == n and n >= RULE_OF_THREE_MIN:
        return f"{passed}/{n} | 실패율 < {3 / n:.0%} (3의 규칙) |"
    return f"{passed}/{n} | {low:.0%} ~ {high:.0%} |"


def _inputs(row: dict[str, Any]) -> list[tuple[str, dict[str, int]]]:
    """run 하나(여러 턴일 수 있다)를 입력 단위로 쪼갠다. 마지막 턴만 input_kind, 앞은 "앞 턴"."""
    trace = row["trace"]
    order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for u in trace["usage"]:
        rid = str(u.get("run_id", ""))
        if rid not in groups:
            order.append(rid)
        groups.setdefault(rid, []).append(u)
    if not order:
        order, groups = [""], {"": []}
    out: list[tuple[str, dict[str, int]]] = []
    for i, rid in enumerate(order):
        usage = groups[rid]
        last = i == len(order) - 1
        out.append(
            (
                row["input_kind"] if last else "앞 턴",
                {
                    "calls": len(usage),
                    "prompt": sum(u["prompt_tokens"] for u in usage),
                    "cached": sum(int(u.get("cached_prompt_tokens", 0)) for u in usage),
                    "completion": sum(u["completion_tokens"] for u in usage),
                    "latency": int(trace["latency_ms"])
                    if last
                    else sum(int(u.get("latency_ms", 0)) for u in usage),
                },
            )
        )
    return out


def _cost(rows: Sequence[dict[str, Any]]) -> list[str]:
    # W 의 끝 상태 점검 · 승인 · 정정 단계는 입력이 아니다(run 없음) — 입력 종류 표에서 뺀다
    rows = [
        row for row in _scored(rows) if row.get("step_kind") not in ("check", "approve", "correct")
    ]
    by_kind: dict[str, list[dict[str, int]]] = defaultdict(list)
    for row in rows:
        for kind, numbers in _inputs(row):
            by_kind[kind].append(numbers)
    lines = [
        "| 입력 종류 | run | API 호출 p50/p95 | 입력 토큰 p50/p95 | 캐시 토큰 p50/p95 "
        "| 출력 토큰 p50/p95 | 지연 ms p50/p95 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    total = cached_total = 0
    for kind, items in sorted(by_kind.items()):

        def pair(key: str, items: list[dict[str, int]] = items) -> str:
            values = [item[key] for item in items]
            return f"{percentile(values, 0.5)}/{percentile(values, 0.95)}"

        total += sum(i["prompt"] + i["completion"] for i in items)
        cached_total += sum(i["cached"] for i in items)
        lines.append(
            f"| {kind} | {len(items)} | {pair('calls')} | {pair('prompt')} | {pair('cached')} "
            f"| {pair('completion')} | {pair('latency')} |"
        )
    partial = sum(1 for row in rows if row["trace"]["partial"])
    lines += [
        "",
        f"- 토큰 합계 {total:,} (캐시 {cached_total:,}) · 부분 결과 {partial}/{len(rows)} run",
    ]
    models = sorted({f"{u['role']}={u['model']}" for r in rows for u in r["trace"]["usage"]})
    lines.append(f"- 역할별 모델: {', '.join(models) or '기록 없음'}")
    many = [r for r in rows if int(r["trace"].get("model_calls", 0) or 0) > MODEL_CALL_WARN]
    if many:
        names = ", ".join(dict.fromkeys(str(r["case"]) for r in many))
        lines.append(
            f"- ⚠️ NF-01 경고: model_calls > {MODEL_CALL_WARN} 인 run {len(many)}/{len(rows)} run "
            f"({_cell(names)}) — 안전 실패가 아니라 비용 경고다"
        )
    else:
        lines.append(f"- NF-01 경고 없음 (model_calls > {MODEL_CALL_WARN} 인 run 0개)")
    return lines


def is_week(row: dict[str, Any]) -> bool:
    return "week" in row and "skipped" not in row


def daily_inputs(rows: Sequence[dict[str, Any]]) -> dict[str, dict[tuple[str, str], int]]:
    """W 케이스별 {(보호자, 날짜): 그날 입력 수}. say · reply 만 센다 — 되묻기 답도 한도를 쓴다."""
    counts: dict[str, dict[tuple[str, str], int]] = {}
    for row in rows:
        if is_week(row) and row["step_kind"] in ("say", "reply"):
            per = counts.setdefault(row["week"], {})
            key = (row["by"], row["day"])
            per[key] = per.get(key, 0) + 1
    return counts


def blocked_at(counts: dict[tuple[str, str], int], limit: int) -> int:
    """하루 한도가 N 이면 막히는 입력 수 — 보호자 · 날짜마다 N 을 넘는 만큼의 합."""
    return sum(max(0, used - limit) for used in counts.values())


def _week(rows: Sequence[dict[str, Any]]) -> list[str]:
    week_rows = [row for row in rows if is_week(row)]
    skipped = [row for row in rows if "skipped" in row and ":" in str(row.get("case", ""))]
    lines = [
        "## W · 일주일 시나리오",
        "",
        "W 의 Curator 는 가짜 임베딩 · 가짜 판정기다 — 실제 묶음 품질은 보지 않는다. "
        "한도는 끄고 돌았다(한도 분석은 아래 표).",
        "",
        "| 케이스 | 단계 통과 | 끝 상태 점검 | 토큰 | 첫 실패 이유 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for week in dict.fromkeys(row["week"] for row in week_rows):
        mine = [row for row in week_rows if row["week"] == week]
        steps = [row for row in mine if row["step_kind"] != "check"]
        checks = [row for row in mine if row["step_kind"] == "check"]
        failed = [row for row in mine if not row["passed"]]
        first = (failed[0]["violations"] + failed[0]["misses"] or ["-"])[0] if failed else "-"
        tokens = 0
        for row in mine:
            tokens += sum(
                int(u.get("prompt_tokens", 0)) + int(u.get("completion_tokens", 0))
                for u in row["trace"]["usage"]
            )
        check_text = (
            " · ".join("✅" if row["passed"] else "❌" for row in checks) if checks else "없음"
        )
        why = _cell(f"{failed[0]['case']}: {first}") if failed else "-"
        lines.append(
            f"| {week} | {sum(1 for r in steps if r['passed'])}/{len(steps)} | {check_text} "
            f"| {tokens:,} | {why} |"
        )
    if skipped:
        lines += ["", f"- 토큰 상한으로 건너뛴 단계 {len(skipped)}개"]
    lines += ["", "### 끝 상태 점검", "", "| 케이스 | 결과 | 미달 · 위반 |", "| --- | --- | --- |"]
    for row in week_rows:
        if row["step_kind"] == "check":
            reason = row["violations"] + row["misses"]
            lines.append(
                f"| {row['week']} | {'✅' if row['passed'] else '❌'} "
                f"| {_cell('; '.join(reason) or '-')} |"
            )
    counts = daily_inputs(week_rows)
    lines += [
        "",
        "### 하루 입력 한도 분석 (보호자 · 날짜별, 되묻기 답 포함)",
        "",
        "한도 N 이면 막히는 입력 수 — 한도가 바뀌어도 쓰는 표다 (eval-v1 §5-4).",
        "",
        "| 케이스 | 입력 수 | 하루 최대(보호자) | " + " | ".join(f"N={n}" for n in LIMITS) + " |",
        "| --- | --- | --- | " + " | ".join("---" for _ in LIMITS) + " |",
    ]
    for week, per in counts.items():
        lines.append(
            f"| {week} | {sum(per.values())} | {max(per.values())} | "
            + " | ".join(str(blocked_at(per, n)) for n in LIMITS)
            + " |"
        )
    lines.append("")
    return lines


def _commit_label(rows: Sequence[dict[str, Any]], fallback: str) -> str:
    git = next((row["git"] for row in rows if row.get("git")), None)
    if git is None:
        return f"`{fallback}`"
    dirty = " (커밋 안 된 변경 있음)" if git.get("dirty") is True else ""
    return f"`{git.get('sha', 'unknown')}`{dirty}"


def render(rows: Sequence[dict[str, Any]], *, source: str, sha: str, day: str) -> str:
    skipped = [row for row in rows if "skipped" in row]
    scored = _scored(rows)
    cases = summarize(rows)
    repeat = max((row["repeat"] for row in scored), default=0)
    headline = sorted((c for c in cases.values() if c.headline), key=lambda c: c.headline or 0)
    lines = [
        f"# eval 결과 {day}",
        "",
        f"- 커밋 {_commit_label(rows, sha)} · 원본 `{source}` · 반복 {repeat} "
        f"· 케이스 {len(cases)} · run {len(scored)} · 건너뜀 {len(skipped)}",
        "",
        f"## 헤드라인 {sum(c.passed for c in headline)}/{len(headline)}"
        f" · 불안정 {sum(c.unstable for c in headline)}",
        "",
        '통과 = 반복 전부 통과. 일부만 통과하면 실패로 세고 "불안정" 으로 표시한다',
        "",
        "| # | 케이스 | 통과 |",
        "| --- | --- | --- |",
        *(f"| {c.headline} | {c.case} | {c.mark} {sum(c.passes)}/{c.repeat} |" for c in headline),
        "",
        "## 불변식 위반 (0 이어야 한다)",
        "",
    ]
    violations = [(c.case, v) for c in cases.values() for v in c.violations]
    lines += [f"- {case}: {v}" for case, v in violations] or ["- 없음"]
    lines += [
        "",
        "## 능력별",
        "",
        "| 능력 | 통과 케이스 | 95% 구간 | run 통과 |",
        "| --- | --- | --- | --- |",
    ]
    abilities: dict[str, list[CaseResult]] = defaultdict(list)
    for c in cases.values():
        abilities[c.ability].append(c)
    for ability, group in abilities.items():
        attempts = [ok for c in group for ok in c.passes]
        lines.append(
            f"| {ability} | {_rate(sum(c.passed for c in group), len(group))} "
            f"{sum(attempts)}/{len(attempts)} |"
        )
    lines += [
        "",
        "run 통과율은 safety 케이스의 첫 실패 조기 종료로 낮게 치우친다 — "
        "회귀 비교는 케이스별 짝 비교(§9)를 먼저 본다",
    ]
    failing = [c for c in cases.values() if not c.passed]
    lines += [
        "",
        "## 실패한 케이스",
        "",
        "| 케이스 | 통과 | §2 위반 수 | 기대 미달 수 | 첫 이유 |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {c.case} | {c.mark} {sum(c.passes)}/{c.repeat} | {len(c.violations)} | {len(c.misses)} "
        f"| {_cell((c.violations + c.misses or ['-'])[0])} |"
        for c in failing
    ] or ["| 없음 | | | | |"]
    if skipped:
        lines += ["", "## 실행하지 않은 케이스", "", "| 케이스 | 이유 |", "| --- | --- |"]
        lines += [f"| {_cell(row['case'])} | {_cell(row['skipped'])} |" for row in skipped]
    if any(is_week(row) for row in rows):
        lines += ["", *_week(rows)]
    lines += ["", "## 비용 · 지연", "", *_cost(scored), ""]
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
