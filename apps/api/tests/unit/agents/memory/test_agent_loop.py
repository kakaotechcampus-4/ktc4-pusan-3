"""agent 루프 검증. 가짜 LLM 응답으로 돌리므로 실제 API를 부르지 않는다.

Memory agent 의 루프만 본다. Supervisor 라우팅까지 묶어서 보는 테스트는
tests/unit/agents/supervisor 에 있다.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.llm_client import LLMResponse
from app.agents.memory.agent import run
from app.agents.memory.context import AgentContext
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore

KST = ZoneInfo("Asia/Seoul")
# 재현을 위해 임의로 설정한 값이므로 변경가능
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=KST)


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


class FakeLLM:
    """정해둔 응답을 순서대로 돌려준다. 마지막 응답을 다 쓰면 마무리 멘트를 낸다."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], **_: Any) -> LLMResponse:
        self.seen.append([dict(message) for message in messages])
        if self._queue:
            return self._queue.pop(0)
        return _reply("끝냈어요.")


def _call(call_id: str, name: str, arguments: dict[str, Any] | str) -> SimpleNamespace:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=raw))


def _tools(*calls: SimpleNamespace, usage: dict[str, int] | None = None) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=list(calls)),
        usage=usage or {"prompt_tokens": 100, "completion_tokens": 20},
        latency_ms=1,
    )


def _reply(content: str, usage: dict[str, int] | None = None) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=content, tool_calls=None),
        usage=usage or {"prompt_tokens": 100, "completion_tokens": 20},
        latency_ms=1,
    )


_APPLE = {"raw_text": "사과 먹었어", "observed_on": "오늘", "subject": "사과"}


# ── 기본 흐름 ───────────────────────────────────────────────────
async def test_tool_없이_바로_답하면_한_step_에_끝난다(context: AgentContext) -> None:
    result = await run("안녕", context, client=FakeLLM(_reply("무엇을 기록할까요?")))
    assert result.completed is True
    assert result.steps == 1
    assert result.final_message == "무엇을 기록할까요?"
    assert result.calls == []


async def test_한_응답의_tool_을_모두_실행한다(context: AgentContext) -> None:
    # 첫 개만 실행하면 복합 발화에서 나머지가 통째로 사라진다
    llm = FakeLLM(
        _tools(
            _call("a", "create_observation_food", _APPLE),
            _call(
                "b",
                "create_observation_activity",
                {
                    "raw_text": "블록놀이 했어",
                    "observed_on": "오늘",
                    "subject": "블록놀이",
                    "activity": "블록놀이",
                },
            ),
        ),
        _reply("두 건 기록했어요."),
    )
    result = await run("사과 먹고 블록놀이 했어", context, client=llm)

    assert result.tool_names == ["create_observation_food", "create_observation_activity"]
    assert all(call.success for call in result.calls)
    for domain in ("food", "activity"):
        rows = await context.store.query_observations(domain=domain, child_id=context.child_id)
        assert len(rows) == 1


async def test_새_일정과_준비물은_create_event_한_번으로_끝난다(context: AgentContext) -> None:
    # 초안에는 event id 가 없어 준비물을 뒤 step 으로 이어 붙일 수 없다
    llm = FakeLLM(
        _tools(
            _call(
                "a",
                "create_event",
                {
                    "title": "운동회",
                    "starts_on": "모레",
                    "starts_time": "오전 9시",
                    "items": ["체육복"],
                },
            )
        ),
        _reply("확인해 주세요."),
    )
    result = await run("모레 운동회, 체육복 챙겨야 해", context, client=llm)

    assert result.completed is True
    assert result.tool_names == ["create_event"]
    assert result.calls[-1].success is True
    drafts = context.drafts.all()
    assert len(drafts) == 1
    assert [item.item_name for item in drafts[0].items] == ["체육복"]
    assert await context.store.query_events(child_id=context.child_id) == []


# ── 깨진 arguments ──────────────────────────────────────────────
async def test_JSON_이_깨져도_루프가_죽지_않는다(context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(_call("a", "create_observation_food", '{"raw_text": "사과",')),  # 잘린 JSON
        _reply("다시 시도할게요."),
    )
    result = await run("사과 먹었어", context, client=llm)

    assert result.completed is True
    assert result.calls[0].success is False
    assert result.calls[0].result["error"]["code"] == ErrorCode.INVALID_ARGS


