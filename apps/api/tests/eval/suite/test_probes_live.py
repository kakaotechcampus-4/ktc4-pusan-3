"""P · 단발 프로브 — 실제 모델로 운영 입구를 돈다 (docs/eval/eval-v1.md §4).

    make eval-smoke                    # smoke: true 케이스만 1회 (~15 입력)
    make eval                          # 전체 33개 × 3회 (~100 입력)
    EVAL_ONLY=P-REC-02 make eval       # 한 케이스

결과는 eval/results/raw/*.jsonl (gitignore) → make eval-report 로 요약 md.
safety 케이스는 첫 실패에서 반복을 멈춘다 — 이미 pass^k 실패라 더 돌릴 필요가 없다(비용).
되묻기로만 끝난 run 은 "시험 못 함" 이라 실패가 아니다 — 반복을 계속하고, 케이스가 판정 못 함이면
pytest 는 skip 으로 남긴다(결과 md 가 따로 센다).
토큰 합이 EVAL_MAX_TOKENS(기본 3,000,000 — 입력 하나 ≈22k 토큰, 전체 ≈2.0M)를 넘으면 남은
케이스를 건너뛰고, 건너뛴 케이스는 결과 jsonl 에 `skipped` 행으로 남긴다(크레딧을 넘으면 키가
지워진다).
"""

import json
import logging
import os
import subprocess
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from app.core import llm_usage
from tests.eval.suite.probe_runner import run_probe
from tests.eval.suite.report import RAW_DIR
from tests.eval.suite.schema import ProbeCase, load_probes
from tests.eval.suite.verdict import case_status, judge, record
from tests.eval.suite.world import KST

pytestmark = pytest.mark.live

TIER = os.getenv("EVAL_TIER", "full")
REPEAT = 1 if TIER == "smoke" else int(os.getenv("EVAL_REPEAT", "3"))
ONLY = os.getenv("EVAL_ONLY")
MAX_TOKENS = int(os.getenv("EVAL_MAX_TOKENS", "3000000"))
RAW_PATH = RAW_DIR / f"probes-{TIER}-{datetime.now(KST):%Y%m%d-%H%M%S}.jsonl"
_spent = {"tokens": 0}


def _git() -> dict[str, object]:
    """결과가 어느 코드에서 나왔는지 — 리포트 시점이 아니라 실행 시점에 한 번 적는다."""

    def run(*args: str) -> str:
        done = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise RuntimeError(args)
        return done.stdout.strip()

    try:
        sha = run("rev-parse", "--short", "HEAD")
        dirty = bool(run("status", "--porcelain", "--untracked-files=no"))
    except (OSError, RuntimeError):
        return {"sha": "unknown", "dirty": None}
    return {"sha": sha, "dirty": dirty}


GIT = _git()


def _selected() -> list[ProbeCase]:
    cases = load_probes()
    if ONLY and ONLY.startswith("W-"):  # W 케이스를 지정했다 — P 는 돌지 않는다
        return []
    if ONLY:
        picked = [case for case in cases if case.id == ONLY]
        if not picked:
            raise ValueError(f"EVAL_ONLY={ONLY!r} 는 없는 케이스 id 다")
        return picked
    if TIER == "smoke":
        return [case for case in cases if case.smoke]
    return cases


def _append(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({**row, "git": GIT}, ensure_ascii=False) + "\n")


@pytest.fixture(scope="module", autouse=True)
def _usage_capture() -> Iterator[None]:
    llm_usage.enable()
    llm_usage.drain(limit=100_000)  # 앞서 쌓인 것을 버린다
    yield
    llm_usage.disable()


@pytest.mark.parametrize("case", _selected(), ids=lambda case: case.id)
async def test_probe(case: ProbeCase, caplog: pytest.LogCaptureFixture) -> None:
    if _spent["tokens"] >= MAX_TOKENS:
        _append(RAW_PATH, {"case": case.id, "skipped": "token_cap", "repeat": REPEAT})
        pytest.skip(f"EVAL_MAX_TOKENS({MAX_TOKENS:,}) 를 넘었다 — 남은 케이스를 건너뛴다")
    caplog.set_level(logging.INFO)
    statuses: list[str] = []
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
        statuses.append(verdict.status)
        reasons += [str(v) for v in verdict.violations] + list(verdict.misses)
        if case.severity == "safety" and verdict.status == "fail":
            break  # 시험 못 함은 실패가 아니다 — 반복을 계속한다
    status = case_status(statuses, REPEAT)
    assert status != "fail", f"{case.id} {statuses} {reasons[:5]}"
    if status == "untested":
        tested = statuses.count("pass")
        pytest.skip(f"{case.id} 판정 못 함 (시험 {tested}/{REPEAT}) — 되묻기로 끝난 run 이 있다")
