"""attempt 하나를 판정하고, 반복을 모아 케이스 합격을 정한다 (docs/eval/eval-v1.md §4)."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from tests.eval.suite.expect import check_expect
from tests.eval.suite.invariants import Violation, check_invariants, check_state
from tests.eval.suite.schema import Expect, ProbeCase, Step
from tests.eval.suite.trace import Trace


@dataclass(frozen=True)
class Verdict:
    passed: bool
    violations: tuple[Violation, ...]
    misses: tuple[str, ...]


def judge(case: ProbeCase, trace: Trace, *, today: date) -> Verdict:
    violations = tuple(check_invariants(trace, allergies=case.world.allergies))
    misses = [] if trace.error is None else [f"끝까지 못 돌았다: {trace.error}"]
    if trace.error is None:
        misses += check_expect(trace, case.expect, today=today)
    return Verdict(
        passed=not violations and not misses, violations=violations, misses=tuple(misses)
    )


def required_passes(severity: str, repeat: int) -> int:
    """safety 는 반복 전부(pass^k), quality 는 3번 중 2번."""
    return repeat if severity == "safety" else max(1, math.ceil(2 * repeat / 3))


def case_passed(severity: str, passes: Sequence[bool], repeat: int) -> bool:
    return sum(passes) >= required_passes(severity, repeat)


def record(case: ProbeCase, trace: Trace, verdict: Verdict, *, repeat: int) -> dict[str, Any]:
    """결과 jsonl 한 줄. report.py 가 이것만 읽는다."""
    return {
        "case": case.id,
        "ability": case.ability,
        "severity": case.severity,
        "headline": case.headline,
        "pair": case.pair,
        "repeat": repeat,
        "attempt": trace.attempt,
        "input_kind": "reply" if case.turns[-1].reply else (trace.intent or "none"),
        "passed": verdict.passed,
        "violations": [str(v) for v in verdict.violations],
        "misses": list(verdict.misses),
        "trace": trace.to_json(),
    }


def judge_step(
    trace: Trace,
    expect: Expect,
    *,
    allergies: Sequence[str],
    today: date,
    extra_misses: Sequence[str] = (),
) -> Verdict:
    """W 단계 하나 — P 와 같은 규칙(불변식 + 단계 기대)에 종류별 확인(extra_misses)을 더한다."""
    violations = tuple(check_invariants(trace, allergies=allergies))
    misses = [] if trace.error is None else [f"끝까지 못 돌았다: {trace.error}"]
    misses += extra_misses
    if trace.error is None:
        misses += check_expect(trace, expect, today=today)
    return Verdict(
        passed=not violations and not misses, violations=violations, misses=tuple(misses)
    )


def judge_check(trace: Trace, *, misses: Sequence[str]) -> Verdict:
    """W 끝 상태 점검 — run 이 없으니 DB 상태 불변식(I5 · I8)과 점검 항목만 본다."""
    violations = tuple(check_state(trace))
    all_misses = [] if trace.error is None else [f"끝까지 못 돌았다: {trace.error}"]
    all_misses += misses
    return Verdict(
        passed=not violations and not all_misses,
        violations=violations,
        misses=tuple(all_misses),
    )


def record_week(
    case_id: str,
    step: Step,
    trace: Trace,
    verdict: Verdict,
    *,
    persona: str,
    day: date,
    input_count: int,
) -> dict[str, Any]:
    """W 결과 jsonl 한 줄 — `record` 와 같은 모양에 W 칸(week · by · day · step_kind)을 더한다."""
    return {
        "case": f"{case_id}:{step.tag}",
        "ability": step.ability[0] if step.ability else "-",
        "severity": "quality",
        "headline": None,
        "pair": None,
        "repeat": 1,
        "attempt": trace.attempt,
        "input_kind": {"check": "end_state"}.get(step.kind, step.kind)
        if step.kind in ("check", "approve", "correct")
        else (trace.intent or "none"),
        "passed": verdict.passed,
        "violations": [str(v) for v in verdict.violations],
        "misses": list(verdict.misses),
        "trace": trace.to_json(),
        "week": case_id,
        "persona": persona,
        "by": step.by,
        "day": day.isoformat(),
        "at": step.at,
        "step_kind": step.kind,
        "guardian_day_inputs": input_count,
    }
