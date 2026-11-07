"""F2 — Supervisor · Memory 가 멈출 때 (docs/eval/eval-v1.md §6).

둘은 20초(`pipeline.RUN_DEADLINE_S`)에 끊지 않는다 — 저장이 반만 남지 않게 한 의도된 설계다.
그래서 멈추면 러너의 60초 안전망(`runner.RUN_DEADLINE_SECONDS`)이 끊는다. 기존 러너 테스트는
가짜 진입점(`asyncio.sleep`)으로만 봤다 — 여기서는 진짜 진입점 · pipeline 에 멈춘 모델을 꽂는다.

보는 것: 끝 이벤트 1번 (failed · timeout) · commit 없음 · 멱등 키 풀림 · 멈춘 모델이 정말 불렸음.
"보호자가 60초를 기다린 뒤 실패를 본다" 가 괜찮은지는 테스트로 못 정해 이슈로 올린다
(eval/faults-issues.md 의 ISSUE-F2-UX).
"""

import asyncio
from uuid import UUID

import pytest

from app.agents import pipeline
from app.agents.pipeline import Saved, handle_input
from app.agents.suggestion_save import InMemorySuggestions
from app.agents.supervisor.schemas import SupervisorOutput
from app.api import idempotency
from app.api.runs import registry, runner
from tests.eval.faults.fakes import RAW, SlowLLM, bind_clients, fake_sessions, memory_ctx
from tests.unit.agents.supervisor.test_pipeline import (
    FakeLLM,
    _call,
    _no_commit,
    _reply,
    _tools,
)

CHILD = UUID(int=1)
PARENT = UUID(int=2)
_ROUTE = SupervisorOutput.model_validate(
    {"segments": [{"text": RAW, "kind": "record", "work": "observe"}]}
).model_dump(mode="json", exclude_none=True)
_BROCCOLI = {"raw_text": RAW, "observed_on": "어제", "subject": "브로콜리", "amount": "세 송이"}


@pytest.fixture(autouse=True)
def _clean():
    registry.clear()
    idempotency.clear()
    yield
    registry.clear()
    idempotency.clear()


@pytest.mark.parametrize("stuck", ["supervisor", "memory"])
async def test_F2_멈춘_Supervisor_Memory_는_러너_안전망이_failed_로_끝내고_키를_푼다(
    stuck: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = fake_sessions(monkeypatch)
    slow = SlowLLM()
    route = FakeLLM(_tools(_call("s1", "route", _ROUTE)))
    if stuck == "supervisor":
        bind_clients(monkeypatch, supervisor=slow, memory=FakeLLM())
    else:
        bind_clients(monkeypatch, supervisor=route, memory=slow)
    monkeypatch.setattr(runner, "RUN_DEADLINE_SECONDS", 0.2)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": f"f2-{stuck}"}
    await idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW), timeout=5)

    assert slow.started.is_set()  # 멈춘 쪽까지 실제로 갔다 — 앞에서 다른 이유로 끝난 게 아니다
    if stuck == "memory":
        assert route.calls == 1
    ends = [name for name, _ in channel.events if name in ("done", "failed")]
    assert ends == ["failed"]  # 끝 이벤트는 정확히 하나 (I12)
    assert channel.events[-1] == ("failed", {"reason": "timeout", "raw_text": RAW})
    assert [session.commits for session in sessions] == [0]  # 반쯤 저장된 기록 없음
    assert not channel.committed
    assert await idempotency.recall(**scope) is None  # 같은 키로 다시 시도하면 새 run
    assert channel.closed


async def test_F2_pipeline_은_Supervisor_와_Memory_를_20초에_끊지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """설계를 고정한다. 20초 예산을 거의 0 으로 줄여도 느린 Supervisor 뒤에 Memory 가 저장한다.

    이 동작이 바뀌면 (예: Supervisor 에 짧은 상한) 이 테스트와 ISSUE-F2-UX 를 같이 고친다.
    """
    monkeypatch.setattr(pipeline, "RUN_DEADLINE_S", 0.01)

    class LateRoute(FakeLLM):
        async def chat(self, **kwargs: object) -> object:
            await asyncio.sleep(0.05)  # 20초 예산(0.01초)보다 늦게 답한다
            return await super().chat(**kwargs)

    memory_llm = FakeLLM(
        _tools(_call("m1", "create_observation_food", _BROCCOLI)), _reply("기록했어요.")
    )
    events: list[object] = []
    memory_context = memory_ctx()

    result = await handle_input(
        RAW,
        memory_context,
        {},
        run_id="run-f2",
        supervisor_client=LateRoute(_tools(_call("s1", "route", _ROUTE))),
        memory_client=memory_llm,
        emit=events.append,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    # Supervisor 혼자 20초 예산을 다 썼다 — 그래도 Memory 는 끝까지 돌았다
    assert result.times.supervisor_ms is not None
    assert result.times.supervisor_ms > pipeline.RUN_DEADLINE_S * 1000
    assert result.failed is None
    assert any(isinstance(event, Saved) for event in events)
    rows = await memory_context.store.query_observations(domain="food", child_id=CHILD)
    assert len(rows) == 1
