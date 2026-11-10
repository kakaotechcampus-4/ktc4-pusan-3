"""후속 질문일 때만 앞 대화를 조회해 Supervisor 에 넘긴다 — pipeline 배선.

    ① 후속이 아닌 입력은 조회하지 않고 메시지도 지금과 같다
    ② 후속이면 앞 대화는 Supervisor 에만 간다 — 추천 task 는 해석한 요청을,
       Memory 는 이번 원문만 받는다
    ③ 앞 턴이 없거나 조회가 실패해도 run 은 그대로 돌고, Supervisor 는 "없음" 을 받는다
    ④ 재분기 호출에도 같은 맥락이 실린다 — 조회는 한 번이다
    ⑤ 되묻기 답(이어받기) · 포트 없음 · 로그

Supervisor · Memory · Growth 는 가짜 LLM 이다. 문장은 전부 지어낸 것이다.
"""

import logging
from typing import Any

import pytest

from app.agents import entrypoint, pipeline
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore
from app.agents.supervisor.context import (
    EMPTY_CONTEXT,
    LOOKUP_FAILED,
    ConversationContext,
    PriorResult,
    PriorTurn,
)
from tests.unit.agents.growth.support import fixture_docs
from tests.unit.agents.growth.test_growth_answer_resume import (
    ASKED,
    CHILD,
    KST,
    NOW,
    PARENT,
    asks_level,
    memory_says,
)
from tests.unit.agents.growth.test_growth_wiring import FakeLLM, call
from tests.unit.agents.growth.test_growth_wiring import contexts as built_contexts

PRIOR = ConversationContext(
    (
        PriorTurn(
            "물감 놀이 추천해줘",
            (PriorResult("activity", None, "completed", ("손바닥 물감 찍기",)),),
        ),
    )
)
FOLLOW = "다른 방법도 알려줘"
RESOLVED = "손바닥 물감 찍기 말고 다른 물감 놀이 방법 추천"
FOLLOW_ROUTE = {
    "segments": [
        {
            "text": FOLLOW,
            "kind": "request",
            "agent": "growth",
            "growth_task": "routine_coaching",
            "resolved_request": RESOLVED,
        }
    ]
}


@pytest.fixture(autouse=True)
def _fixture_docs(monkeypatch):
    from app.agents.growth.store.inmemory import InMemoryGrowthDocs

    monkeypatch.setattr(InMemoryGrowthDocs, "from_seed", classmethod(lambda cls: fixture_docs()))


class RecordingLLM(FakeLLM):
    """FakeLLM 에 호출마다 받은 메시지를 남긴다."""

    def __init__(self, *responses: Any) -> None:
        super().__init__(*responses)
        self.messages: list[list[dict[str, Any]]] = []

    async def chat(self, messages, **kwargs):
        self.messages.append(list(messages))
        return await super().chat(messages, **kwargs)


class Loader:
    """앞 대화 조회 포트. 몇 번 불렸는지 센다."""

    def __init__(self, result: ConversationContext | Exception) -> None:
        self.result = result
        self.calls = 0

    async def __call__(self) -> ConversationContext:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def _no_commit() -> None:
    return None


def route_llm(*outputs: dict[str, Any]) -> RecordingLLM:
    return RecordingLLM(*(call("route", output) for output in outputs))


