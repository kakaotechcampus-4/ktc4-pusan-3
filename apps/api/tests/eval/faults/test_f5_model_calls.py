"""F5 (P1) — `model_calls` 상한 경고가 실제로 뜨는가 · 재시도를 세는가 (eval-v1 §6 · NF-01).

`model_calls` 는 실제 API 호출 수가 아니라 "Agent 진입 1 + 실패해서 다시 한 호출" 이다
(`pipeline.py:87-95`). tool 루프 왕복 · SDK 내부 재시도 · 예외로 끝난 Agent 의 호출은 세지
않는다 — 그 차이는 스위트 C 가 `llm_usage` 로 잰다 (eval-v1 §8 · §12-1). 여기서는 코드가 약속한
것만 본다.
    - Supervisor 의 tool_choice 폴백은 센다 (재시도)
    - 예산(`MAX_MODEL_CALLS`)을 넘으면 WARNING 이 남는다 — 지금까지 "안 뜬다" 만 봤다
"""

import logging
from typing import Any

import pytest

from app.agents import pipeline
from app.agents.common.llm_client import LLMBadRequestError
from app.agents.pipeline import handle_input
from app.agents.suggestion_save import InMemorySuggestions
from tests.eval.agents.routing_cases import CASES_BY_ID, answer_output
from tests.eval.faults.fakes import memory_ctx
from tests.unit.agents.supervisor.test_pipeline import (
    RUN_ID,
    FakeLLM,
    _call,
    _FakeContext,
    _no_commit,
    _pool_memory,
    _runner,
    _tools,
)

_ROUTE = answer_output(CASES_BY_ID["RC16"]).model_dump(mode="json", exclude_none=True)


@pytest.mark.parametrize(
    ("fallback", "expected", "warned"),
    [(False, 4, False), (True, 5, True)],
    ids=["정상_4회", "폴백_5회"],
)
async def test_F5_재시도를_세고_예산을_넘으면_경고한다(
    fallback: bool,
    expected: int,
    warned: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=pipeline.__name__)
    # 가짜 도메인 결과는 model_calls=1 씩이다
    monkeypatch.setitem(pipeline._RUNNERS, "food", _runner("food"))
    monkeypatch.setitem(pipeline._RUNNERS, "activity", _runner("activity"))
    first: list[Any] = [LLMBadRequestError("tool_choice")] if fallback else []
    supervisor = FakeLLM(*first, _tools(_call("s1", "route", _ROUTE)))

    result = await handle_input(
        CASES_BY_ID["RC16"].text,
        memory_ctx(),
        {"food": _FakeContext(), "activity": _FakeContext()},
        run_id=RUN_ID,
        supervisor_client=supervisor,
        memory_client=_pool_memory(),
        emit=lambda event: None,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    # Supervisor (1, 폴백이면 2) + Memory 1 + 도메인 2
    assert result.model_calls == expected
    assert result.rerouted is None  # 다시 나눈 run 이 아니라 예산은 MAX_MODEL_CALLS 그대로
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    budget = pipeline.MAX_MODEL_CALLS
    warning = f"model_calls 예산 초과 run_id={RUN_ID} model_calls={expected} 예산={budget}"
    assert (warning in lines) is warned
