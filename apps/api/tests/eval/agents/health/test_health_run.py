"""Health `run()` 케이스 재실행 eval — `test_input_health.txt` 의 입력을 전부 돌려 코드가 판정한다.

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/health/test_health_run.py -m live -s              # 전부
    uv run pytest tests/eval/agents/health/test_health_run.py -m live -s -k "T13"     # 하나
    $env:TEST_REPEAT=3; uv run pytest tests/eval/agents/health/test_health_run.py -m live -s

기준은 `docs/agents/health/test_expected_health.md`. Health 는 tool 을 하나씩 붙일 때마다 앞에서
돌려 본 케이스가 그대로인지 다시 돌려 보고 응답을 읽고 넘어간다 — 그래서 표는 처음부터 전부 있고,
구현이 없는 칸은 실패가 아니라 GAP 으로 남는다.

판정은 셋이다 (judges.py).
  failures  스펙을 어긴 것 → 케이스 실패
  gaps      아직 구현이 없는 것 → `pytest.xfail` 로 끝낸다. 공용 conftest 가 결과 파일에 PASS 가
            아니라 `XFAIL · 알고 있는 구멍 — …` 으로 적는다. 남은 일이 눈에 보이는 게 목적이다
  notes     판정 못 한 것 · 세계에 아직 없는 데이터

아이 · 동의 · 안전 정보 · 복약 코스 · 체온 · 식사 기록은 전부 가짜다(`world.py`). 모델을 부르는
케이스(T01–T05 · T12–T16 · 진료 준비 정리 T23 · T24)만 LLM 설정(`HEALTH_*` 또는 `MEMORY_*`)이
필요하고, 나머지는 키 없이도 끝까지 돈다. 먹인 약 케이스(T21-memory · T21-linked)는 Health 가
아니라 Memory 를 돌려 저장된 행을 본다(`MEMORY_*`). 되묻기를 이어받는 케이스는 `followups` 로 다음
턴을 이어 돈다(`runner.py`). 결과는 `test_health_run_results.jsonl` 에 한 줄씩 남는다.

진료 준비 · 열 타임라인 문장은 `-s` 콘솔 · 공용 conftest 가 쓰는 `test_health_run_result.txt` ·
jsonl 의 `readouts` 에서 읽는다. 금지어가 실행마다 흔들리는지는 같은 케이스를 여러 번 돌려 본다
(`$env:TEST_REPEAT=3` — 실패는 `#n` 회차를 붙여 모은다).

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from app.agents.common.llm_client import LLMClient, LLMConfigError
from app.agents.health.registry import IMPLEMENTED_TASKS, MODEL_TASKS
from app.agents.health.schemas.task import HealthTaskType
from app.core.agent_config import AgentRole
from tests.eval.agents.health.cases import CASES, OUTSIDE, Case
from tests.eval.agents.health.judges import Ran, Verdict, common
from tests.eval.agents.health.runner import run_case

pytestmark = pytest.mark.live

RESULT_PATH = Path(
    os.getenv("TEST_RESULT_PATH", str(Path(__file__).with_name("test_health_run_results.jsonl")))
)

# 같은 케이스를 몇 번 돌릴지 — 모델 판정(금지어 · 약 이름)이 실행마다 흔들리는지 본다
TEST_REPEAT = int(os.getenv("TEST_REPEAT", "1"))

_clients: dict[AgentRole, LLMClient] = {}


def _client_for(case: Case) -> LLMClient | None:
    """모델을 부르는 라벨이 구현된 뒤에만 만든다. 그 전에는 늘 None — LLM 설정이 없어도 돈다.

    Memory 저장 케이스는 Memory 의 모델이다(`role="memory"`).
    """
    if case.memory:
        return _client("memory", "MEMORY_* 모델 설정이 필요합니다")
    label: HealthTaskType | None = case.label
    if label is None or label not in MODEL_TASKS or label not in IMPLEMENTED_TASKS:
        return None
    return _client("health", "HEALTH_* 또는 MEMORY_* 모델 설정이 필요합니다")


def _client(role: AgentRole, missing: str) -> LLMClient:
    if role not in _clients:
        try:
            _clients[role] = LLMClient(role=role)
        except LLMConfigError:
            pytest.skip(missing)
    return _clients[role]


@pytest.fixture(scope="module", autouse=True)
def _fresh_results() -> None:
    """결과 파일은 이 모듈을 돌릴 때마다 새로 쓴다 — 지난 실행과 섞이면 요약이 틀린다."""
    RESULT_PATH.unlink(missing_ok=True)


def _write(record: dict[str, Any]) -> None:
    with RESULT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# Memory 저장 케이스에서 볼 칸 — 한 행 = 한 사건인지 · 연결 · 시각 · 체온
_ROW_FIELDS = ("symptom", "medication_given", "linked_symptom_id", "observed_time", "temperature")


def _row_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: fields.get(key) for key in _ROW_FIELDS}


def _say(text: str) -> None:
    """콘솔 인코딩(Windows cp949)이 못 쓰는 글자 때문에 케이스가 죽지 않게 한다."""
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, "replace").decode(encoding))


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_health_run(case: Case, expect: Callable[..., None]) -> None:
    expect(case.expect)
    client = _client_for(case)
    broken: list[str] = []
    gaps: list[str] = []
    for attempt in range(1, TEST_REPEAT + 1):
        ran = await run_case(case, client=client)
        verdict = common(ran).extend(case.judge(ran))
        _report(case, ran, verdict, attempt)
        broken.extend(
            f"#{attempt} {item}" if TEST_REPEAT > 1 else item for item in verdict.failures
        )
        gaps.extend(item for item in verdict.gaps if item not in gaps)

    if broken:
        pytest.fail("\n".join(broken))
    if gaps:
        pytest.xfail("; ".join(gaps))


def _report(case: Case, ran: Ran, verdict: Verdict, attempt: int) -> None:
    """한 번 돈 결과를 콘솔과 jsonl 에 남긴다. 반복하면 케이스 id 뒤에 #n 이 붙는다."""
    result, request, elapsed_ms = ran.result, ran.request, ran.elapsed_ms
    outcome = "FAIL" if verdict.failures else ("GAP" if verdict.gaps else "PASS")
    label = case.label.value if case.label else ("memory" if case.memory else "hook")
    suffix = f" #{attempt}" if TEST_REPEAT > 1 else ""
    _say(f"\n[{case.case_id}{suffix}] {outcome}  {request!r}")
    _say(f"    {label} · 세계 {case.world} · 기대: {case.expect}")
    for number, turn in enumerate(ran.turns, start=1):
        turn_result = turn.result
        _say(
            f"    [{number}턴] {turn.request!r} → status={turn_result.status} "
            f"모델 {turn_result.model_calls}회 쓰기 {len(turn.writes)}건"
        )
        for readout in turn_result.readouts:
            _say(f"    · {readout.body}")
        for draft in turn_result.medication_drafts:
            _say(f"    + 초안 {draft.op} {draft.notice_times} missing={draft.missing}")
        if turn_result.needs_observation:
            _say(f"    ? {turn_result.needs_observation[0]}")
    for row in ran.memory_rows:
        _say(f"    = {row.domain} {_row_fields(row.fields)}")
    if result is not None or case.memory:
        _say(f"    {elapsed_ms}ms")
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
            "attempt": attempt,
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
            "turns": [
                {
                    "request": turn.request,
                    "status": turn.result.status,
                    "model_calls": turn.result.model_calls,
                    "writes": [
                        [str(schedule_id), at.isoformat()] for schedule_id, at in turn.writes
                    ],
                    "readouts": [r.body for r in turn.result.readouts],
                    "needs_observation": list(turn.result.needs_observation),
                    "drafts": [asdict(d) for d in turn.result.medication_drafts],
                }
                for turn in ran.turns
            ],
            "memory_rows": [
                {"id": row.id, "domain": row.domain, **_row_fields(row.fields)}
                for row in ran.memory_rows
            ],
            "failures": verdict.failures,
            "gaps": verdict.gaps,
            "notes": verdict.notes,
            "elapsed_ms": elapsed_ms,
        }
    )


@pytest.mark.parametrize("input_id", sorted(OUTSIDE))
def test_health_outside(input_id: str) -> None:
    pytest.skip(f"Health 밖 — 연결 이슈에서: {OUTSIDE[input_id]}")
