"""Health run() — 동의 없으면 전 라벨 닫힘 · 구현 전 라벨은 모델 없이 unsupported.

모델은 부르면 기록이 남는 가짜다. `seen` 이 비어 있어야 모델 0회다.
"""

from typing import Any

import pytest

from app.agents.health import agent, registry
from app.agents.health.agent import run
from app.agents.health.readouts import READOUTS
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.inmemory import InMemoryHealthSafety
from app.agents.health.store.ports import HealthSafetyEntry
from tests.unit.agents.health.support import context, task


class FakeLLM:
    def __init__(self) -> None:
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages, **kwargs):
        self.seen.append([dict(m) for m in messages])
        raise AssertionError("모델을 부르면 안 되는 경로다")


@pytest.mark.parametrize("label", list(HealthTaskType))
async def test_동의가_없으면_전_라벨이_닫힌다(label):
    safety = InMemoryHealthSafety([HealthSafetyEntry("allergy", "땅콩", "active")])
    llm = FakeLLM()

    result = await run(task(label.value), context(consent=False, safety=safety), client=llm)

    assert result.status == "blocked"
    assert result.task_type == label.value
    assert result.model_calls == 0
    assert result.readouts == (READOUTS.render("blocked.consent"),)
    assert safety.calls == 0
    assert llm.seen == []
    assert result.medication_drafts == ()
    assert result.event_requests == ()
    assert result.suggestions == ()


@pytest.mark.parametrize("label", list(HealthTaskType))
async def test_구현되지_않은_라벨은_모델_없이_unsupported(label):
    llm = FakeLLM()

    result = await run(task(label.value), context(), client=llm)

    assert result.status == "unsupported"
    assert result.task_type == label.value
    assert result.model_calls == 0
    assert result.readouts == ()
    assert llm.seen == []


async def test_라벨이_없으면_아무것도_읽지_않고_unsupported():
    safety = InMemoryHealthSafety([HealthSafetyEntry("allergy", "땅콩", "active")])
    llm = FakeLLM()

    result = await run(task(None), context(safety=safety), client=llm)

    assert result.status == "unsupported"
    assert result.task_type is None
    assert safety.calls == 0
    assert llm.seen == []


async def test_모르는_라벨도_unsupported():
    result = await run(task("growth_review"), context(), client=FakeLLM())

    assert result.status == "unsupported"
    assert result.task_type is None


def test_라벨_실행표와_구현_목록이_같다():
    assert set(agent._LABEL_RUNNERS) == registry.IMPLEMENTED_TASKS
