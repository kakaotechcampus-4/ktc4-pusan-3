"""F1 — 장애 경로의 로그에 원문 · 별명이 없다 (docs/eval/eval-v1.md §6 · 루트 §2 · I13).

기존 로그 검사는 정상 경로 · 도메인 Agent 예외 · 러너 예외에만 있다. 세 경로를 더 본다.
    ① pipeline — Memory 모델 오류
    ② 러너 — 60초 안전망 (시간 초과)
    ③ 러너 + 진입점 + pipeline 전체 — Supervisor · Memory 둘 다 모델 오류
모델 오류는 제공처가 요청 원문을 400 메시지에 되돌려 주는 경우로 흉내 낸다.
`LLMBadRequestError` 는 그 메시지를 그대로 담는다 (`llm_client.py:166-168`).
"""

import asyncio
import logging
from uuid import UUID

import pytest

from app.agents.pipeline import Done, Failed, handle_input
from app.agents.suggestion_save import InMemorySuggestions
from app.agents.supervisor.schemas import SupervisorOutput
from app.api import idempotency
from app.api.runs import registry, runner
from app.api.runs.sse import failed_event
from tests.eval.faults.fakes import (
    NICK,
    RAW,
    SlowLLM,
    bind_clients,
    echo_client,
    fake_sessions,
    memory_ctx,
)
from tests.unit.agents.supervisor.test_pipeline import FakeLLM, _call, _no_commit, _tools

CHILD = UUID(int=1)
PARENT = UUID(int=2)

# 원문 전체를 기록 조각 하나로 나눈 Supervisor 답 — Memory 를 부르게 한다
_ROUTE = SupervisorOutput.model_validate(
    {"segments": [{"text": RAW, "kind": "record", "work": "observe"}]}
).model_dump(mode="json", exclude_none=True)


@pytest.fixture(autouse=True)
def _clean():
    registry.clear()
    idempotency.clear()
    yield
    registry.clear()
    idempotency.clear()


def _route_llm() -> FakeLLM:
    return FakeLLM(_tools(_call("s1", "route", _ROUTE)))


def _assert_no_canary(caplog: pytest.LogCaptureFixture) -> None:
    # caplog.text 는 exc_info 로 찍힌 traceback 까지 담는다 — 메시지가 거기로 새도 잡힌다
    assert RAW not in caplog.text
    assert NICK not in caplog.text


async def test_F1_Memory_모델_오류의_원문_되돌림이_로그에_없다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    memory_client, echoed = echo_client("memory")
    events: list[object] = []

    result = await handle_input(
        RAW,
        memory_ctx(),
        {},
        run_id="run-f1",
        supervisor_client=_route_llm(),
        memory_client=memory_client,
        emit=events.append,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    # 제공처 400(요청 거절)은 다시 보내도 안 풀려서 llm_misconfigured 다 (#772 · common/errors.py)
    assert result.failed == Failed("llm_misconfigured", RAW)  # 원문은 화면(failed)으로만 간다
    ends = [type(event).__name__ for event in events if isinstance(event, Failed | Done)]
    assert ends == ["Failed", "Done"]
    assert echoed.echoed and NICK in echoed.echoed[0]  # 제공처는 정말로 원문을 되돌려 줬다
    assert "pipeline run_id=run-f1" in caplog.text  # 로그가 비어서 통과하는 게 아니다
    _assert_no_canary(caplog)


async def test_F1_러너_시간_초과_로그에_원문이_없다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake_sessions(monkeypatch)
    slow = SlowLLM()
    bind_clients(monkeypatch, supervisor=slow, memory=FakeLLM())
    monkeypatch.setattr(runner, "RUN_DEADLINE_SECONDS", 0.2)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW), timeout=5)

    assert slow.started.is_set()
    # 모양(안내 문구 message 포함)은 화면용 failed 를 만드는 함수 하나가 정한다 (#772)
    assert channel.events[-1] == failed_event("timeout", RAW)
    assert [name for name, _ in channel.events if name in ("done", "failed")] == ["failed"]
    assert "안에 끝나지 않아 끊었다" in caplog.text
    _assert_no_canary(caplog)


async def test_F1_러너부터_pipeline_까지_모델_오류의_원문_되돌림이_로그에_없다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    sessions = fake_sessions(monkeypatch)
    supervisor_client, supervisor_echo = echo_client("supervisor")
    memory_client, memory_echo = echo_client("memory")
    bind_clients(monkeypatch, supervisor=supervisor_client, memory=memory_client)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "f1-full"}
    await idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW), timeout=5)

    # Supervisor 는 tool_choice 세 가지를 다 거절당해 강등되고, Memory 가 원문으로 돌다 죽는다
    # 호출 수(Supervisor 3 · Memory 1)는 의도된 값이다 — fallback 횟수가 바뀌면 여기서 알아챈다
    assert len(supervisor_echo.echoed) == 3 and len(memory_echo.echoed) == 1
    # 400 은 설정 · 요청 거절이라 llm_misconfigured 다 (#772)
    assert channel.events[-1] == failed_event("llm_misconfigured", RAW)
    assert [name for name, _ in channel.events if name in ("done", "failed")] == ["failed"]
    assert [session.commits for session in sessions] == [0]
    assert await idempotency.recall(**scope) is None  # 저장 없는 실패 — 다시 시도할 수 있다
    assert f"pipeline run_id={channel.run_id}" in caplog.text
    _assert_no_canary(caplog)
