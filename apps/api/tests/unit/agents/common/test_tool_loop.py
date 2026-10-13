"""도메인 Agent 공통 tool calling 루프 (#319).

가짜 LLM 응답으로 돌린다. 루프가 지는 책임만 본다 — 출력 tool 에서 끝나기 · 깨진 인자 · 실행 순서 ·
끊기. 허용 목록과 인자 검증은 각 Agent registry 의 몫이라 여기서는 가짜 실행 함수를 쓴다.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.agents.common.evidence import RankedEvidence, top_refs, unseen
from app.agents.common.llm_client import LLMResponse
from app.agents.common.refs import Ref
from app.agents.common.tool_loop import MAX_STEPS, run_tool_loop
from app.agents.common.tool_runtime import ErrorCode, ToolResult, fail, ok

SEARCH = "search_memory"
PROPOSE = "propose_candidates"
USAGE = {"prompt_tokens": 100, "completion_tokens": 20}
# Agent 가 공통 ErrorCode 를 상속해 더한 코드 (Activity · Food 의 출력 검증 거절)
REJECTED = "CANDIDATE_REJECTED"


class FakeLLM:
    """정해 둔 응답을 순서대로 돌려준다. 다 쓴 뒤에 또 부르면 실패한다 — 더 부르면 안 되는
    자리다."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.seen: list[list[dict[str, Any]]] = []
        self.seen_kwargs: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.seen.append([dict(message) for message in messages])
        self.seen_kwargs.append(dict(kwargs))
        if not self._queue:
            raise AssertionError("응답을 다 썼는데 모델을 또 불렀다")
        return self._queue.pop(0)


class FakeTools:
    """registry.execute_tool 자리. 부른 순서와 인자를 남긴다."""

    def __init__(self, **handlers: Callable[[dict[str, Any]], ToolResult]) -> None:
        self._handlers = handlers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, arguments))
        return self._handlers[name](arguments)


def call(call_id: str, name: str, arguments: dict[str, Any] | str) -> SimpleNamespace:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=raw))


def tools(*calls: SimpleNamespace) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=list(calls)), usage=USAGE, latency_ms=1
    )


def reply(content: str) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=content, tool_calls=None), usage=USAGE, latency_ms=1
    )


def searched(arguments: dict[str, Any]) -> ToolResult:
    return ok("query", SEARCH, count=0)


def proposed(arguments: dict[str, Any]) -> ToolResult:
    return ok("propose", PROPOSE, count=3)


def rejected(arguments: dict[str, Any]) -> ToolResult:
    return fail("propose", PROPOSE, REJECTED, "1번 후보: 다른 활동으로 바꾼다.")


async def loop(llm: FakeLLM, execute: FakeTools, **kwargs: Any):
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "시스템"},
        {"role": "user", "content": "요청"},
    ]
    return await run_tool_loop(
        llm, messages, tools=[], execute=execute, output_tools={PROPOSE}, **kwargs
    )


def tool_messages(request: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message for message in request if message["role"] == "tool"]


class TestEnding:
    async def test_출력_tool_이_성공하면_그_스텝에서_끝나고_모델을_다시_부르지_않는다(self):
        llm = FakeLLM(tools(call("c1", SEARCH, {})), tools(call("c2", PROPOSE, {"n": 3})))
        execute = FakeTools(search_memory=searched, propose_candidates=proposed)

        result = await loop(llm, execute)

        assert (result.ended_by, result.completed, result.steps) == ("output_accepted", True, 2)
        assert len(llm.seen) == 2
        assert execute.calls == [(SEARCH, {}), (PROPOSE, {"n": 3})]

    async def test_출력_tool_이_실패하면_사유를_모델에게_돌려주고_계속한다(self):
        llm = FakeLLM(tools(call("c1", PROPOSE, {})), tools(call("c2", PROPOSE, {})))
        verdicts = iter([rejected({}), proposed({})])
        execute = FakeTools(propose_candidates=lambda arguments: next(verdicts))

        result = await loop(llm, execute)

        assert (result.ended_by, result.steps) == ("output_accepted", 2)
        (back,) = tool_messages(llm.seen[1])
        assert back["tool_call_id"] == "c1"
        assert json.loads(back["content"])["error"]["code"] == REJECTED

    async def test_출력이_아닌_tool_이_성공해도_끝나지_않는다(self):
        llm = FakeLLM(tools(call("c1", SEARCH, {})), tools(call("c2", PROPOSE, {})))
        execute = FakeTools(search_memory=searched, propose_candidates=proposed)

        result = await loop(llm, execute)

        assert result.steps == 2

    async def test_모델이_tool_없이_말하면_출력_없이_끝난다(self):
        """도메인 Agent 는 말로 끝내지 않는다. Agent 는 결과가 없는 것으로 본다."""
        result = await loop(FakeLLM(reply("추천을 준비했어요.")), FakeTools())

        assert (result.ended_by, result.completed, result.steps) == ("no_tool_call", False, 1)

    async def test_MAX_STEPS_에서_끊는다(self):
        llm = FakeLLM(*(tools(call(f"c{i}", SEARCH, {})) for i in range(MAX_STEPS)))
        execute = FakeTools(search_memory=searched)

        result = await loop(llm, execute)

        assert (result.ended_by, result.completed, result.steps) == ("max_steps", False, MAX_STEPS)
        assert len(llm.seen) == MAX_STEPS

    async def test_max_steps_를_넘겨_받으면_그만큼만_돈다(self):
        llm = FakeLLM(*(tools(call(f"c{i}", SEARCH, {})) for i in range(2)))

        result = await loop(llm, FakeTools(search_memory=searched), max_steps=2)

        assert (result.ended_by, result.steps) == ("max_steps", 2)


