"""F4 — 도메인 Agent 의 기억 읽기(`memory.search`)가 터질 때 (docs/eval/eval-v1.md §6 · 루트 §2).

운영에서 도메인 Agent 의 기억 포트는 호출마다 따로 연 store 로 읽는다 (러너의
`domain_store_opener`). 그 store 가 터진 경우를 흉내 낸다 — Memory 가 쓰는 run store 는 멀쩡하다.

보는 것: 근거를 못 읽었으면 개인화 추천이 하나도 저장 · 표시되지 않는다 · 그 Agent 는 partial 로
빠진다 · 기록은 남는다 · 끝 신호 하나. Food 는 모델 전 선조회(`prefetch_food_memory`)에서,
Activity 는 모델이 부른 `search_activity_memory` 에서 터진다 — 두 길을 같이 본다.
"""

from dataclasses import replace

from app.agents.memory_bridge import StoreActivityMemory, StoreFoodMemory
from app.agents.pipeline import AgentResult, Done, Failed, Partial, Saved, handle_input
from app.agents.suggestion_save import InMemorySuggestions
from tests.eval.agents.routing_cases import CASES_BY_ID
from tests.eval.faults.fakes import (
    FailingStore,
    activity_ok,
    food_ctx,
    food_ok,
    memory_ctx,
    opener_of,
)
from tests.unit.agents.activity.test_activity_agent import context as activity_context
from tests.unit.agents.supervisor.test_pipeline import (
    NOW,
    RUN_ID,
    _no_commit,
    _pool_memory,
    _supervisor_llm,
)


async def test_F4_기억을_못_읽으면_개인화_추천이_나가지_않고_partial_로_빠진다() -> None:
    opener = opener_of(FailingStore(now=NOW))
    activity = activity_context()
    contexts = {
        "food": food_ctx(memory=StoreFoodMemory(opener=opener)),
        "activity": replace(
            activity, ports=replace(activity.ports, memory=StoreActivityMemory(opener=opener))
        ),
    }
    food_llm, activity_llm = food_ok(), activity_ok()
    saver = InMemorySuggestions()
    events: list[object] = []

    result = await handle_input(
        CASES_BY_ID["RC16"].text,
        memory_ctx(),
        contexts,
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm("RC16"),
        memory_client=_pool_memory(),
        domain_clients={"food": food_llm, "activity": activity_llm},
        emit=events.append,
        commit=_no_commit,
        save_suggestions=saver,
    )

    # 개인화 0 — 저장된 추천도, 화면 카드도 없다 (루트 §2 "근거 없이 개인화 금지")
    assert not any(d.kind == "personalized" for b in saver.batches for d in b.drafts)
    assert saver.batches == []
    assert not [event for event in events if isinstance(event, AgentResult)]
    # 두 Agent 모두 기억을 읽다 멈췄다. Food 는 모델 전에, Activity 는 조회 tool 에서
    assert food_llm.calls == 0
    assert len(activity_llm.seen) == 1
    assert result.partial == Partial(
        "agent_error",
        succeeded=(),
        failed=("food", "activity"),
        retryable=("food", "activity"),
    )
    assert result.failed is None  # 기록은 저장됐으니 실패가 아니다
    assert any(isinstance(event, Saved) for event in events)
    assert sum(isinstance(event, Failed | Done) for event in events) == 1
