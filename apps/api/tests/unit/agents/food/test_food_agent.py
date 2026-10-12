"""Food Agent 진입점 — run() 을 공통 tool 루프 위에서 끝까지 돈다."""

import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import DomainTask
from app.agents.common.tool_loop import MAX_STEPS
from app.agents.food.agent import run
from app.agents.food.context import FoodContext
from app.agents.food.readouts import READOUTS
from app.agents.food.store import InMemoryProfile, in_memory_ports, unwired_ports

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=KST)
TODDLER_BIRTH = date(2024, 6, 2)  # NOW 기준 28개월
INFANT_BIRTH = date(2026, 2, 2)  # NOW 기준 8개월 — infant_weaning
USAGE = {"prompt_tokens": 10, "completion_tokens": 5}


class FakeLLM:
    """정해 둔 응답을 순서대로 돌려준다. 다 쓴 뒤에 또 부르면 실패한다."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        if not self._queue:
            raise AssertionError("응답을 다 썼는데 모델을 또 불렀다 — 닫힌 경로는 모델 0회가 맞다")
        return self._queue.pop(0)


def call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    raw = json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=raw))


def tool_response(*calls: SimpleNamespace) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=list(calls)), usage=USAGE, latency_ms=1
    )


def reply(content: str) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=content, tool_calls=None), usage=USAGE, latency_ms=1
    )


def _context(birth_date: date, **port_overrides: object) -> FoodContext:
    defaults: dict[str, object] = {"profile": InMemoryProfile({CHILD: birth_date})}
    defaults.update(port_overrides)
    return FoodContext(
        child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=in_memory_ports(**defaults)
    )


@pytest.fixture
def task() -> DomainTask:
    return DomainTask(
        run_id="run-1",
        agent="food",
        task_type="nutrient_analysis",
        request_texts=("요즘 영양은 괜찮은지 봐줘",),
    )


@pytest.fixture
def context_infant() -> FoodContext:
    return _context(INFANT_BIRTH)


@pytest.fixture
def context_unwired() -> FoodContext:
    ports = unwired_ports(profile=InMemoryProfile({CHILD: TODDLER_BIRTH}))
    return FoodContext(child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=ports)


@pytest.fixture
def context() -> FoodContext:
    return _context(TODDLER_BIRTH)


@pytest.fixture
def counting_client() -> FakeLLM:
    return FakeLLM()  # 큐가 비어 있다 — 한 번이라도 부르면 AssertionError


@pytest.fixture
def fake_client() -> FakeLLM:
    return FakeLLM(
        tool_response(
            call(
                "c1",
                "report_nutrient_analysis",
                {
                    "period": "last_7_days",
                    "findings": ["최근 식사 기록을 바탕으로 식단을 살펴봤어요"],
                },
            )
        )
    )


@pytest.fixture
def insufficient_client() -> FakeLLM:
    # 빈 포트라 기록이 0건 — compare_diet_balance 가 insufficient=True 를 내고,
    # report_nutrient_analysis 의 ⓪ 검사가 요구하는 대로 findings 를 비워 낸다
    return FakeLLM(
        tool_response(call("c1", "compare_diet_balance", {"period": "last_7_days"})),
        tool_response(
            call("c2", "report_nutrient_analysis", {"period": "last_7_days", "findings": []})
        ),
    )


@pytest.fixture
def talking_client() -> FakeLLM:
    return FakeLLM(reply("분석을 준비했어요."))


@pytest.fixture
def looping_client() -> FakeLLM:
    # 출력 tool 을 한 번도 안 부르고 조회만 반복해 MAX_STEPS 에서 끊긴다
    return FakeLLM(
        *(
            tool_response(call(f"c{i}", "lookup_nutrition", {"foods": ["밥"]}))
            for i in range(MAX_STEPS)
        )
    )


async def test_닫힌_경로는_모델을_0회_부른다(task, context_infant, counting_client) -> None:
    result = await run(task, context_infant, client=counting_client)

    assert result.status == "closed"
    assert result.model_calls == 0
    assert result.readouts[0].body == READOUTS.get("unsupported.infant_nutrient").template


async def test_안전_조회_실패는_blocked_safety_로_닫는다(
    task, context_unwired, counting_client
) -> None:
    # 안전 구멍 ②③ — unwired_ports() 는 조회를 실패시킨다
    result = await run(task, context_unwired, client=counting_client)

    assert result.model_calls == 0
    assert result.readouts[0].body == READOUTS.get("blocked.safety").template


async def test_출력_tool_이_통과하면_ok_다(task, context, fake_client) -> None:
    result = await run(task, context, client=fake_client)

    assert result.status == "ok"
    assert result.readouts


async def test_기록이_부족하면_insufficient_readout_을_낸다(
    task, context, insufficient_client
) -> None:
    # findings 가 비어도 ok 로 끝나는 것 자체는 맞다(⓪ 검사가 요구하는 모양) — 그런데 그 상태를
    # 그대로 readouts=() 로 내면 부모 화면에 아무것도 안 뜬다. insufficient 전용 문구가 나가야 한다
    result = await run(task, context, client=insufficient_client)

    assert result.status == "ok"
    assert result.readouts, "기록 부족 상태에서 readout 이 비었다 — 화면에 아무것도 안 뜬다"
    assert result.readouts[0].body == READOUTS.get("nutrient.insufficient").template.format(
        recorded=0
    )


async def test_모델이_말로_끝내면_실패다(task, context, talking_client) -> None:
    # no_tool_call 을 추천 0개로 받으면 모델 실패가 "말할 게 없다" 로 보인다 (#320 PM 리뷰)
    result = await run(task, context, client=talking_client)

    assert result.status == "failed"


async def test_상한까지_출력_tool_이_통과하지_못하면_실패다(task, context, looping_client) -> None:
    result = await run(task, context, client=looping_client)

    assert result.status == "failed"
