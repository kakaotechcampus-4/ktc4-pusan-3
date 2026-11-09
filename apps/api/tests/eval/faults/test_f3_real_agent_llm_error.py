"""F3 — 진짜 도메인 Agent 가 모델 오류를 낼 때 (docs/eval/eval-v1.md §6 · 루트 §2 부분 결과).

기존 테스트(`test_RC16_한_Agent_가_죽어도_나머지_결과는_나간다`)는 `RuntimeError` 를 던지는
가짜 실행 함수로만 봤다. 여기서는 실제 Food · Activity run() 에 400 을 내는 LLMClient 를 꽂는다.
모델 오류가 Agent 안에서 삼켜지거나 다른 Agent 결과를 막는지를 본다.

보는 것: 남은 Agent 의 카드 · 추천 id 가 나간다 · partial(agent_error) 이 끝 신호 앞에 한 번 ·
기록은 남는다 · 실패한 Agent 의 다시 시도 맥락 · 로그에 원문 조각이 없다.
"""

import logging
from typing import Any

import pytest

from app.agents.pipeline import (
    AgentResult,
    Done,
    Failed,
    Partial,
    RetryContext,
    Saved,
    handle_input,
)
from app.agents.suggestion_save import InMemorySuggestions
from tests.eval.agents.routing_cases import CASES_BY_ID
from tests.eval.faults.fakes import activity_ok, echo_client, food_ctx, food_ok, memory_ctx
from tests.unit.agents.activity.test_activity_agent import context as activity_context
from tests.unit.agents.supervisor.test_pipeline import (
    RUN_ID,
    _no_commit,
    _pool_memory,
    _supervisor_llm,
)

_TEXT = CASES_BY_ID["RC16"].text  # 오늘 수영장 다녀왔어. 저녁 메뉴랑 주말 놀이 추천해줘.


@pytest.mark.parametrize("broken", ["food", "activity"])
async def test_F3_진짜_Agent_하나가_모델_오류를_내도_나머지_결과는_나간다(
    broken: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ok = "activity" if broken == "food" else "food"
    bad_client, echoed = echo_client(broken)
    clients: dict[str, Any] = {"food": food_ok(), "activity": activity_ok(), broken: bad_client}
    saver = InMemorySuggestions()
    events: list[object] = []

    result = await handle_input(
        _TEXT,
        memory_ctx(),
        {"food": food_ctx(), "activity": activity_context()},
        run_id=RUN_ID,
        supervisor_client=_supervisor_llm("RC16"),
        memory_client=_pool_memory(),
        domain_clients=clients,
        emit=events.append,
        commit=_no_commit,
        save_suggestions=saver,
    )

    # 실패한 쪽도 모델을 부르는 데까지 갔고, 제공처가 요청 조각을 되돌려 줬다
    assert echoed.echoed and any(f in echoed.echoed[0] for f in ("저녁 메뉴", "주말 놀이"))
    assert result.failed is None
    assert result.partial == Partial(
        "agent_error", succeeded=(ok,), failed=(broken,), retryable=(broken,)
    )
    [card] = [event for event in events if isinstance(event, AgentResult)]
    assert card.agent == ok and card.status == "completed"
    assert len(card.suggestion_ids) == 3  # 남은 Agent 의 추천은 저장까지 끝나 화면으로 갔다
    assert [batch.agent for batch in saver.batches] == [ok]
    assert [retry.agent for retry in events if isinstance(retry, RetryContext)] == [broken]
    assert any(isinstance(event, Saved) for event in events)  # 기록은 남았다
    names = [type(event).__name__ for event in events]
    assert names[-2:] == ["Partial", "Done"]
    assert sum(isinstance(event, Failed | Done) for event in events) == 1  # 끝 신호 하나 (I12)
    for fragment in ("수영장", "저녁 메뉴", "주말 놀이"):
        assert fragment not in caplog.text
