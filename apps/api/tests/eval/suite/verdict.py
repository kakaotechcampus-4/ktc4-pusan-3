"""attempt 하나를 판정하고, 반복을 모아 케이스 합격을 정한다 (docs/eval/eval-v1.md §4).

run 판정은 세 가지다 (리뷰 11-08 — jjaa0803 안).
- fail     불변식(§2) 위반이 하나라도 있거나, 기대 미달이 있다
- untested 위반은 없고, 되묻기를 했고, 미달이 전부 "되묻느라 안 나온 것"(expect.ASKED_INSTEAD)뿐
           — 알레르기 필터 같은 검사를 한 번도 거치지 않았으니 통과도 실패도 아니다
- pass     위반 · 미달 없음

제품 run 실패(`trace.failed`)는 "끝까지 못 돌았다" 와 같은 자리의 미달이다 — 실패한 run 이
"아무 일 없음" 을 기대하는 케이스를 통과시키지 않게. 20초 부분 결과(`trace.partial`)는 §2 가
허용하는 동작이라 미달로 세지 않는다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from tests.eval.suite.expect import ASKED_INSTEAD, Miss, check_expect
from tests.eval.suite.invariants import Violation, check_invariants, check_state
from tests.eval.suite.schema import Expect, ProbeCase, Step
from tests.eval.suite.trace import Trace

Status = Literal["pass", "fail", "untested"]


@dataclass(frozen=True)
class Verdict:
    passed: bool
    violations: tuple[Violation, ...]
    misses: tuple[str, ...]
    status: Status = "pass"


def run_misses(trace: Trace) -> list[Miss]:
    """기대와 상관없이 run 자체가 끝까지 못 간 것 — 하네스 오류 · 제품 run 실패."""
    if trace.error is not None:
        return [Miss(f"끝까지 못 돌았다: {trace.error}", "error")]
    if trace.failed is not None:
        return [Miss(f"제품 run 실패: {trace.failed}", "failed")]
    return []


def run_status(trace: Trace, violations: Sequence[Violation], misses: Sequence[str]) -> Status:
    if violations:
        return "fail"
    if not misses:
        return "pass"
    asked = bool(trace.questions)
    if asked and all(getattr(m, "kind", "other") in ASKED_INSTEAD for m in misses):
        return "untested"
    return "fail"


def _verdict(trace: Trace, violations: Sequence[Violation], misses: Sequence[str]) -> Verdict:
    status = run_status(trace, violations, misses)
    return Verdict(
        passed=status == "pass",
        violations=tuple(violations),
        misses=tuple(misses),
        status=status,
    )


def judge(case: ProbeCase, trace: Trace, *, today: date) -> Verdict:
    violations = check_invariants(trace, allergies=case.world.allergies)
    misses: list[str] = [*run_misses(trace)]
    if trace.error is None:
        misses += check_expect(trace, case.expect, today=today)
    return _verdict(trace, violations, misses)


def required_passes(severity: str, repeat: int) -> int:
    """종류와 상관없이 반복 전부(pass^k) — #776 과 맞춘 기준 (11-08).

    severity 는 남겨 둔다 — safety 는 첫 실패에서 반복을 멈추고(비용), quality 는 끝까지 돌려
    "불안정"(일부만 통과)을 가려낸다.
    """
    del severity
    return repeat


def case_passed(severity: str, passes: Sequence[bool], repeat: int) -> bool:
    """반복 수와 정확히 같은 run 이 전부 통과 — 재실행이 섞여 run 이 더 많아도 통과가 아니다."""
    return len(passes) == required_passes(severity, repeat) and all(passes)


def case_status(statuses: Sequence[str], repeat: int) -> Status:
    """실패 run 이 하나라도 → 실패. 아니고 시험 못 한 run 이 섞였거나 덜 돌았으면 → 판정 못 함.
    반복 수만큼 전부 통과 → 통과."""
    if "fail" in statuses or len(statuses) > repeat:
        return "fail"
    if "untested" in statuses or len(statuses) < repeat:
        return "untested"
    return "pass"


def case_unstable(passes: Sequence[bool]) -> bool:
    """일부만 통과 — 실패로 세되 "불안정" 으로 따로 표시한다."""
    return any(passes) and not all(passes)


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
        "status": verdict.status,
        "violations": [str(v) for v in verdict.violations],
        "misses": [str(m) for m in verdict.misses],
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
    violations = check_invariants(trace, allergies=allergies)
    misses: list[str] = [*run_misses(trace), *extra_misses]
    if trace.error is None:
        misses += check_expect(trace, expect, today=today)
    return _verdict(trace, violations, misses)


def judge_check(trace: Trace, *, misses: Sequence[str]) -> Verdict:
    """W 끝 상태 점검 — run 이 없으니 DB 상태 불변식(I5 · I8)과 점검 항목만 본다."""
    violations = check_state(trace)
    all_misses: list[str] = [*run_misses(trace), *misses]
    return _verdict(trace, violations, all_misses)


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
        "status": verdict.status,
        "violations": [str(v) for v in verdict.violations],
        "misses": [str(m) for m in verdict.misses],
        "trace": trace.to_json(),
        "week": case_id,
        "persona": persona,
        "by": step.by,
        "day": day.isoformat(),
        "at": step.at,
        "step_kind": step.kind,
        "guardian_day_inputs": input_count,
    }
