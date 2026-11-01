"""답을 받는 질문 — 고르기 · 보내기 · 05 빈 안내 (`pipeline._pick_question` 외).

도메인 Agent 를 가짜로 바꿔 pipeline 이 결과에서 읽는 칸만으로 본다. Growth 가 실제로 물은 자리를
싣는 것은 tests/unit/agents/growth, Memory 까지 이어 도는 길은 test_growth_answer_resume.py 다.
"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from app.agents import entrypoint, pipeline
from app.agents.common.readout import Readout
from app.agents.common.result import DomainPending
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import SuggestionDraft
from tests.unit.agents.supervisor.test_pipeline import _draft


@dataclass(frozen=True)
class Outcome:
    """가짜 도메인 결과. pipeline 이 읽는 칸만 둔다 (`pipeline.DomainOutcome`)."""

    agent: str
    task_type: str | None = None
    status: str = "completed"
    model_calls: int = 1
    readouts: tuple[Readout, ...] = ()
    needs_observation: tuple[str, ...] = ()
    suggestions: tuple[SuggestionDraft, ...] = ()
    pending: DomainPending | None = None


@dataclass(frozen=True)
class FakeContext:
    def for_task(self) -> "FakeContext":
        return FakeContext()


EMPTY = Readout(
    kind="notice",
    body="지금 어떤지 알아야 맞는 방법을 고를 수 있어요.",
    authored_by="code",
    code="empty",
)
GROWTH_Q = "양치는 요즘 어느 정도까지 혼자 하나요?"
FOOD_Q = "브로콜리는 좋아하는 편인가요?"


def pending_of(question: str, agent: str = "growth") -> DomainPending:
    return DomainPending(agent, "ask.routine_current", question, {"subject": "양치"})


def growth_asks(**kwargs: Any) -> Outcome:
    return Outcome(
        "growth",
        "routine_coaching",
        readouts=(EMPTY,),
        needs_observation=(GROWTH_Q,),
        pending=pending_of(GROWTH_Q),
        **kwargs,
    )


def food_asks() -> Outcome:
    return Outcome("food", "meal_recommendation", readouts=(EMPTY,), needs_observation=(FOOD_Q,))


FOOD_TASK = DomainTask("run-1", "food", "meal_recommendation", ("반찬 추천",), record_texts=("a",))
GROWTH_TASK = DomainTask("run-1", "growth", "routine_coaching", ("양치 가르치기",))
TASKS = (FOOD_TASK, GROWTH_TASK)


class TestPickQuestion:
    def test_답을_받는_질문이_같은_순위의_다른_질문보다_먼저다(self):
        growth = growth_asks()

        asked = pipeline._pick_question([food_asks(), growth], TASKS, can_resume=True)

        assert asked == (growth, GROWTH_TASK)

    def test_맥락을_못_만드는_run_은_task_순서다(self):
        food = food_asks()

        asked = pipeline._pick_question([food, growth_asks()], TASKS, can_resume=False)

        assert asked == (food, None)

    def test_표에_없는_Agent_의_pending_은_답을_받지_않는다(self):
        outcome = Outcome(
            "activity",
            None,
            needs_observation=(FOOD_Q,),
            pending=pending_of(FOOD_Q, "activity"),
        )

        asked = pipeline._pick_question(
            [outcome], (DomainTask("run-1", "activity", None, ("놀이",)),), can_resume=True
        )

        assert asked == (outcome, None)

    def test_추천에_덧붙은_질문은_이어받지_않는다(self):
        growth = Outcome(
            "growth",
            "routine_coaching",
            needs_observation=(GROWTH_Q,),
            pending=pending_of(GROWTH_Q),
            suggestions=(_draft("칫솔 같이 쥐기"),),
        )

        asked = pipeline._pick_question([growth], (GROWTH_TASK,), can_resume=True)

        assert asked == (growth, None)

    def test_질문을_낸_결과가_없으면_고르지_않는다(self):
        assert pipeline._pick_question([Outcome("food")], TASKS, can_resume=True) is None


class TestSendQuestion:
    def sent(self, asked: Any) -> list[Any]:
        events: list[Any] = []
        pipeline._send_question(asked, "run-1", events.append)
        return events

    def test_답을_받는_질문은_맥락을_먼저_보내고_awaits_reply_가_참이다(self):
        task = DomainTask(
            "run-1",
            "growth",
            "routine_coaching",
            ("양치 가르치기",),
            record_texts=("오늘 그림 그렸어",),
            recorded_subjects=("양치하기",),
        )

        events = self.sent((growth_asks(), task))

        reply, question = events
        assert isinstance(reply, pipeline.PendingReply)
        assert reply.run_id == "run-1"
        assert reply.context.hint_text == "양치 가르치기"
        assert reply.context.question == GROWTH_Q
        assert reply.context.asked == pending_of(GROWTH_Q)
        # 요청 조각만 둔다. 같은 입력의 기록 조각 원문은 15분 메모리에도 두지 않는다
        assert reply.context.resume.record_texts == ()
        assert reply.context.resume.recorded_subjects == ()
        assert reply.context.resume.request_texts == ("양치 가르치기",)
        assert (question.text, question.awaits_reply) == (GROWTH_Q, True)

    def test_답을_받지_않는_질문은_맥락_없이_문장만_나간다(self):
        (question,) = self.sent((food_asks(), None))

        assert isinstance(question, pipeline.AgentQuestion)
        assert (question.agent, question.text, question.awaits_reply) == ("food", FOOD_Q, False)


class TestEmptyResult:
    """답을 받는 질문은 05 에 처음 run 의 0개 안내를 남기지 않는다."""

    async def stage(
        self, monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, Outcome], *, can_resume: bool
    ) -> list[Any]:
        for agent, outcome in outcomes.items():

            async def run(task: DomainTask, context: Any, *, _o: Outcome = outcome, **kw: Any):
                return _o

            monkeypatch.setitem(pipeline._RUNNERS, agent, run)
        events: list[Any] = []
        await pipeline._domain_stage(
            tuple(task for task in TASKS if task.agent in outcomes),
            {agent: FakeContext() for agent in outcomes},
            run_id="run-1",
            sent_at=datetime.now(),
            started=time.perf_counter(),
            send=events.append,
            save_suggestions=entrypoint.InMemorySuggestions(),
            count_records=None,
            domain_clients=None,
            can_resume=can_resume,
            step=pipeline.Step(3, 3, "다음 행동을 준비하고 있어요"),
        )
        return events

    async def test_고른_답을_받는_질문은_05_빈_안내를_남기지_않는다(self, monkeypatch):
        events = await self.stage(
            monkeypatch, {"food": food_asks(), "growth": growth_asks()}, can_resume=True
        )

        # Food 질문은 답을 받지 않아서 지금처럼 남는다. Growth 질문만 빠진다
        assert [e.agent for e in events if isinstance(e, pipeline.EmptyResult)] == ["food"]

    async def test_맥락을_못_만드는_run_은_그대로_남긴다(self, monkeypatch):
        events = await self.stage(monkeypatch, {"growth": growth_asks()}, can_resume=False)

        assert [e.agent for e in events if isinstance(e, pipeline.EmptyResult)] == ["growth"]