async def run(
    raw: str,
    *,
    supervisor: RecordingLLM,
    memory: RecordingLLM | None = None,
    recent_turns: Any = None,
    continuation: Any = None,
    growth_texts: list[tuple[str, ...]] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> list[Any]:
    if growth_texts is not None and monkeypatch is not None:
        real = pipeline._RUNNERS["growth"]

        async def recording(task, context, **kwargs):
            growth_texts.append(task.request_texts)
            return await real(task, context, **kwargs)

        monkeypatch.setitem(pipeline._RUNNERS, "growth", recording)
    events: list[Any] = []
    store = InMemoryStore(now=NOW)
    await pipeline.handle_input(
        raw,
        AgentContext(child_id=CHILD, source_writer=PARENT, now=NOW, timezone=KST, store=store),
        built_contexts(store),
        run_id="run-2",
        supervisor_client=supervisor,
        memory_client=memory or RecordingLLM(),
        domain_clients={"growth": asks_level()},
        emit=events.append,
        continuation=continuation,
        commit=_no_commit,
        save_suggestions=entrypoint.InMemorySuggestions(),
        recent_turns=recent_turns,
    )
    return events


def roles(llm: RecordingLLM, call_index: int = 0) -> list[str]:
    return [message["role"] for message in llm.messages[call_index]]


async def test_후속이_아닌_입력은_조회하지_않는다() -> None:
    loader = Loader(PRIOR)
    request = "양치 혼자 하게 하고 싶어"
    supervisor = route_llm(
        {
            "segments": [
                {
                    "text": request,
                    "kind": "request",
                    "agent": "growth",
                    "growth_task": "routine_coaching",
                }
            ]
        }
    )

    await run(request, supervisor=supervisor, recent_turns=loader)

    assert loader.calls == 0
    assert roles(supervisor) == ["system", "user"]


async def test_후속_입력은_앞_대화를_Supervisor_에만_넘긴다(monkeypatch) -> None:
    loader = Loader(PRIOR)
    supervisor, texts = route_llm(FOLLOW_ROUTE), []

    await run(
        FOLLOW,
        supervisor=supervisor,
        recent_turns=loader,
        growth_texts=texts,
        monkeypatch=monkeypatch,
    )

    assert loader.calls == 1
    assert supervisor.messages[0][1] == {"role": "user", "content": PRIOR.render()}
    assert supervisor.messages[0][2] == {"role": "user", "content": FOLLOW}
    assert texts == [(RESOLVED,)]  # 추천 task 는 해석한 요청을 받는다


async def test_Memory_는_이번_원문만_받는다(monkeypatch) -> None:
    raw = "오늘 또 울었어. 다른 방법도 알려줘"
    supervisor = route_llm(
        {
            "segments": [
                {"text": "오늘 또 울었어", "kind": "record", "work": "observe"},
                FOLLOW_ROUTE["segments"][0],
            ]
        }
    )
    said = memory_says("저장할 내용이 없어요.", answering=False)
    memory, texts = RecordingLLM(said, said), []

    await run(
        raw,
        supervisor=supervisor,
        memory=memory,
        recent_turns=Loader(PRIOR),
        growth_texts=texts,
        monkeypatch=monkeypatch,
    )

    sent = "\n".join(message["content"] or "" for call_ in memory.messages for message in call_)
    assert "오늘 또 울었어" in sent
    assert "물감 놀이" not in sent and "손바닥 물감" not in sent and RESOLVED not in sent
    assert texts == [(RESOLVED,)]


async def test_앞_턴이_없으면_빈_맥락으로_간다() -> None:
    loader = Loader(ConversationContext())
    supervisor = route_llm({"segments": [{"text": FOLLOW, "kind": "unclear"}]})

    await run(FOLLOW, supervisor=supervisor, recent_turns=loader)

    assert loader.calls == 1
    assert supervisor.messages[0][1] == {"role": "user", "content": EMPTY_CONTEXT}


async def test_조회가_실패하면_빈_맥락으로_간다(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    loader = Loader(RuntimeError("물감 놀이 원문이 섞인 오류 문구"))
    supervisor = route_llm({"segments": [{"text": FOLLOW, "kind": "unclear"}]})

    events = await run(FOLLOW, supervisor=supervisor, recent_turns=loader)

    assert supervisor.messages[0][1] == {"role": "user", "content": LOOKUP_FAILED}
    assert any(isinstance(event, pipeline.Done) for event in events)  # run 은 그대로 끝난다
    assert "RuntimeError" in caplog.text
    assert "물감" not in caplog.text  # 예외 메시지는 싣지 않는다


async def test_다시_나눌_때도_같은_맥락을_싣는다() -> None:
    loader = Loader(PRIOR)
    # 처음엔 요청을 기록으로 보내 Memory 가 적지 않는다 → 다시 나눈다
    supervisor = route_llm(
        {"segments": [{"text": FOLLOW, "kind": "record", "work": "observe"}]}, FOLLOW_ROUTE
    )
    said = memory_says("저장할 내용이 없어요.", answering=False)

    await run(FOLLOW, supervisor=supervisor, memory=RecordingLLM(said, said), recent_turns=loader)

    assert supervisor.calls == 2
    assert loader.calls == 1  # 조회는 한 번이다
    assert supervisor.messages[1][1] == {"role": "user", "content": PRIOR.render()}
    assert supervisor.messages[1][-1]["content"].startswith("[다시 나누기]")


async def test_이어받기_run_은_조회하지_않는다() -> None:
    loader = Loader(PRIOR)

    await run("그거요", supervisor=route_llm(), recent_turns=loader, continuation=ASKED)

    assert loader.calls == 0


async def test_포트가_없으면_지금처럼_돈다() -> None:
    supervisor = route_llm(FOLLOW_ROUTE)

    await run(FOLLOW, supervisor=supervisor, recent_turns=None)

    assert roles(supervisor) == ["system", "user"]


async def test_로그에는_감지와_턴_수만(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    await run(FOLLOW, supervisor=route_llm(FOLLOW_ROUTE), recent_turns=Loader(PRIOR))

    assert "followup run_id=run-2 turns=1" in caplog.text
    assert "물감" not in caplog.text and "다른 방법" not in caplog.text


async def test_되묻기_맥락의_hint_text_는_보호자_원문이다() -> None:
    events = await run(FOLLOW, supervisor=route_llm(FOLLOW_ROUTE), recent_turns=Loader(PRIOR))

    (pending,) = [event for event in events if isinstance(event, pipeline.PendingReply)]
    # 답을 받으면 Memory · Supervisor 로 가는 말이다 — AI 가 쓴 해석 문장이 보호자 말로 안 섞인다
    assert pending.context.hint_text == FOLLOW
    assert RESOLVED not in pending.context.hint_text
    # 답을 받고 이어 돌 task 는 해석한 요청과 보호자 원문을 둘 다 들고 있다
    assert pending.context.resume.request_texts == (RESOLVED,)
    assert pending.context.resume.original_texts == (FOLLOW,)


async def test_다시_시도_맥락에는_해석과_보호자_원문이_둘_다_간다(monkeypatch) -> None:
    async def failing(task, context, **kwargs):
        raise RuntimeError("부서졌다")

    monkeypatch.setitem(pipeline._RUNNERS, "growth", failing)

    events = await run(FOLLOW, supervisor=route_llm(FOLLOW_ROUTE), recent_turns=Loader(PRIOR))

    (retry,) = [event for event in events if isinstance(event, pipeline.RetryContext)]
    # 05 는 해석한 요청으로 다시 만들고, 출처 확인은 보호자 원문 · 앞 턴의 말로 한다
    assert retry.request_texts == (RESOLVED,)
    assert retry.original_texts == (FOLLOW,)
    assert retry.prior_texts == ("물감 놀이 추천해줘",)


async def test_Growth_는_출처_확인에_보호자_원문을_쓴다(monkeypatch) -> None:
    real = pipeline._RUNNERS["growth"]
    seen: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    async def recording(task, context, **kwargs):
        result = await real(task, context, **kwargs)
        seen.append((task.request_texts, context.state.said_texts))
        return result

    monkeypatch.setitem(pipeline._RUNNERS, "growth", recording)

    await run(FOLLOW, supervisor=route_llm(FOLLOW_ROUTE), recent_turns=Loader(PRIOR))

    assert seen == [((RESOLVED,), (FOLLOW,))]


async def test_해석을_받은_task_는_앞_턴의_보호자_말도_든다(monkeypatch) -> None:
    real = pipeline._RUNNERS["growth"]
    seen: list[tuple[str, ...]] = []

    async def recording(task, context, **kwargs):
        seen.append(task.prior_texts)
        return await real(task, context, **kwargs)

    monkeypatch.setitem(pipeline._RUNNERS, "growth", recording)

    await run(FOLLOW, supervisor=route_llm(FOLLOW_ROUTE), recent_turns=Loader(PRIOR))

    assert seen == [("물감 놀이 추천해줘",)]


async def test_닫힘은_해석_문장에서_빠진_보호자_말도_본다() -> None:
    """ "약 먹는 걸 싫어해. 다른 방법 없어?" 를 Supervisor 가 약 없이 풀어 써도 Growth 는 닫힌다."""
    raw = "약 먹는 걸 너무 싫어해. 다른 방법 없어?"
    route = {
        "segments": [
            {
                "text": raw,
                "kind": "request",
                "agent": "growth",
                "growth_task": "routine_coaching",
                "resolved_request": "양치를 싫어하는 아이에게 쓸 다른 습관 방법 추천",
            }
        ]
    }

    events = await run(raw, supervisor=route_llm(route), recent_turns=Loader(PRIOR))

    (result,) = [e for e in events if isinstance(e, pipeline.AgentResult) and e.agent == "growth"]
    assert result.status == "blocked"


# ── 이미 보여 준 추천 (#852) ─────────────────────────────────────────────
def _routing(*tasks):
    from app.agents.common.schemas.task import DomainTask
    from app.agents.supervisor.routing import Routing

    return Routing(
        intent_type="request",
        memory_task=None,
        domain_tasks=tuple(
            DomainTask(
                run_id="r",
                agent=agent,
                task_type=None,
                request_texts=(text,),
                original_texts=originals,
            )
            for agent, text, originals in tasks
        ),
    )


SHOWN = ConversationContext(
    (
        PriorTurn(
            "주말에 갈만한 행사 추천해줘",
            (
                PriorResult(
                    "activity", None, "completed", ("가상 국화축제 구경하기", "가상 공원 산책")
                ),
                PriorResult("food", "meal_recommendation", "completed", ("가상 주먹밥",)),
            ),
        ),
        PriorTurn(
            "다른 행사 추천해줘",
            (PriorResult("activity", None, "completed", ("가상 빛축제 보기",)),),
        ),
    )
)


def test_앞_턴들에서_같은_Agent_가_낸_추천을_모두_싣는다() -> None:
    """ "다른 거" 를 여러 번 물어도 앞에서 보여 준 것을 다시 내지 않게. 해석을 받지 않은 task 도
    싣는다 — "주말에 갈만한 다른 행사" 는 혼자 읽어도 통해서 Supervisor 가 해석을 안 쓸 수 있다."""
    routing = pipeline._with_prior(
        _routing(("activity", "주말에 갈만한 다른 행사 추천해줘", ()), ("food", "간식 추천", ())),
        SHOWN,
    )
    activity, food = routing.domain_tasks
    assert activity.shown_before == ("가상 국화축제 구경하기", "가상 공원 산책", "가상 빛축제 보기")
    assert food.shown_before == ("가상 주먹밥",)
    # 앞 턴의 보호자 말은 지금처럼 해석을 받은 task 만이다
    assert activity.prior_texts == ()


def test_앞_대화가_없으면_보여_준_것도_없다() -> None:
    routing = pipeline._with_prior(_routing(("activity", "다른 행사 추천해줘", ())), None)
    assert routing.domain_tasks[0].shown_before == ()