async def test_JSON_이_객체가_아니면_거절한다(context: AgentContext) -> None:
    llm = FakeLLM(_tools(_call("a", "create_observation_food", "[1, 2]")), _reply("끝"))
    result = await run("사과", context, client=llm)
    assert result.calls[0].result["error"]["code"] == ErrorCode.INVALID_ARGS


async def test_깨진_뒤에도_다음_tool_이_실행된다(context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(
            _call("a", "create_observation_food", "{{{"),
            _call("b", "create_observation_food", _APPLE),
        ),
        _reply("끝"),
    )
    result = await run("사과 먹었어", context, client=llm)
    assert [call.success for call in result.calls] == [False, True]


# ── 중복 쓰기 차단 ──────────────────────────────────────────────
async def test_빈_턴이_오면_한_번_다시_묻는다(context: AgentContext) -> None:
    """라이브 RC15·T24 — tool 도 안 부르고 답도 비운 턴이 왔다.

    그대로 두면 pipeline 이 "아무것도 못 했다" 로 보고 입력을 그대로 돌려준다.
    되물어야 하는 입력이었는데 사용자는 아무것도 못 본다.
    """
    llm = FakeLLM(_reply(""), _reply("몇 시에 시작하나요?"))
    result = await run("다음 주 수요일 병원 예약 있어", context, client=llm)

    assert result.final_message == "몇 시에 시작하나요?"
    assert len(llm.seen) == 2
    assert "답이 비어 있다" in llm.seen[1][-1]["content"]


async def test_다시_물어도_비면_그대로_끝낸다(context: AgentContext) -> None:
    # 한 번만 다시 묻는다. 계속 물으면 호출만 쌓인다
    llm = FakeLLM(_reply(""), _reply("   "))
    result = await run("다음 주 수요일 병원 예약 있어", context, client=llm)

    assert result.steps == 2
    assert not (result.final_message or "").strip()


async def test_같은_인자의_쓰기를_두_번_실행하지_않는다(context: AgentContext) -> None:
    # 프롬프트에도 적혀 있지만 무시된 전례가 있다. 저장 중복은 되돌리기 어렵다
    llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _APPLE)),
        _tools(_call("b", "create_observation_food", _APPLE)),
        _reply("끝"),
    )
    result = await run("사과 먹었어", context, client=llm)

    rows = await context.store.query_observations(domain="food", child_id=context.child_id)
    assert len(rows) == 1
    assert result.calls[1].success is False
    assert "이미 했다" in result.calls[1].result["error"]["message"]


async def test_같은_날_같은_대상이면_인자가_달라도_두_번_저장하지_않는다(
    context: AgentContext,
) -> None:
    """라이브 RC08 — 킥보드 관찰이 거의 같은 내용으로 두 행 저장됐다.

    인자 전체로 비교하면 raw_text 만 살짝 달라도 통과한다. 대상(subject)과 날짜로 센다.
    잃는 게 있으면 안 되니 error.message 가 update 로 고치라고 알려준다 (D4).
    """
    first = {**_APPLE, "raw_text": "오늘 사과 먹었어"}
    again = {**_APPLE, "raw_text": "사과를 반 개 먹었지", "amount": "반 개"}
    llm = FakeLLM(
        _tools(_call("a", "create_observation_food", first)),
        _tools(_call("b", "create_observation_food", again)),
        _reply("끝"),
    )
    result = await run("오늘 사과 먹었어", context, client=llm)

    rows = await context.store.query_observations(domain="food", child_id=context.child_id)
    assert len(rows) == 1
    assert result.calls[1].success is False
    assert "update" in result.calls[1].result["error"]["message"]


async def test_대상이_다르면_두_번_저장한다(context: AgentContext) -> None:
    # 한 발화에 관찰이 둘일 수 있다 (T17 — 모래놀이 + 떡볶이). 대상이 다르면 막지 않는다
    other = {**_APPLE, "raw_text": "바나나도 먹었어", "subject": "바나나"}
    llm = FakeLLM(
        _tools(
            _call("a", "create_observation_food", _APPLE),
            _call("b", "create_observation_food", other),
        ),
        _reply("끝"),
    )
    await run("사과랑 바나나 먹었어", context, client=llm)

    rows = await context.store.query_observations(domain="food", child_id=context.child_id)
    assert len(rows) == 2


