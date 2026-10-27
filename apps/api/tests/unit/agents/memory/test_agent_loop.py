"""agent 루프 검증. 가짜 LLM 응답으로 돌리므로 실제 API를 부르지 않는다.

Memory agent 의 루프만 본다. Supervisor 라우팅까지 묶어서 보는 테스트는
tests/unit/agents/supervisor 에 있다.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.datetime_rules import DateRange
from app.agents.common.llm_client import LLMResponse
from app.agents.memory.agent import _FOUND_HEADER, MAX_FOUND, MAX_QUESTIONS, run
from app.agents.memory.context import AgentContext
from app.agents.memory.result import ErrorCode
from app.agents.memory.schemas.task import (
    FoundRef,
    MemoryHint,
    MemoryTask,
    PendingMemoryContext,
    WorkType,
)
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
        self.seen_tools: list[list[dict[str, Any]]] = []  # 매 호출에 열린 tool 스펙
        self.seen_kwargs: list[dict[str, Any]] = []  # messages 밖의 인자 (tools · response_format)

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.seen.append([dict(message) for message in messages])
        self.seen_tools.append(list(kwargs.get("tools") or []))
        self.seen_kwargs.append(dict(kwargs))
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


def _answer(reply: dict[str, Any]) -> LLMResponse:
    """모델의 마지막 말. structured output 이라 content 가 스키마 JSON 이다."""
    return _reply(json.dumps({"pending_hint": None, **reply}, ensure_ascii=False))


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


async def test_문장이_빈_JSON_도_빈_턴으로_보고_다시_묻는다(context: AgentContext) -> None:
    """라이브 RC26 — structured output 이라 빈 턴이 빈 content 가 아니라 text 가 빈 JSON 으로 온다.

    스키마에는 맞아서 그대로 끝나면 pipeline 이 메모 없음으로 보고 입력을 그대로 돌려준다.
    """
    llm = FakeLLM(
        _answer({"text": "", "kind": "message"}),
        _answer({"text": "급식 기록을 찾지 못했어요.", "kind": "message"}),
    )
    result = await run("오늘 급식 뭐야?", context, client=llm)

    assert result.final_message == "급식 기록을 찾지 못했어요."
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


# ── 마지막 말 (structured output) ───────────────────────────────
_ASK_WHEN = {"text": "기침은 언제부터였어요?", "kind": "question"}


async def test_요청에_strict_응답_형식이_실린다(context: AgentContext) -> None:
    llm = FakeLLM(_answer(_ASK_WHEN))

    await run("요즘 기침해", context, client=llm)

    response_format = llm.seen_kwargs[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert llm.seen_kwargs[0]["tools"]  # tool 과 같이 보낸다


async def test_질문으로_답하면_question_으로_표시된다(context: AgentContext) -> None:
    llm = FakeLLM(_answer(_ASK_WHEN))

    result = await run("요즘 기침해", context, client=llm)

    assert result.reply is not None
    assert result.reply.kind == "question"
    assert result.reply.text == "기침은 언제부터였어요?"
    assert result.completed is True
    assert result.steps == 1


async def test_저장한_뒤_다음_턴에_되묻는다(context: AgentContext) -> None:
    # 말은 tool 호출이 없는 턴에만 나온다. 모델은 저장 결과를 본 뒤에 말한다
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_food", _APPLE)),
        _answer(_ASK_WHEN),
    )

    result = await run("사과 먹었어. 요즘 기침해", context, client=llm)

    assert [call.name for call in result.calls] == ["create_observation_food"]
    assert result.calls[0].success is True
    assert result.reply is not None and result.reply.kind == "question"
    assert result.steps == 2


async def test_문장으로만_끝나면_일반_메시지로_본다(context: AgentContext) -> None:
    # provider 가 형식을 무시하고 평문을 낸 경우. kind 를 모르니 message 로 둔다
    result = await run("알림 오나요", context, client=FakeLLM(_reply("일정 알림은 자동으로 가요.")))

    assert result.reply is not None
    assert result.reply.kind == "message"
    assert result.final_message == "일정 알림은 자동으로 가요."


async def test_스키마와_맞지_않는_답은_message_로_둔다(context: AgentContext) -> None:
    llm = FakeLLM(_reply('{"text": "몇 시예요?", "kind": "ask", "pending_hint": null}'))

    result = await run("모레 운동회가 있어", context, client=llm)

    assert result.reply is not None and result.reply.kind == "message"
    assert result.steps == 1  # 다시 묻지 않는다


# ── pending ─────────────────────────────────────────────────────
def _task(*hints: tuple[str, str], raw: str = "") -> MemoryTask:
    return MemoryTask(
        raw_text=raw or " ".join(text for text, _ in hints),
        hints=tuple(MemoryHint(text=text, work=WorkType(work)) for text, work in hints),
    )


async def test_질문이면_미완료_조각만_pending_에_담는다(context: AgentContext) -> None:
    task = _task(("사과 먹었어", "observe"), ("요즘 기침해", "observe"))
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_food", _APPLE),
        ),
        _answer({**_ASK_WHEN, "pending_hint": "요즘 기침해"}),
    )

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.pending is not None
    assert result.pending.hint_text == "요즘 기침해"  # 원문 전체가 아니다
    assert result.pending.question == "기침은 언제부터였어요?"
    assert result.pending.work is WorkType.OBSERVE
    assert result.pending.transcript == ()


async def test_일반_메시지에는_pending_이_없다(context: AgentContext) -> None:
    task = _task(("알림 오나요", "observe"))
    llm = FakeLLM(_answer({"text": "자동으로 가요.", "kind": "message"}))

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.pending is None


async def test_조각이_하나면_pending_hint_를_안_줘도_된다(context: AgentContext) -> None:
    task = _task(("요즘 기침해", "observe"))
    llm = FakeLLM(_answer(_ASK_WHEN))

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.pending is not None and result.pending.hint_text == "요즘 기침해"


async def test_없는_조각을_지목하면_한_번_다시_묻는다(context: AgentContext) -> None:
    task = _task(("사과 먹었어", "observe"), ("요즘 기침해", "observe"))
    llm = FakeLLM(
        _answer({**_ASK_WHEN, "pending_hint": "머리 아프대"}),
        _answer({**_ASK_WHEN, "pending_hint": "요즘 기침해"}),
    )

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.steps == 2
    assert result.pending is not None and result.pending.hint_text == "요즘 기침해"


async def test_두_번_틀리면_질문만_내보낸다(context: AgentContext) -> None:
    # 여기서 멈추지 않는다. 질문은 보여주고 이어 적기만 막힌다 (서버가 400 으로 안내)
    task = _task(("사과 먹었어", "observe"), ("요즘 기침해", "observe"))
    bad = {**_ASK_WHEN, "pending_hint": "머리 아프대"}
    llm = FakeLLM(_answer(bad), _answer(bad))

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.reply is not None and result.reply.kind == "question"
    assert result.pending is None


async def test_힌트가_없으면_원문_전체를_pending_으로_쓴다(context: AgentContext) -> None:
    # Supervisor 강등 경로 — 조각이 없다. 원문이 한 조각인 셈이라 그대로 담는다
    llm = FakeLLM(_answer(_ASK_WHEN))

    result = await run("요즘 기침해", context, client=llm, task=MemoryTask(raw_text="요즘 기침해"))

    assert result.pending is not None and result.pending.hint_text == "요즘 기침해"
    assert result.pending.whole is True


async def test_나눈_조각의_pending_은_원문_전체가_아니다(context: AgentContext) -> None:
    task = _task(("사과 먹었어", "observe"), ("요즘 기침해", "observe"))
    llm = FakeLLM(_answer({**_ASK_WHEN, "pending_hint": "요즘 기침해"}))

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.pending is not None and result.pending.whole is False


# ── continuation ────────────────────────────────────────────────
_COUGH_PENDING = PendingMemoryContext(
    hint_text="요즘 기침해", question="기침은 언제부터였어요?", work=WorkType.OBSERVE
)
_COUGH = {"raw_text": "3일 전부터 기침해", "observed_on": "오늘", "symptom": ["기침"]}


def _cont_answer(reply: dict[str, Any]) -> LLMResponse:
    """이어받기 run 의 마지막 말. 일반 스키마에 leftover 가 더 붙는다."""
    return _answer({"leftover": False, **reply})


async def test_이어받기는_조각과_질문과_답을_같이_넣는다(context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_health", _COUGH),
        ),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message"}),
    )

    result = await run("3일 전부터", context, client=llm, continuation=_COUGH_PENDING)

    sent = llm.seen[0][-1]["content"]
    assert "요즘 기침해" in sent
    assert "기침은 언제부터였어요?" in sent
    assert "3일 전부터" in sent
    rows = await context.store.query_observations(domain="health", child_id=context.child_id)
    assert len(rows) == 1
    assert result.pending is None


async def test_이어받기에서_또_물으면_pending_이_갱신된다(context: AgentContext) -> None:
    ask = {"text": "정확히 며칠 전인지 기억나세요?", "kind": "question"}
    llm = FakeLLM(_cont_answer(ask))

    result = await run("며칠 됐어요", context, client=llm, continuation=_COUGH_PENDING)

    assert result.pending is not None
    assert result.pending.hint_text == "요즘 기침해"  # 조각은 그대로
    assert result.pending.question == "정확히 며칠 전인지 기억나세요?"
    assert result.pending.transcript == ("기침은 언제부터였어요?", "며칠 됐어요")


async def test_두_번째_이어받기는_앞서_주고받은_것도_넣는다(context: AgentContext) -> None:
    again = PendingMemoryContext(
        hint_text="요즘 기침해",
        question="정확히 며칠 전인지 기억나세요?",
        work=WorkType.OBSERVE,
        transcript=("기침은 언제부터였어요?", "며칠 됐어요"),
    )
    llm = FakeLLM(_cont_answer({"text": "기록해 둘게요.", "kind": "message"}))

    await run("3일 전부터요", context, client=llm, continuation=again)

    sent = llm.seen[0][-1]["content"]
    assert "며칠 됐어요" in sent


def _asked_so_far(times: int) -> PendingMemoryContext:
    """기침 조각에 이미 times 번 물은 맥락. 마지막 질문이 question 이고 앞의 것은 transcript 다."""
    rounds = tuple(text for n in range(1, times) for text in (f"{n}번째 질문", f"{n}번째 답"))
    return PendingMemoryContext(
        hint_text="요즘 기침해",
        question=f"{times}번째 질문",
        work=WorkType.OBSERVE,
        transcript=rounds,
    )


async def test_재질문_상한_직전에는_또_물으면_맥락을_만든다(context: AgentContext) -> None:
    llm = FakeLLM(_cont_answer({"text": "정확히 며칠 전이에요?", "kind": "question"}))
    before = _asked_so_far(MAX_QUESTIONS - 1)

    result = await run("잘 모르겠어요", context, client=llm, continuation=before)

    assert result.pending is not None
    assert len(result.pending.transcript) == 2 * (MAX_QUESTIONS - 1)
    assert result.ask_limit_reached is False


async def test_재질문_상한에_닿으면_맥락을_만들지_않는다(context: AgentContext) -> None:
    llm = FakeLLM(_cont_answer({"text": "정확히 며칠 전이에요?", "kind": "question"}))

    result = await run(
        "잘 모르겠어요", context, client=llm, continuation=_asked_so_far(MAX_QUESTIONS)
    )

    assert result.pending is None
    assert result.ask_limit_reached is True


async def test_상한이어도_이번에_저장했으면_상한으로_세지_않는다(context: AgentContext) -> None:
    # 저장한 조각은 원래 맥락을 만들지 않는다. 여기서 상한 문구가 나가면 방금 저장한 것을
    # "저장하지 않았어요" 라고 말하게 된다
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_health", _COUGH)),
        _cont_answer({"text": "열도 있었어요?", "kind": "question"}),
    )

    result = await run("3일 전부터", context, client=llm, continuation=_asked_so_far(MAX_QUESTIONS))

    assert result.pending is None
    assert result.ask_limit_reached is False


async def test_상한이어도_묻지_않고_끝나면_상한으로_세지_않는다(context: AgentContext) -> None:
    # tool 없이 말로 끝내면 한 번 다시 확인한다. 그래도 같은 말로 끝낸 경우다
    said = {"text": "알겠어요. 생각나면 알려 주세요.", "kind": "message"}
    llm = FakeLLM(_cont_answer(said), _cont_answer(said))

    result = await run(
        "잘 모르겠어요", context, client=llm, continuation=_asked_so_far(MAX_QUESTIONS)
    )

    assert result.pending is None
    assert result.ask_limit_reached is False


_WHOLE_PENDING = PendingMemoryContext(
    hint_text="모래놀이하고 뭐 좀 먹었어",
    question="무엇을 먹었어요?",
    work=WorkType.OBSERVE,
    whole=True,
)


async def test_원문_전체_조각을_이어받으면_조각_안의_기록을_모두_저장하라고_알린다(
    context: AgentContext,
) -> None:
    # 앞 run 이 무엇이든 저장했으면 원문 전체를 맥락으로 남기지 않는다. 그래서 조각 안의 기록은
    # 아직 하나도 저장되지 않았다. 알려 주지 않으면 물은 음식만 저장하고 모래놀이를 빠뜨린다
    llm = FakeLLM(_cont_answer({"text": "기록해 둘게요.", "kind": "message"}))

    await run("떡볶이", context, client=llm, continuation=_WHOLE_PENDING)

    sent = llm.seen[0][-1]["content"]
    assert "나누지 못한 원문 전체" in sent


async def test_나눈_조각을_이어받을_때는_원문_전체라고_알리지_않는다(context: AgentContext) -> None:
    # 헤더 전체에 "조각 안의 기록을 전부 저장한다" 를 넣으면 섞인 말까지 저장했다 (C02)
    llm = FakeLLM(_cont_answer({"text": "기록해 둘게요.", "kind": "message"}))

    await run("3일 전부터", context, client=llm, continuation=_COUGH_PENDING)

    sent = llm.seen[0][-1]["content"]
    assert "나누지 못한 원문 전체" not in sent


async def test_원문_전체_조각에서_또_물으면_표시가_이어진다(context: AgentContext) -> None:
    llm = FakeLLM(_cont_answer({"text": "어떤 음식이었어요?", "kind": "question"}))

    result = await run("뭐였더라", context, client=llm, continuation=_WHOLE_PENDING)

    assert result.pending is not None and result.pending.whole is True


async def test_이어받기는_기록_묶음만_연다(context: AgentContext) -> None:
    llm = FakeLLM(_cont_answer({"text": "기록해 둘게요.", "kind": "message"}))

    await run("3일 전부터", context, client=llm, continuation=_COUGH_PENDING)

    names = {spec["function"]["name"] for spec in llm.seen_tools[0]}
    assert "create_observation_health" in names
    assert "delete_observation_health" not in names


async def test_task_와_continuation_을_같이_주면_거부한다(context: AgentContext) -> None:
    with pytest.raises(ValueError):
        await run(
            "3일 전부터",
            context,
            client=FakeLLM(),
            task=MemoryTask(raw_text="3일 전부터"),
            continuation=_COUGH_PENDING,
        )


async def test_이어받기_요청에는_leftover_가_든_응답_형식이_실린다(context: AgentContext) -> None:
    llm = FakeLLM(_cont_answer({"text": "기록해 둘게요.", "kind": "message"}))

    await run("3일 전부터", context, client=llm, continuation=_COUGH_PENDING)

    schema = llm.seen_kwargs[0]["response_format"]["json_schema"]["schema"]
    assert "leftover" in schema["required"]


async def test_일반_요청의_응답_형식에는_leftover_가_없다(context: AgentContext) -> None:
    llm = FakeLLM(_answer(_ASK_WHEN))

    await run("요즘 기침해", context, client=llm)

    schema = llm.seen_kwargs[0]["response_format"]["json_schema"]["schema"]
    assert "leftover" not in schema["properties"]


async def test_이어받기는_저장한_뒤에도_말하는_턴까지_간다(context: AgentContext) -> None:
    # 조기 종료하면 저장 한 번에 끝나서 leftover 를 받을 턴이 없다
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_health", _COUGH)),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message"}),
    )

    result = await run("3일 전부터", context, client=llm, continuation=_COUGH_PENDING)

    assert result.ended_by == "model"
    assert result.steps == 2
    assert result.reply is not None and result.reply.text == "기록해 둘게요."
    assert result.leftover is False


async def test_이어받기에서_저장_없이_말로_끝내면_한_번_다시_확인하게_한다(
    context: AgentContext,
) -> None:
    # 라이브에서 날짜를 모른다는 답에 tool 없이 "모르시는 것으로 기록할게요." 로 끝낸 적이 있다.
    # 말하는 턴에서 run 이 끝나 그 말은 지켜지지 않는다
    llm = FakeLLM(
        _cont_answer({"text": "모르시는 것으로 기록할게요.", "kind": "message"}),
        _cont_answer({"text": "기침은 대략 언제부터였어요?", "kind": "question"}),
    )

    result = await run("잘 모르겠어요", context, client=llm, continuation=_COUGH_PENDING)

    assert len(llm.seen) == 2
    assert "아무것도 저장하지 않았고" in llm.seen[1][-1]["content"]
    assert result.reply is not None and result.reply.kind == "question"
    assert result.pending is not None


async def test_저장_tool_이_실패한_뒤_말로_끝내도_다시_확인하게_한다(context: AgentContext) -> None:
    # "날짜 없이 저장할까요?" 에 "응" 이라고 하면 날짜를 못 읽어 저장이 실패한다.
    # 실패한 쓰기는 저장이 아니라서, 그 뒤에 "기록했어요" 라고 해도 지켜지지 않는다
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_health", {**_COUGH, "observed_on": "모름"})),
        _cont_answer({"text": "기록했어요.", "kind": "message"}),
        _cont_answer({"text": "기침은 언제부터였어요?", "kind": "question"}),
    )

    result = await run("응", context, client=llm, continuation=_COUGH_PENDING)

    assert result.calls[0].success is False
    assert len(llm.seen) == 3
    assert "아무것도 저장하지 않았고" in llm.seen[2][-1]["content"]
    assert result.reply is not None and result.reply.kind == "question"


async def test_다시_확인해도_말로_끝내면_그_말로_끝낸다(context: AgentContext) -> None:
    # 한 번만 확인한다. 못 했다고 사실대로 한 말은 그대로 나간다
    said = {"text": "날짜를 몰라 기록하지 못했어요.", "kind": "message"}
    llm = FakeLLM(_cont_answer(said), _cont_answer(said))

    result = await run("잘 모르겠어요", context, client=llm, continuation=_COUGH_PENDING)

    assert result.steps == 2
    assert result.final_message == "날짜를 몰라 기록하지 못했어요."


async def test_처음_run_은_tool_없이_말로_끝나도_다시_확인하지_않는다(
    context: AgentContext,
) -> None:
    # 알림 안내 · 조회 답처럼 tool 없이 말로 끝나는 것이 정상인 run 이다
    llm = FakeLLM(_answer({"text": "자동으로 가요.", "kind": "message"}))

    await run("알림 오나요", context, client=llm)

    assert len(llm.seen) == 1


async def test_조회_수정_이어받기는_tool_없이_말로_끝나도_다시_확인하지_않는다(
    context: AgentContext,
) -> None:
    # 지울지 물었는데 그냥 두라고 하면 tool 없이 말로 끝나는 것이 맞다
    asked = PendingMemoryContext(
        hint_text="어제 사과 먹은 기록 지워줘",
        question="어제 사과 먹은 기록을 삭제할까요?",
        work=WorkType.LOOKUP_EDIT,
    )
    llm = FakeLLM(_cont_answer({"text": "알겠어요. 그대로 둘게요.", "kind": "message"}))

    await run("아니, 그냥 둬", context, client=llm, continuation=asked)

    assert len(llm.seen) == 1


async def test_답에_섞인_말을_남겼으면_leftover_로_알린다(context: AgentContext) -> None:
    answer = "3일 전부터. 그리고 오늘 수영장 다녀왔어"
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_health", _COUGH)),
        _cont_answer({"text": "기록해 둘게요.", "kind": "message", "leftover": True}),
    )

    result = await run(answer, context, client=llm, continuation=_COUGH_PENDING)

    assert result.leftover is True
    assert result.reply is not None and result.reply.kind == "message"


async def test_이어받기에서_저장한_뒤_또_물으면_pending_을_남기지_않는다(
    context: AgentContext,
) -> None:
    # 저장한 조각을 pending 으로 남기면 다음 답에서 그 조각이 한 번 더 저장된다
    answer = "그저께부터. 내일 소풍 있어"
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_health", _COUGH)),
        _cont_answer({"text": "내일 소풍은 몇 시에 시작하나요?", "kind": "question"}),
    )

    result = await run(answer, context, client=llm, continuation=_COUGH_PENDING)

    assert result.reply is not None and result.reply.kind == "question"
    assert result.pending is None


# ── 리뷰 반영: 결과를 보기 전에 말하지 않는다 ─────────────────────
async def test_쓰기가_실패하면_결과를_보고_고친_뒤에_답한다(context: AgentContext) -> None:
    # tool 방식에서는 쓰기와 "기록했어요" 가 한 응답에 와서 실패해도 그대로 나갔다
    broken = {"raw_text": "사과 먹었어", "observed_on": "오늘"}  # subject 누락
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_food", broken)),
        _tools(_call("c2", "create_observation_food", _APPLE)),
        _answer({"text": "사과 먹은 것 기록했어요.", "kind": "message"}),
    )

    result = await run("사과 먹었어", context, client=llm)

    assert result.steps == 3
    rows = await context.store.query_observations(domain="food", child_id=context.child_id)
    assert len(rows) == 1


async def test_조회_결과를_본_뒤에_답한다(context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_food", {})),
        _answer({"text": "사과 기록이 한 건 있어요.", "kind": "message"}),
    )

    result = await run("뭐 먹었는지 보여줘", context, client=llm)

    assert result.steps == 2
    assert any(message["role"] == "tool" for message in llm.seen[1])
    assert result.reply is not None and result.reply.text == "사과 기록이 한 건 있어요."


# ── 리뷰 반영: 이미 저장한 조각은 pending 이 아니다 ──────────────
async def test_강등_경로에서_이미_저장했으면_원문을_pending_으로_쓰지_않는다(
    context: AgentContext,
) -> None:
    # 원문 전체를 pending 으로 두면 이어받기에서 사과가 한 번 더 저장된다
    raw = "사과 먹었어. 요즘 기침해"
    llm = FakeLLM(_tools(_call("c1", "create_observation_food", _APPLE)), _answer(_ASK_WHEN))

    result = await run(raw, context, client=llm, task=MemoryTask(raw_text=raw))

    assert result.reply is not None and result.reply.kind == "question"
    assert result.pending is None


async def test_강등_경로에서_일정_초안을_만들었으면_원문을_pending_으로_쓰지_않는다(
    context: AgentContext,
) -> None:
    # 원문 전체 맥락은 "조각 안의 기록은 하나도 저장하지 않았다" 로 넘어간다.
    # 관찰만 세면 이어받기가 같은 일정 초안을 한 번 더 만든다
    raw = "모레 운동회 있어. 요즘 기침해"
    event = {"title": "운동회", "starts_on": "모레", "starts_time": "오전 9시"}
    llm = FakeLLM(_tools(_call("c1", "create_event", event)), _answer(_ASK_WHEN))

    result = await run(raw, context, client=llm, task=MemoryTask(raw_text=raw))

    assert result.calls[0].success is True
    assert result.reply is not None and result.reply.kind == "question"
    assert result.pending is None


async def test_하나뿐인_조각이_이미_저장됐으면_pending_이_없다(context: AgentContext) -> None:
    # Supervisor 가 "요즘 기침해" 를 빠뜨려 후보가 저장한 조각 하나뿐인 경우.
    # 조회를 같이 불러 조기 종료를 피한다 — 저장만 하면 coverage 로 끝나 말할 턴이 없다
    task = _task(("사과 먹었어", "observe"), raw="사과 먹었어. 요즘 기침해")
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_food", _APPLE),
            _call("q1", "query_observation_health", {}),
        ),
        _answer(_ASK_WHEN),
    )

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.steps == 2  # 고를 다른 후보가 없으니 다시 묻지 않는다
    assert result.reply is not None and result.reply.kind == "question"
    assert result.pending is None


async def test_이미_저장한_조각을_지목하면_다시_고르게_한다(context: AgentContext) -> None:
    task = _task(("사과 먹었어", "observe"), ("요즘 기침해", "observe"))
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_food", _APPLE),
        ),
        _answer({**_ASK_WHEN, "pending_hint": "사과 먹었어"}),
        _answer({**_ASK_WHEN, "pending_hint": "요즘 기침해"}),
    )

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.steps == 3  # 답 → 다시 고르라는 요청 → 답
    assert result.pending is not None and result.pending.hint_text == "요즘 기침해"


# ── unclear (#484) — Supervisor 가 아이 기록이 아니라고 본 조각 ─────────
_STRAWBERRY = {"raw_text": "딸기 맛있다", "observed_on": "오늘", "subject": "딸기", "polarity": 1}
_BLOCK = {
    "raw_text": "오늘 블록놀이 했어",
    "observed_on": "오늘",
    "subject": "블록",
    "activity": "블록놀이",
}
_UNCLEAR_ONLY = MemoryTask(raw_text="딸기 맛있다.", unclear=("딸기 맛있다",), unclear_only=True)
_UNCLEAR_PENDING = PendingMemoryContext(
    "딸기 맛있다.", "아이가 딸기를 맛있다고 했나요?", WorkType.OBSERVE, whole=True, unclear=True
)


async def _foods(context: AgentContext) -> list[Any]:
    return await context.store.query_observations(domain="food", child_id=context.child_id)


async def test_unclear_조각만_있으면_관찰_저장을_거절하고_이유를_돌려준다(
    context: AgentContext,
) -> None:
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_food", _STRAWBERRY)),
        _answer({"text": "아이 기록인지 알 수 없어 저장하지 않았어요.", "kind": "message"}),
    )

    result = await run(_UNCLEAR_ONLY.raw_text, context, client=llm, task=_UNCLEAR_ONLY)

    assert result.calls[0].success is False
    assert result.calls[0].result["error"]["code"] == ErrorCode.NOT_CHILD_RECORD
    # tool 을 빼면 이유를 몰라 "기록했어요" 라고 답했다 — 거절 메시지에 다음 행동을 적는다.
    # 묻지 않는다: 물으면 "아니" 라는 답에도 저장한 적이 있다
    assert "묻지 않고" in result.calls[0].result["error"]["message"]
    assert result.pending is None
    assert await _foods(context) == []
    names = {spec["function"]["name"] for spec in llm.seen_tools[0]}
    assert "create_observation_food" in names  # 묶음은 그대로라 캐시도 그대로


async def test_다시_확인해도_물으면_unclear_맥락을_남긴다(context: AgentContext) -> None:
    # 안전망 — 다시 확인한 뒤에도 물으면 이어받기가 "아니" 를 받을 수 있게 맥락을 남긴다
    ask = _answer({"text": "아이가 딸기를 맛있다고 했나요?", "kind": "question"})
    llm = FakeLLM(ask, ask)

    result = await run(_UNCLEAR_ONLY.raw_text, context, client=llm, task=_UNCLEAR_ONLY)

    assert result.pending is not None
    assert result.pending.hint_text == "딸기 맛있다"  # 원문이 아니라 unclear 조각
    assert result.pending.unclear is True


async def test_기록과_섞이면_unclear_조각만_거절하고_기록은_저장한다(context: AgentContext) -> None:
    task = MemoryTask(
        raw_text="딸기 맛있다. 오늘 블록놀이 했어.",
        hints=(MemoryHint(text="오늘 블록놀이 했어", work=WorkType.OBSERVE),),
        unclear=("딸기 맛있다",),
    )
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_food", _STRAWBERRY),
            _call("c2", "create_observation_activity", _BLOCK),
        ),
        _answer({"text": "블록놀이를 기록했어요.", "kind": "message"}),
    )

    result = await run(task.raw_text, context, client=llm, task=task)

    assert [call.success for call in result.calls] == [False, True]
    assert "묻지 않고" in result.calls[0].result["error"]["message"]
    assert await _foods(context) == []


async def test_원문을_길게_적어도_기록_조각과_겹치면_막지_않는다(context: AgentContext) -> None:
    task = MemoryTask(
        raw_text="딸기 맛있다. 오늘 블록놀이 했어.",
        hints=(MemoryHint(text="오늘 블록놀이 했어", work=WorkType.OBSERVE),),
        unclear=("딸기 맛있다",),
    )
    whole = {**_BLOCK, "raw_text": "딸기 맛있다. 오늘 블록놀이 했어."}
    llm = FakeLLM(_tools(_call("c1", "create_observation_activity", whole)))

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.calls[0].success is True


async def test_unclear_맥락을_이어받으면_저장할_수_있다(context: AgentContext) -> None:
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_food", _STRAWBERRY)),
        _answer({"text": "딸기를 기록했어요.", "kind": "message", "leftover": False}),
    )

    result = await run("응 애가 그랬어", context, client=llm, continuation=_UNCLEAR_PENDING)

    assert result.calls[0].success is True
    assert len(await _foods(context)) == 1


async def test_unclear_맥락의_이어받기는_아니라는_답을_받을_수_있게_안내한다(
    context: AgentContext,
) -> None:
    no = {"text": "아이 기록이 아니라서 남기지 않았어요.", "kind": "message", "leftover": False}
    llm = FakeLLM(_answer(no))

    result = await run("아니", context, client=llm, continuation=_UNCLEAR_PENDING)

    user = llm.seen[0][1]["content"]
    assert "아이 얘기라고 답했을 때만" in user
    assert "나누지 못한 원문 전체다" not in user
    # 저장 없이 끝난 게 정상이라 재확인을 부르지 않는다. 재확인이 같은 질문을 다시 부를 수 있다
    assert len(llm.seen) == 1
    assert result.pending is None


# ── 고쳐 말하기 (#483) — lookup_edit 조각은 찾아보고 나서 만든다 ─────────
_PIZZA = {"raw_text": "떡볶이말고 피자 먹었대", "observed_on": "오늘", "subject": "피자"}
_MIXED = MemoryTask(
    raw_text="딸기 맛있다. 오늘 블록놀이 했어.",
    hints=(MemoryHint(text="오늘 블록놀이 했어", work=WorkType.OBSERVE),),
    unclear=("딸기 맛있다",),
)


@pytest.mark.parametrize(
    "raw_text",
    ["딸기 맛있다. 오늘 블록놀이 했어.", "딸기가 맛있다"],
    ids=["원문_전체", "바꿔_적음"],
)
async def test_섞인_입력에서_감상_대상이면_원문을_어떻게_적어도_거절한다(
    context: AgentContext, raw_text: str
) -> None:
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_food", {**_STRAWBERRY, "raw_text": raw_text}))
    )

    result = await run(_MIXED.raw_text, context, client=llm, task=_MIXED)

    assert result.calls[0].result["error"]["code"] == ErrorCode.NOT_CHILD_RECORD
    assert await _foods(context) == []


async def test_unclear_만_있으면_저장된_기록도_고치지_않는다(context: AgentContext) -> None:
    await context.store.create_observation(
        domain="food",
        child_id=context.child_id,
        source_writer=context.source_writer,
        raw_text="딸기 좋아해",
        observed_on=NOW.date(),
        observed_range=DateRange(start=NOW.date(), end=NOW.date() + timedelta(days=1)),
        fields={"subject": "딸기", "polarity": 1},
    )
    row_id = (await _foods(context))[0].id
    task = MemoryTask(raw_text="딸기 맛없다.", unclear=("딸기 맛없다",), unclear_only=True)
    llm = FakeLLM(
        _tools(_call("u1", "update_observation_food", {"observation_id": row_id, "polarity": -1}))
    )

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.calls[0].result["error"]["code"] == ErrorCode.NOT_CHILD_RECORD
    assert (await _foods(context))[0].fields["polarity"] == 1


async def test_섞인_입력에서_감상을_거절했으면_조기_종료하지_않고_알린다(
    context: AgentContext,
) -> None:
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_food", _STRAWBERRY)),
        _tools(_call("c2", "create_observation_activity", _BLOCK)),
        _answer({"text": "블록놀이만 기록했어요.", "kind": "message"}),
    )

    result = await run(_MIXED.raw_text, context, client=llm, task=_MIXED)

    assert result.ended_by == "model"
    assert result.final_message == "블록놀이만 기록했어요."


_CORRECTION_TASK = MemoryTask(
    raw_text="떡볶이말고 피자 먹었대.",
    hints=(MemoryHint(text="떡볶이말고 피자 먹었대", work=WorkType.LOOKUP_EDIT),),
)


async def test_고쳐_말하는_조각을_조회_없이_새로_만들면_거절한다(context: AgentContext) -> None:
    llm = FakeLLM(_tools(_call("c1", "create_observation_food", _PIZZA)))

    result = await run(_CORRECTION_TASK.raw_text, context, client=llm, task=_CORRECTION_TASK)

    assert result.calls[0].success is False
    assert result.calls[0].result["error"]["code"] == ErrorCode.TARGET_REQUIRED
    assert await _foods(context) == []


async def test_고쳐_말하는_조각도_찾아본_뒤에는_새로_만들_수_있다(context: AgentContext) -> None:
    # 바뀌기 전 기록이 없으면 바뀐 값만 새로 저장하는 게 맞다
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_food", {"date_from": "오늘"})),
        _tools(_call("c1", "create_observation_food", _PIZZA)),
    )

    result = await run(_CORRECTION_TASK.raw_text, context, client=llm, task=_CORRECTION_TASK)

    assert [call.success for call in result.calls] == [True, True]
    assert len(await _foods(context)) == 1


async def test_없는_필드를_넣으면_빼고_다시_부르라고_알린다(context: AgentContext) -> None:
    # education 에는 assistance_level 이 없다. 복구 방법이 없으면 모델이 저장을 포기했다
    homework = {"raw_text": "숙제 했어", "observed_on": "오늘", "subject": "숙제", "topic": "숙제"}
    extra = {**homework, "assistance_level": "independent"}
    llm = FakeLLM(_tools(_call("c1", "create_observation_education", extra)))

    result = await run("숙제 했어", context, client=llm)

    assert result.calls[0].result["error"]["code"] == ErrorCode.INVALID_ARGS
    assert "빼고 다시 부른다" in result.calls[0].result["error"]["message"]


async def test_같은_응답의_조회는_찾아본_것으로_치지_않는다(context: AgentContext) -> None:
    # 모델이 조회 결과를 보기 전에 새로 만든 것이다 — 바뀌기 전 기록이 그대로 남는다
    llm = FakeLLM(
        _tools(
            _call("q1", "query_observation_food", {"date_from": "오늘"}),
            _call("c1", "create_observation_food", _PIZZA),
        )
    )

    result = await run(_CORRECTION_TASK.raw_text, context, client=llm, task=_CORRECTION_TASK)

    assert [call.success for call in result.calls] == [True, False]
    assert result.calls[1].result["error"]["code"] == ErrorCode.TARGET_REQUIRED


async def test_관찰_조각에도_들어가는_원문은_조회를_강제하지_않는다(context: AgentContext) -> None:
    task = MemoryTask(
        raw_text="어제 사과 기록 지워줘. 오늘 사과 먹었어.",
        hints=(
            MemoryHint(text="어제 사과 기록 지워줘", work=WorkType.LOOKUP_EDIT),
            MemoryHint(text="오늘 사과 먹었어", work=WorkType.OBSERVE),
        ),
    )
    apple = {"raw_text": "사과", "observed_on": "오늘", "subject": "사과"}
    llm = FakeLLM(_tools(_call("c1", "create_observation_food", apple)))

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.calls[0].success is True


async def test_unclear_만_있는_입력의_되묻기는_한_번_다시_확인한다(context: AgentContext) -> None:
    # "나 딸기 좋아해" 에 tool 없이 "아이 취향으로 기록할까요?" 라고 물었다 — 보호자 얘기를 아이에게
    # 옮기는 질문이라 다시 확인하게 하고, 알림으로 바뀌면 되묻기 맥락을 남기지 않는다
    task = MemoryTask(raw_text="나 딸기 좋아해.", unclear=("나 딸기 좋아해",), unclear_only=True)
    llm = FakeLLM(
        _answer({"text": "아이의 음식 취향으로 기록할까요?", "kind": "question"}),
        _answer({"text": "아이 기록이 아니라서 저장하지 않았어요.", "kind": "message"}),
    )

    result = await run(task.raw_text, context, client=llm, task=task)

    assert len(llm.seen) == 2
    assert "보호자에게 보이지 않는 확인" in llm.seen[1][-1]["content"]
    assert result.reply is not None and result.reply.kind == "message"
    assert result.pending is None


# ── 찾은 기록 이어받기 (#529) ───────────────────────────────────
_JUK = "점심으로는 전복죽 먹었어."
_JUK_DELETE = MemoryTask(
    raw_text=f'아까 "{_JUK}" 기록 지워줘',
    hints=(MemoryHint(text=f'아까 "{_JUK}" 기록 지워줘', work=WorkType.LOOKUP_EDIT),),
)


async def _seed_juk(context: AgentContext, raw_text: str = _JUK) -> str:
    today = NOW.date()
    row = await context.store.create_observation(
        domain="food",
        child_id=context.child_id,
        source_writer=context.source_writer,
        raw_text=raw_text,
        observed_on=today,
        observed_range=DateRange(start=today, end=today + timedelta(days=1)),
        fields={"subject": "전복죽", "action": "먹었다"},
    )
    return row.id


async def test_조회하고_되물으면_찾은_기록_id를_맥락에_남긴다(context: AgentContext) -> None:
    juk = await _seed_juk(context)
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_food", {"raw_text_query": "전복죽"})),
        _answer({"text": "점심에 먹은 전복죽 기록을 삭제할까요?", "kind": "question"}),
    )

    result = await run(_JUK_DELETE.raw_text, context, client=llm, task=_JUK_DELETE)

    assert result.pending is not None
    assert result.pending.found == (FoundRef("observation_food", juk),)


async def test_일정을_조회하고_되물으면_일정_id를_남긴다(context: AgentContext) -> None:
    event = await context.store.create_event(
        child_id=context.child_id,
        title="소아과 예약",
        starts_at=datetime(2026, 9, 12, 10, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        fields={},
    )
    task = MemoryTask(
        raw_text="소아과 예약 일정 지워줘",
        hints=(MemoryHint(text="소아과 예약 일정 지워줘", work=WorkType.LOOKUP_EDIT),),
    )
    llm = FakeLLM(
        _tools(_call("q1", "query_event", {"title_query": "소아과"})),
        _answer({"text": "소아과 예약 일정을 삭제할까요?", "kind": "question"}),
    )

    result = await run(task.raw_text, context, client=llm, task=task)

    assert result.pending is not None
    assert result.pending.found == (FoundRef("event", event.id),)


async def test_찾은_기록이_상한과_같으면_모두_남긴다(context: AgentContext) -> None:
    for index in range(MAX_FOUND):
        await _seed_juk(context, f"전복죽 {index}")
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_food", {"raw_text_query": "전복죽"})),
        _answer({"text": "전복죽 기록을 모두 삭제할까요?", "kind": "question"}),
    )

    result = await run(_JUK_DELETE.raw_text, context, client=llm, task=_JUK_DELETE)

    assert result.pending is not None
    assert len(result.pending.found) == MAX_FOUND


async def test_찾은_기록이_상한을_넘으면_하나도_남기지_않는다(context: AgentContext) -> None:
    # 일부만 남기면 "전부 지워줘" 에 "응" 했을 때 일부만 지워진다
    for index in range(MAX_FOUND + 1):
        await _seed_juk(context, f"전복죽 {index}")
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_food", {"raw_text_query": "전복죽"})),
        _answer({"text": "전복죽 기록을 모두 삭제할까요?", "kind": "question"}),
    )

    result = await run(_JUK_DELETE.raw_text, context, client=llm, task=_JUK_DELETE)

    assert result.pending is not None
    assert result.pending.found == ()


async def test_실패한_조회는_찾은_기록으로_세지_않는다(context: AgentContext) -> None:
    await _seed_juk(context)
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_food", {"date_from": "아무말대잔치"})),
        _answer({"text": "언제 기록인가요?", "kind": "question"}),
    )

    result = await run(_JUK_DELETE.raw_text, context, client=llm, task=_JUK_DELETE)

    assert result.calls[0].success is False
    assert result.pending is not None
    assert result.pending.found == ()


async def test_이어받기에서_또_물으면_찾은_id를_이어서_남긴다(context: AgentContext) -> None:
    ref = FoundRef("observation_food", "observation_food-1")
    asked = PendingMemoryContext(
        hint_text=_JUK_DELETE.raw_text,
        question="점심에 먹은 전복죽 기록을 삭제할까요?",
        work=WorkType.LOOKUP_EDIT,
        found=(ref,),
    )
    llm = FakeLLM(_cont_answer({"text": "정말 삭제할까요?", "kind": "question"}))

    result = await run("음...", context, client=llm, continuation=asked)

    assert result.pending is not None
    assert result.pending.found == (ref,)


def _asked_delete(*refs: FoundRef) -> PendingMemoryContext:
    return PendingMemoryContext(
        hint_text=_JUK_DELETE.raw_text,
        question="점심에 먹은 전복죽 기록을 삭제할까요?",
        work=WorkType.LOOKUP_EDIT,
        found=refs,
    )


async def test_이어받기는_찾은_기록을_다시_읽어_보여준다(context: AgentContext) -> None:
    juk = await _seed_juk(context)
    llm = FakeLLM(
        _tools(
            _call("u1", "update_observation_food", {"observation_id": juk, "status": "deleted"})
        ),
        _cont_answer({"text": "삭제했어요.", "kind": "message"}),
    )

    result = await run(
        "응 지워줘.",
        context,
        client=llm,
        continuation=_asked_delete(FoundRef("observation_food", juk)),
    )

    sent = llm.seen[0][1]["content"]
    assert _FOUND_HEADER in sent and juk in sent and _JUK in sent
    assert sent.index(_FOUND_HEADER) < sent.index("[직전에 물은 것]")
    assert result.calls[0].success is True
    assert await context.store.get_observation(domain="food", observation_id=juk) is None


async def test_그새_지워진_기록은_보여주지_않는다(context: AgentContext) -> None:
    juk = await _seed_juk(context)
    await context.store.delete_observation(domain="food", observation_id=juk)
    llm = FakeLLM(_cont_answer({"text": "기록을 찾지 못했어요.", "kind": "message"}))

    await run(
        "응 지워줘.",
        context,
        client=llm,
        continuation=_asked_delete(FoundRef("observation_food", juk)),
    )

    assert _FOUND_HEADER not in llm.seen[0][1]["content"]


async def test_찾은_기록을_보여준_이어받기는_새_저장을_막지_않는다(context: AgentContext) -> None:
    # 고쳐 말하는 조각은 조회 없이 새로 저장하면 막힌다. 찾은 기록을 이미 보여 줬으면 조회한 것이다
    today = NOW.date()
    row = await context.store.create_observation(
        domain="food",
        child_id=context.child_id,
        source_writer=context.source_writer,
        raw_text="떡볶이 먹었어",
        observed_on=today,
        observed_range=DateRange(start=today, end=today + timedelta(days=1)),
        fields={"subject": "떡볶이", "action": "먹었다"},
    )
    asked = PendingMemoryContext(
        hint_text="떡볶이말고 피자 먹었대",
        question="떡볶이 기록을 피자로 바꿀까요?",
        work=WorkType.LOOKUP_EDIT,
        found=(FoundRef("observation_food", row.id),),
    )
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_food", _PIZZA)),
        _cont_answer({"text": "기록했어요.", "kind": "message"}),
    )

    result = await run("응", context, client=llm, continuation=asked)

    assert result.calls[0].success is True


async def _seed_pediatric_event(context: AgentContext) -> str:
    event = await context.store.create_event(
        child_id=context.child_id,
        title="소아과 예약",
        starts_at=datetime(2026, 9, 12, 10, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        fields={},
    )
    await context.store.create_event_item(event_id=event.id, item_name="보험증")
    return event.id


async def test_이어받기가_보여주는_일정에는_준비물이_담긴다(context: AgentContext) -> None:
    # query_event 결과와 같은 모양이어야 준비물 id 를 다시 조회하지 않고 쓸 수 있다
    event_id = await _seed_pediatric_event(context)
    llm = FakeLLM(_cont_answer({"text": "그대로 둘게요.", "kind": "message"}))

    await run(
        "응",
        context,
        client=llm,
        continuation=_asked_delete(FoundRef("event", event_id)),
    )

    sent = llm.seen[0][1]["content"]
    assert _FOUND_HEADER in sent and "보험증" in sent


async def test_일정_이어받기는_조회_없이_그_일정을_지운다(context: AgentContext) -> None:
    event_id = await _seed_pediatric_event(context)
    llm = FakeLLM(
        _tools(_call("d1", "delete_event", {"event_id": event_id})),
        _cont_answer({"text": "삭제했어요.", "kind": "message"}),
    )

    result = await run(
        "응",
        context,
        client=llm,
        continuation=_asked_delete(FoundRef("event", event_id)),
    )

    assert result.tool_names == ["delete_event"] and result.calls[0].success is True
    assert await context.store.get_event(event_id=event_id) is None


async def test_일정만_보여준_이어받기는_관찰_새_저장을_막는다(context: AgentContext) -> None:
    # 보여 준 행과 같은 종류의 관찰만 조회한 것으로 본다. 일정을 봤다고 관찰 조회를 건너뛰지 않는다
    event_id = await _seed_pediatric_event(context)
    asked = PendingMemoryContext(
        hint_text="떡볶이말고 피자 먹었대",
        question="어느 기록을 말씀하시나요?",
        work=WorkType.LOOKUP_EDIT,
        found=(FoundRef("event", event_id),),
    )
    llm = FakeLLM(_tools(_call("c1", "create_observation_food", _PIZZA)))

    result = await run("응", context, client=llm, continuation=asked)

    assert result.calls[0].success is False
    assert result.calls[0].result["error"]["code"] == ErrorCode.TARGET_REQUIRED


async def test_넓게_조회하고_좁혀_조회했으면_마지막_조회_결과만_남긴다(
    context: AgentContext,
) -> None:
    # 질문은 마지막 조회에서 나온다. 앞선 넓은 조회까지 남기면 무관한 행이 같이 보인다
    await _seed_juk(context, "전복죽 0")
    narrow = await _seed_juk(context, "전복죽 1")
    llm = FakeLLM(
        _tools(_call("q1", "query_observation_food", {})),
        _tools(_call("q2", "query_observation_food", {"raw_text_query": "전복죽 1"})),
        _answer({"text": "전복죽 1 기록을 삭제할까요?", "kind": "question"}),
    )

    result = await run(_JUK_DELETE.raw_text, context, client=llm, task=_JUK_DELETE)

    assert result.pending is not None
    assert result.pending.found == (FoundRef("observation_food", narrow),)


async def test_알_수_없는_종류의_id는_보여주지_않는다(context: AgentContext) -> None:
    llm = FakeLLM(_cont_answer({"text": "찾지 못했어요.", "kind": "message"}))

    await run(
        "응",
        context,
        client=llm,
        continuation=_asked_delete(FoundRef("observation_bogus", "x"), FoundRef("junk", "y")),
    )

    assert _FOUND_HEADER not in llm.seen[0][1]["content"]