class TestCalls:
    async def test_한_응답에_여러_tool_이_오면_온_순서대로_전부_실행한다(self):
        """조회가 먼저 끝나야 같은 응답의 출력 tool 이 그 결과로 근거를 대조한다."""
        llm = FakeLLM(tools(call("c1", SEARCH, {"q": "블록"}), call("c2", PROPOSE, {})))
        execute = FakeTools(search_memory=searched, propose_candidates=proposed)

        result = await loop(llm, execute)

        assert [name for name, _ in execute.calls] == [SEARCH, PROPOSE]
        assert (result.ended_by, result.steps) == ("output_accepted", 1)
        assert [record.name for record in result.calls] == [SEARCH, PROPOSE]

    @pytest.mark.parametrize("raw", ["{깨진", "[1, 2]", '"문자열"'])
    async def test_인자가_JSON_객체가_아니면_실행하지_않고_INVALID_ARGS_로_돌려준다(self, raw):
        llm = FakeLLM(tools(call("c1", PROPOSE, raw)), tools(call("c2", PROPOSE, {})))
        execute = FakeTools(propose_candidates=proposed)

        result = await loop(llm, execute)

        assert execute.calls == [(PROPOSE, {})]  # 깨진 첫 호출은 실행하지 않았다
        (back,) = tool_messages(llm.seen[1])
        assert json.loads(back["content"])["error"]["code"] == ErrorCode.INVALID_ARGS
        assert result.calls[0].result.success is False
        assert result.ended_by == "output_accepted"

    async def test_인자가_비어_오면_빈_객체로_실행한다(self):
        empty = SimpleNamespace(id="c1", function=SimpleNamespace(name=PROPOSE, arguments=None))
        llm = FakeLLM(tools(empty))
        execute = FakeTools(propose_candidates=proposed)

        await loop(llm, execute)

        assert execute.calls == [(PROPOSE, {})]

    async def test_다음_요청에_모델이_부른_tool_과_결과를_짝지어_싣는다(self):
        """tool_call_id 가 짝을 잃으면 provider 가 다음 요청을 거절한다."""
        llm = FakeLLM(tools(call("c1", SEARCH, {"q": "블록"})), tools(call("c2", PROPOSE, {})))
        execute = FakeTools(search_memory=searched, propose_candidates=proposed)

        await loop(llm, execute)

        request = llm.seen[1]
        assistant = request[-2]
        assert assistant["role"] == "assistant"
        assert assistant["tool_calls"] == [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": SEARCH, "arguments": '{"q": "블록"}'},
            }
        ]
        assert request[-1]["tool_call_id"] == "c1"
        assert json.loads(request[-1]["content"])["success"] is True

    async def test_열린_tool_과_tool_choice_를_매번_그대로_보낸다(self):
        llm = FakeLLM(tools(call("c1", PROPOSE, {})))
        specs = [{"type": "function", "function": {"name": PROPOSE}}]
        messages: list[dict[str, Any]] = [{"role": "user", "content": "요청"}]

        await run_tool_loop(
            llm,
            messages,
            tools=specs,
            execute=FakeTools(propose_candidates=proposed),
            output_tools={PROPOSE},
            tool_choice="required",
            max_completion_tokens=900,
        )

        assert llm.seen_kwargs[0]["tools"] == specs
        assert llm.seen_kwargs[0]["tool_choice"] == "required"
        assert llm.seen_kwargs[0]["max_completion_tokens"] == 900

    async def test_usage_를_왕복마다_더한다(self):
        llm = FakeLLM(tools(call("c1", SEARCH, {})), tools(call("c2", PROPOSE, {})))

        result = await loop(llm, FakeTools(search_memory=searched, propose_candidates=proposed))

        assert result.usage == {"prompt_tokens": 200, "completion_tokens": 40}