async def test_키_순서가_달라도_같은_인자로_본다(context: AgentContext) -> None:
    reordered = {"subject": "사과", "observed_on": "오늘", "raw_text": "사과 먹었어"}
    llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _APPLE)),
        _tools(_call("b", "create_observation_food", reordered)),
        _reply("끝"),
    )
    await run("사과 먹었어", context, client=llm)
    rows = await context.store.query_observations(domain="food", child_id=context.child_id)
    assert len(rows) == 1


async def test_인자가_다르면_막지_않는다(context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(_call("a", "create_observation_food", _APPLE)),
        _tools(
            _call(
                "b",
                "create_observation_food",
                {"raw_text": "바나나 먹었어", "observed_on": "오늘", "subject": "바나나"},
            )
        ),
        _reply("끝"),
    )
    await run("사과랑 바나나 먹었어", context, client=llm)
    rows = await context.store.query_observations(domain="food", child_id=context.child_id)
    assert len(rows) == 2


async def test_실패한_호출은_다시_부를_수_있다(context: AgentContext) -> None:
    # 실패를 차단 대상에 넣으면 error.message 를 보고 고쳐 부르는 복구 경로가 막힌다
    bad = {"raw_text": "사과", "observed_on": "언젠가", "subject": "사과"}
    llm = FakeLLM(
        _tools(_call("a", "create_observation_food", bad)),
        _tools(_call("b", "create_observation_food", bad)),
        _reply("끝"),
    )
    result = await run("언젠가 사과 먹었어", context, client=llm)

    codes = [call.result["error"]["code"] for call in result.calls]
    assert codes == [ErrorCode.DATE_UNPARSEABLE, ErrorCode.DATE_UNPARSEABLE]


async def test_조회는_몇_번이든_막지_않는다(context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(_call("a", "query_observation_food", {})),
        _tools(_call("b", "query_observation_food", {})),
        _reply("끝"),
    )
    result = await run("사과 기록 보여줘", context, client=llm)
    assert [call.success for call in result.calls] == [True, True]


# ── 종료 조건 ───────────────────────────────────────────────────
async def test_MAX_STEPS_를_넘기면_미완료로_알린다(context: AgentContext) -> None:
    # 조용히 끝내면 호출자가 완료인지 중단인지 구분 못 한다
    llm = FakeLLM(*[_tools(_call(f"c{i}", "query_observation_food", {})) for i in range(5)])
    result = await run("계속", context, client=llm, max_steps=3)

    assert result.completed is False
    assert result.final_message is None
    assert result.steps == 3
    assert len(result.calls) == 3


async def test_없는_tool_이름도_루프를_끊지_않는다(context: AgentContext) -> None:
    llm = FakeLLM(_tools(_call("a", "create_diary", {})), _reply("그건 못 해요."))
    result = await run("일기 써줘", context, client=llm)

    assert result.completed is True
    assert result.calls[0].result["error"]["code"] == ErrorCode.TOOL_NOT_ALLOWED


# ── 대화 누적 ───────────────────────────────────────────────────
async def test_tool_결과가_tool_call_id_와_짝지어_쌓인다(context: AgentContext) -> None:
    llm = FakeLLM(_tools(_call("call-1", "create_observation_food", _APPLE)), _reply("끝"))
    await run("사과 먹었어", context, client=llm)

    second_request = llm.seen[1]
    assistant = second_request[2]
    tool_message = second_request[3]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call-1"
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call-1"
    assert json.loads(tool_message["content"])["success"] is True


async def test_system_과_user_가_먼저_쌓인다(context: AgentContext) -> None:
    llm = FakeLLM(_reply("끝"))
    await run("사과 먹었어", context, client=llm)

    first_request = llm.seen[0]
    assert first_request[0]["role"] == "system"
    assert first_request[1] == {"role": "user", "content": "사과 먹었어"}


# ── 사용량 ──────────────────────────────────────────────────────
async def test_usage_를_step_마다_합산한다(context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(_call("a", "query_observation_food", {}), usage={"prompt_tokens": 100}),
        _reply("끝", usage={"prompt_tokens": 150, "completion_tokens": 30}),
    )
    result = await run("보여줘", context, client=llm)

    assert result.usage["prompt_tokens"] == 250
    assert result.usage["completion_tokens"] == 30
