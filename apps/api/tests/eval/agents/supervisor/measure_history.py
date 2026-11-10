"""SUPERVISOR_EVAL.md §1 측정 이력 한 행을 잰다 (#770 · #861). 라이브 — 크레딧을 쓴다.

    $env:TEST_REPEAT=5; uv run python -m tests.eval.agents.supervisor.measure_history out.json

평가셋은 `routing_cases.ALL_CASES` 전체, 케이스마다 `TEST_REPEAT` 회(기본 5).
같은 조건으로 바꾸기 전 · 후를 재야 하므로 평가셋 · 반복 · 모델을 두 행에서 같게 둔다.

C1 (문서 §2): guarded 정답 구간을 guarded 로 못 받음 · GR 0건 규칙 위반.
오답: strict 구간을 못 찾음 · 검증 실패 · 0건 규칙 위반. 입력별로 R 회 중 몇 번인지 센다.
"""

import asyncio
import json
import os
import sys
from collections import defaultdict

from app.agents.common.llm_client import LLMClient
from app.agents.prompt_registry import all_fingerprints
from app.agents.supervisor import agent as supervisor
from tests.eval.agents.routing_cases import ALL_CASES, growth_violations, score_split

REPEAT = int(os.getenv("TEST_REPEAT", "5"))
CONCURRENCY = int(os.getenv("MEASURE_CONCURRENCY", "8"))


async def main(out: str) -> None:
    client = LLMClient(role="supervisor")
    gate = asyncio.Semaphore(CONCURRENCY)
    c1 = defaultdict(int)
    wrong = defaultdict(int)
    detail = defaultdict(list)

    async def one(case, attempt):
        async with gate:
            result = await supervisor.run(case.text, client=client)
        score = score_split(case, result.output)
        violations = growth_violations(case.case_id, result.output)
        is_c1 = score.guarded_found < score.guarded_spans or bool(violations)
        is_wrong = bool(result.error) or bool(score.missed) or bool(violations)
        if is_c1:
            c1[case.case_id] += 1
        if is_wrong:
            wrong[case.case_id] += 1
            detail[case.case_id].append(
                {
                    "attempt": attempt,
                    "error": result.error,
                    "missed": score.missed,
                    "violations": violations,
                }
            )

    await asyncio.gather(*(one(c, a) for c in ALL_CASES for a in range(1, REPEAT + 1)))
    total = len(ALL_CASES)
    report = {
        "fingerprint": all_fingerprints()["supervisor"].short,
        "repeat": REPEAT,
        "inputs": total,
        "c1_inputs": sorted(c1),
        "c1": f"{len(c1)}/{total}",
        "c1_counts": dict(sorted(c1.items())),
        "stable_wrong": sorted(k for k, v in wrong.items() if v == REPEAT),
        "unstable": sorted(k for k, v in wrong.items() if 0 < v < REPEAT),
        "wrong_counts": dict(sorted(wrong.items())),
        "detail": dict(sorted(detail.items())),
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(
        report["fingerprint"],
        "C1",
        report["c1"],
        "stable",
        len(report["stable_wrong"]),
        "unstable",
        len(report["unstable"]),
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
