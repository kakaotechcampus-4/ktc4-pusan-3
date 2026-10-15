"""05 가 부르는 도메인 단계 (#249). Supervisor · Memory 없이 task 만 돌린다."""

import asyncio

import pytest

from app.agents import pipeline
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import SuggestionDraft
from app.agents.suggestion_save import InMemorySuggestions


class _Ctx:
    def for_task(self):
        return self


def _draft(content: str) -> SuggestionDraft:
    return SuggestionDraft(agent="activity", kind="personalized", content=content, reason="r")


def _task(agent: str = "activity") -> DomainTask:
    return DomainTask(run_id="req-1", agent=agent, task_type=None, request_texts=("놀이 추천해줘",))


@pytest.fixture
def runners(monkeypatch):
    table: dict = {}
    monkeypatch.setattr(pipeline, "_RUNNERS", table)
    return table


async def test_추천을_저장하고_id_를_싣는다(runners):
    async def ok(task, context):
        return DomainAgentResult(
            agent="activity", task_type=None, status="completed", suggestions=(_draft("블록"),)
        )

    runners["activity"] = ok
    saved = InMemorySuggestions()
    run = await pipeline.run_suggestions(
        (_task(),), {"activity": _Ctx()}, save_run_id="run-9", save_suggestions=saved
    )
    [outcome] = run.outcomes
    assert len(outcome.suggestion_ids) == 1
    assert saved.batches[0].run_id == "run-9"
    assert run.failed == ()


async def test_홈_요청은_run_칸을_비운다(runners):
    async def ok(task, context):
        return DomainAgentResult(
            agent="activity", task_type=None, status="completed", suggestions=(_draft("블록"),)
        )

    runners["activity"] = ok
    saved = InMemorySuggestions()
    await pipeline.run_suggestions(
        (_task(),), {"activity": _Ctx()}, save_run_id=None, save_suggestions=saved
    )
    assert saved.batches[0].run_id is None


async def test_시간_안에_못_끝낸_Agent_는_실패로_센다(runners):
    async def slow(task, context):
        await asyncio.sleep(1)

    async def ok(task, context):
        return DomainAgentResult(agent="food", task_type="meal_recommendation", status="completed")

    runners["activity"] = slow
    runners["food"] = ok
    run = await pipeline.run_suggestions(
        (_task(), DomainTask("req-1", "food", "meal_recommendation", ())),
        {"activity": _Ctx(), "food": _Ctx()},
        save_run_id=None,
        save_suggestions=InMemorySuggestions(),
        timeout=0.05,
    )
    assert [o.agent for o in run.outcomes] == ["food"]
    assert run.failed == ("activity",)


async def test_저장에_실패하면_그_Agent_는_실패다(runners):
    async def ok(task, context):
        return DomainAgentResult(
            agent="activity", task_type=None, status="completed", suggestions=(_draft("블록"),)
        )

    async def broken(batch):
        raise RuntimeError("db")

    runners["activity"] = ok
    run = await pipeline.run_suggestions(
        (_task(),), {"activity": _Ctx()}, save_run_id=None, save_suggestions=broken
    )
    assert run.outcomes == () and run.failed == ("activity",)


async def test_결과의_되묻기를_그대로_싣는다(runners):
    async def asks(task, context):
        return DomainAgentResult(
            agent="activity",
            task_type=None,
            status="completed",
            needs_observation=("어떤 놀이를 좋아해요?",),
        )

    runners["activity"] = asks
    run = await pipeline.run_suggestions(
        (_task(),), {"activity": _Ctx()}, save_run_id=None, save_suggestions=InMemorySuggestions()
    )
    [outcome] = run.outcomes
    assert (outcome.agent, outcome.task_type, outcome.status) == ("activity", None, "completed")
    assert outcome.question == "어떤 놀이를 좋아해요?"
    assert outcome.suggestion_ids == ()
