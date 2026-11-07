"""P · 단발 프로브 — 실제 모델로 운영 입구를 돈다 (docs/eval/eval-v1.md §4).

    make eval-smoke                    # smoke: true 케이스만 1회 (~15 입력)
    make eval                          # 전체 30개 × 3회 (~90 입력)
    EVAL_ONLY=P-REC-02 make eval       # 한 케이스

결과는 eval/results/raw/*.jsonl (gitignore) → make eval-report 로 요약 md.
safety 케이스는 첫 실패에서 반복을 멈춘다 — 이미 pass^k 실패라 더 돌릴 필요가 없다(비용).
토큰 합이 EVAL_MAX_TOKENS 를 넘으면 남은 케이스를 건너뛴다(크레딧을 넘으면 키가 지워진다).
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytest

from app.core import llm_usage
from tests.eval.suite.probe_runner import run_probe
from tests.eval.suite.report import RAW_DIR
from tests.eval.suite.schema import ProbeCase, load_probes
from tests.eval.suite.verdict import case_passed, judge, record
from tests.eval.suite.world import KST

pytestmark = pytest.mark.live

TIER = os.getenv("EVAL_TIER", "full")
REPEAT = 1 if TIER == "smoke" else int(os.getenv("EVAL_REPEAT", "3"))
ONLY = os.getenv("EVAL_ONLY")
MAX_TOKENS = int(os.getenv("EVAL_MAX_TOKENS", "2000000"))
RAW_PATH = RAW_DIR / f"probes-{TIER}-{datetime.now(KST):%Y%m%d-%H%M}.jsonl"
_spent = {"tokens": 0}


def _selected() -> list[ProbeCase]:
    cases = load_probes()
    if ONLY:
        return [case for case in cases if case.id == ONLY]
    if TIER == "smoke":
        return [case for case in cases if case.smoke]
    return cases


def _append(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.fixture(scope="module", autouse=True)
def _usage_capture() -> object:
    llm_usage.enable()
    llm_usage.drain(limit=100_000)  # 앞서 쌓인 것을 버린다
    yield
    llm_usage.disable()


@pytest.mark.parametrize("case", _selected(), ids=lambda case: case.id)
async def test_probe(case: ProbeCase, caplog: pytest.LogCaptureFixture) -> None:
    if _spent["tokens"] >= MAX_TOKENS:
        pytest.skip(f"EVAL_MAX_TOKENS({MAX_TOKENS:,}) 를 넘었다 — 남은 케이스를 건너뛴다")
    caplog.set_level(logging.INFO)
    passes: list[bool] = []
    reasons: list[str] = []
    for attempt in range(REPEAT):
        caplog.clear()
        trace = await run_probe(
            case,
            attempt=attempt,
            logs=lambda: [caplog.handler.format(r) for r in caplog.records],
        )
        verdict = judge(case, trace, today=datetime.now(KST).date())
        _spent["tokens"] += trace.tokens
        _append(RAW_PATH, record(case, trace, verdict, repeat=REPEAT))
        passes.append(verdict.passed)
        reasons += [str(v) for v in verdict.violations] + list(verdict.misses)
        if case.severity == "safety" and not verdict.passed:
            break
    assert case_passed(case.severity, passes, REPEAT), f"{case.id} {passes} {reasons[:5]}"
