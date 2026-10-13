"""Food Agent 진입점 — run() 을 공통 tool 루프 위에서 끝까지 돈다."""

import json
import logging
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
from app.agents.food.nutrition_eval import NutrientVerdict
from app.agents.food.readouts import NUTRIENT_INSUFFICIENT, READOUTS
from app.agents.food.store import (
    InMemoryConsent,
    InMemoryProfile,
    InMemorySafety,
    SafetyEntry,
    in_memory_ports,
    unwired_ports,
)

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
    # compare_diet_balance 는 이미 불렀다고 보고(아래 테스트가 판정을 run state 에 둔다) 문장을 낸다
    return FakeLLM(
        tool_response(
            call(
                "c1",
                "report_nutrient_analysis",
                {
                    "period": "last_7_days",
                    "findings": ["기록된 식사를 보면 단백질 식품이 적은 편이에요"],
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


async def test_출력_tool_이_통과하면_모델_문장이_그대로_ok_다(task, context, fake_client) -> None:
    # 모델 경로 — 코드 문장으로 바뀌지 않고 모델이 쓴 문장이 나간다
    context.state.nutrient_verdict = _verdict({"protein": "low"})
    context.state.basis_keys = {"protein"}

    result = await run(task, context, client=fake_client)

    assert result.status == "ok"
    (readout,) = result.readouts
    assert readout.authored_by == "model"
    assert readout.body == "기록된 식사를 보면 단백질 식품이 적은 편이에요"


async def test_판정_없이_report_를_부르면_거절되고_코드_문장으로_간다(task, context) -> None:
    # 판정은 코드가 한다 — compare_diet_balance 를 건너뛴 모델 문장은 나가지 않는다 (최종 리뷰)
    client = FakeLLM(
        tool_response(
            call(
                "c1",
                "report_nutrient_analysis",
                {
                    "period": "last_7_days",
                    "findings": ["기록된 식사를 보면 전반적으로 균형 잡힌 식단이에요"],
                },
            )
        ),
        reply("끝냈어요."),
    )

    result = await run(task, context, client=client)

    assert result.status == "ok"
    assert result.readouts == (READOUTS.render(NUTRIENT_INSUFFICIENT, recorded=0),)


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


def _verdict(bands: dict[str, str], *, records: int = 9) -> NutrientVerdict:
    return NutrientVerdict(
        bands=bands,  # type: ignore[arg-type]
        unscored=(),
        records=records,
        resolved_ratio=1.0,
        insufficient=False,
        food_groups=frozenset(),
    )


async def test_모델이_말로_끝내면_코드_문장으로_낸다(
    task, context, talking_client, caplog: pytest.LogCaptureFixture
) -> None:
    # 모델 실패를 "말할 게 없다" 로 보이게 두지 않는다(#320 PM 리뷰). 판정 · 안내는 코드 값이라
    # 문장 틀로 낸다(#357 리뷰) — 대신 authored_by="code" 와 경고 로그로 모델 실패를 센다
    caplog.set_level(logging.WARNING, logger="app.agents.food.agent")

    result = await run(task, context, client=talking_client)

    assert result.status == "ok"
    assert result.model_calls == 1
    assert result.readouts == (READOUTS.render(NUTRIENT_INSUFFICIENT, recorded=0),)  # 기록이 없다
    assert "food.nutrient.fallback run_id=run-1 ended_by=no_tool_call" in caplog.text
    assert "영양은 괜찮은지" not in caplog.text  # 원문은 남기지 않는다 — run_id 와 종료 사유만


async def test_상한까지_출력_tool_이_통과하지_못하면_코드_문장으로_낸다(
    task, context, looping_client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="app.agents.food.agent")

    result = await run(task, context, client=looping_client)

    assert result.status == "ok"
    assert result.readouts
    assert all(readout.authored_by == "code" for readout in result.readouts)
    assert "ended_by=max_steps" in caplog.text


async def test_이미_낸_판정이_있으면_그_판정으로_코드_문장을_낸다(
    task, context, talking_client
) -> None:
    # compare_diet_balance 까지 부른 뒤 모델이 끝내지 못한 경우 — run state 의 판정을 그대로 쓴다.
    # 다시 계산하면 이 context 는 기록이 없어 insufficient 가 나온다
    context.state.nutrient_verdict = _verdict({"protein": "low", "sodium": "high", "iron": "ok"})

    result = await run(task, context, client=talking_client)

    (readout,) = result.readouts
    assert readout.authored_by == "code"
    lines = readout.body.splitlines()
    assert lines[0].startswith("기록된 식사 9건을 보면 단백질 식품이 적은 편이에요 — ")
    assert lines[1] == "기록된 식사 9건을 보면 나트륨 많은 음식이 자주 보여요 — 국물·절임 줄이기"
    assert len(lines) == 2  # ok 인 항목은 말하지 않는다


async def test_판정한_항목이_없으면_빈_findings_뒤에_살펴보지_못했다고_낸다(task, context) -> None:
    # 모델 경로 — 구간이 하나도 없으면 findings 를 비우고, 화면에는 코드 문구가 나간다
    context.state.nutrient_verdict = _verdict({})
    client = FakeLLM(
        tool_response(
            call("c1", "report_nutrient_analysis", {"period": "last_7_days", "findings": []})
        )
    )

    result = await run(task, context, client=client)

    assert result.status == "ok"
    assert result.readouts == (READOUTS.render("nutrient.unscored", recorded=9),)


async def test_대체_문장의_안내에서도_알레르기_식품이_빠진다(task, talking_client) -> None:
    # 🚨 알레르기 식품 빼기는 코드가 한다 — 모델이 못 끝내서 코드 문장으로 가도 같다
    context = _context(
        TODDLER_BIRTH,
        consent=InMemoryConsent({CHILD: True}),
        safety=InMemorySafety([SafetyEntry(kind="allergy", label="달걀", status="active")]),
    )
    context.state.nutrient_verdict = _verdict({"protein": "low"})

    result = await run(task, context, client=talking_client)

    body = result.readouts[0].body
    assert "달걀" not in body and "계란" not in body
    assert "고기" in body  # 달걀만 빠지고 다른 식품 안내는 남는다