class TestCancel:
    """pipeline 은 도메인 task 를 `asyncio.wait_for` 로 20초에 끊는다 (Agent_공통규약 §7).

    루프가 취소를 삼키고 값을 돌려주면 wait_for 가 그 값을 그대로 돌려줘서, 끊긴 Agent 의 늦은
    결과가 성공으로 들어간다.
    """

    async def test_모델을_기다리다_끊기면_결과를_내지_않고_시간_초과로_끝난다(self):
        class SlowLLM(FakeLLM):
            async def chat(self, messages, **kwargs):
                await asyncio.sleep(10)
                return await super().chat(messages, **kwargs)

        llm = SlowLLM(tools(call("c1", PROPOSE, {})))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(loop(llm, FakeTools(propose_candidates=proposed)), 0.01)

    async def test_tool_을_실행하다_끊기면_결과를_내지_않고_시간_초과로_끝난다(self):
        async def slow_search(name: str, arguments: dict[str, Any]) -> ToolResult:
            await asyncio.sleep(10)
            return searched(arguments)

        llm = FakeLLM(tools(call("c1", SEARCH, {})), reply("끝"))
        messages: list[dict[str, Any]] = [{"role": "user", "content": "요청"}]
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                run_tool_loop(llm, messages, tools=[], execute=slow_search, output_tools={PROPOSE}),
                0.01,
            )

    async def test_tool_이_낸_예외를_삼키지_않는다(self):
        """registry 는 미구현 핸들러의 NotImplementedError 를 그대로 올린다."""

        def broken(arguments: dict[str, Any]) -> ToolResult:
            raise NotImplementedError("DB 연결 후 구현")

        llm = FakeLLM(tools(call("c1", SEARCH, {})))
        with pytest.raises(NotImplementedError):
            await loop(llm, FakeTools(search_memory=broken))


def evidence(i: int) -> RankedEvidence:
    return RankedEvidence(
        ref=Ref(kind="observation_activity", id=UUID(int=i)),
        tier=3,
        label=f"관찰{i}",
        polarity=1,
        observed_on=date(2026, 10, 11),
    )


class TestEvidenceRequired:
    """조회하지 않은 id 를 인용한 후보는 근거만 빼지 않고 거절된다 — EVIDENCE_REQUIRED
    (#195 · Tool_공통.md §5-3).

    출력 tool 은 Agent 마다 다르다. 여기서는 공통 대조(`unseen`)를 그대로 쓰는 출력 tool 로
    루프 위에서 거절 → 고쳐서 통과까지 본다.
    """

    async def test_조회하지_않은_id_를_인용하면_거절되고_고쳐_내면_끝난다(self):
        seen: dict[UUID, RankedEvidence] = {}
        accepted: list[list[str]] = []

        def search(arguments: dict[str, Any]) -> ToolResult:
            seen.update(top_refs([evidence(1), evidence(2)]))
            return ok("query", SEARCH, ids=[str(key) for key in seen])

        def propose(arguments: dict[str, Any]) -> ToolResult:
            if unseen(arguments["evidence"], seen):
                return fail(
                    "propose",
                    PROPOSE,
                    ErrorCode.EVIDENCE_REQUIRED,
                    "evidence 에는 조회 결과에 있던 id 만 쓴다.",
                )
            accepted.append(arguments["evidence"])
            return ok("propose", PROPOSE, count=1)

        made_up = str(UUID(int=99))
        llm = FakeLLM(
            tools(call("c1", SEARCH, {})),
            tools(call("c2", PROPOSE, {"evidence": [str(UUID(int=1)), made_up]})),
            tools(call("c3", PROPOSE, {"evidence": [str(UUID(int=1))]})),
        )

        result = await loop(llm, FakeTools(search_memory=search, propose_candidates=propose))

        back = tool_messages(llm.seen[2])[-1]
        assert json.loads(back["content"])["error"]["code"] == ErrorCode.EVIDENCE_REQUIRED
        assert accepted == [[str(UUID(int=1))]]  # 지어낸 id 를 뺀 채로 통과시키지 않았다
        assert result.ended_by == "output_accepted"
