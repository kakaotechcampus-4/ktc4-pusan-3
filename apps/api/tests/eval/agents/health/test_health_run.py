"""Health `run()` 케이스 재실행 eval — `test_input_health.txt` 의 입력을 전부 돌려 코드가 판정한다.

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/health/test_health_run.py -m live -s              # 전부
    uv run pytest tests/eval/agents/health/test_health_run.py -m live -s -k "T13"     # 하나

기준은 `docs/agents/health/test_expected_health.md`. Health 는 tool 을 하나씩 붙일 때마다 앞에서
돌려 본 케이스가 그대로인지 다시 돌려 보고 응답을 읽고 넘어간다 — 그래서 표는 처음부터 전부 있고,
구현이 없는 칸은 실패가 아니라 GAP 으로 남는다.

판정은 셋이다 (judges.py).
  failures  스펙을 어긴 것 → 케이스 실패
  gaps      아직 구현이 없는 것 → `pytest.xfail` 로 끝낸다. 공용 conftest 가 결과 파일에 PASS 가
            아니라 `XFAIL · 알고 있는 구멍 — …` 으로 적는다. 남은 일이 눈에 보이는 게 목적이다
  notes     판정 못 한 것 · 세계에 아직 없는 데이터

아이 · 동의 · 안전 정보는 전부 가짜다(`world.py`). 모델을 부르는 라벨이 아직 없어서 LLM 키 없이도
끝까지 돈다. 결과는 `test_health_run_results.jsonl` 에 한 줄씩 남는다.

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from app.agents.common.llm_client import LLMClient, LLMConfigError
from app.agents.common.schemas.task import DomainTask
from app.agents.health import agent as health_agent
from app.agents.health.registry import IMPLEMENTED_TASKS, MODEL_TASKS
from app.agents.health.schemas.task import HealthTaskType
from tests.eval.agents.health.cases import CASES, OUTSIDE, Case, doc_inputs
from tests.eval.agents.health.judges import Ran, common
from tests.eval.agents.health.world import DAY, NIGHT, build_world

pytestmark = pytest.mark.live

RESULT_PATH = Path(
    os.getenv("TEST_RESULT_PATH", str(Path(__file__).with_name("test_health_run_results.jsonl")))
)

_clients: dict[str, LLMClient] = {}


def _client_for(label: HealthTaskType | None) -> LLMClient | None:
    """모델을 부르는 라벨이 구현된 뒤에만 만든다. 그 전에는 늘 None — LLM 설정이 없어도 돈다."""
    if label is None or label not in MODEL_TASKS or label not in IMPLEMENTED_TASKS:
        return None
    if "health" not in _clients:
        try:
            _clients["health"] = LLMClient(role="health")
        except LLMConfigError:
            pytest.skip("HEALTH_* 또는 MEMORY_* 모델 설정이 필요합니다")
    return _clients["health"]


@pytest.fixture(scope="module", autouse=True)
def _fresh_results() -> None:
    """결과 파일은 이 모듈을 돌릴 때마다 새로 쓴다 — 지난 실행과 섞이면 요약이 틀린다."""
    RESULT_PATH.unlink(missing_ok=True)


def _write(record: dict[str, Any]) -> None:
    with RESULT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _say(text: str) -> None:
    """콘솔 인코딩(Windows cp949)이 못 쓰는 글자 때문에 케이스가 죽지 않게 한다."""
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, "replace").decode(encoding))


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_health_run(case: Case, expect: Callable[..., None]) -> None:
    expect(case.expect)
    world = build_world(case.world, now=NIGHT if case.night else DAY, run_id=f"live-{case.case_id}")
    request = doc_inputs()[case.input_id]

    started = time.perf_counter()
    if case.label is None:
        # hook 케이스 — Health-4 가 hook 함수를 붙인다
        result = None
    else:
        client = _client_for(case.label)
        task = DomainTask(
            run_id=world.context.run_id,
            agent="health",
            task_type=case.label.value,
            request_texts=(request,),
        )
        # 모듈 속성으로 부른다 — conftest 가 감싼 run 이 걸려야 결과 파일에 agent call 이 남는다
        result = await health_agent.run(task, world.context, client=client)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    ran = Ran(
        case_id=case.case_id,
        world=world,
        request=request,
        result=result,
        elapsed_ms=elapsed_ms,
    )
    verdict = common(ran).extend(case.judge(ran))

    outcome = "FAIL" if verdict.failures else ("GAP" if verdict.gaps else "PASS")
    label = case.label.value if case.label else "hook"
    _say(f"\n[{case.case_id}] {outcome}  {request!r}")
    _say(f"    {label} · 세계 {case.world} · 기대: {case.expect}")
    if result is not None:
        _say(f"    status={result.status} 모델 {result.model_calls}회 {elapsed_ms}ms")
        for readout in result.readouts:
            _say(f"    · {readout.body}")
        if result.needs_observation:
            _say(f"    ? {result.needs_observation[0]}")
    for title, items in (
        ("실패", verdict.failures),
        ("미구현", verdict.gaps),
        ("참고", verdict.notes),
    ):
        for item in items:
            _say(f"    {title}: {item}")

    _write(
        {
            "case": case.case_id,
            "input": case.input_id,
            "label": label,
            "world": case.world,
            "outcome": outcome,
            "status": result.status if result else None,
            "model_calls": result.model_calls if result else None,
            "readouts": [{"kind": r.kind, "body": r.body} for r in result.readouts]
            if result
            else [],
            "needs_observation": list(result.needs_observation) if result else [],
            "medication_drafts": [asdict(d) for d in result.medication_drafts] if result else [],
            "event_requests": [asdict(e) for e in result.event_requests] if result else [],
            "tools": list(ran.tools),
            "failures": verdict.failures,
            "gaps": verdict.gaps,
            "notes": verdict.notes,
            "elapsed_ms": elapsed_ms,
        }
    )

    if verdict.failures:
        pytest.fail("\n".join(verdict.failures))
    if verdict.gaps:
        pytest.xfail("; ".join(verdict.gaps))


@pytest.mark.parametrize("input_id", sorted(OUTSIDE))
def test_health_outside(input_id: str) -> None:
    pytest.skip(f"Health 밖 — 연결 이슈에서: {OUTSIDE[input_id]}")
