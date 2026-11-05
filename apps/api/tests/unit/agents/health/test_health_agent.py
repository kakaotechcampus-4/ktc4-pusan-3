"""Health run() — 동의 없으면 전 라벨 닫힘 · 구현 전 라벨은 모델 없이 unsupported.

모델은 부르면 기록이 남는 가짜다. `seen` 이 비어 있어야 모델 0회다.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from app.agents.health import agent, registry
from app.agents.health.agent import registered_titles, run, run_hooks
from app.agents.health.readouts import READOUTS
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryHealthSafety,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot, HealthObservation, HealthSafetyEntry
from tests.unit.agents.health.support import NOW, TODAY, birth_for, context, observation, task


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


@pytest.mark.parametrize(
    "label",
    [
        label
        for label in HealthTaskType
        if label not in registry.IMPLEMENTED_TASKS and label not in agent._PREPARING
    ],
)
async def test_다른_미구현_라벨은_여전히_readout_없음(label):
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


async def test_run_hooks_는_응급_뒤에_반복_안내를_싣는다():
    # 2개월 아이의 38.3도 + 7일 안 사흘 기침. 응급이 위에 와야 화면이 먼저 그린다
    at = NOW - timedelta(hours=1)
    fever = HealthObservation(
        id=UUID(int=5001),
        observed_from=at.date(),
        observed_to=at.date(),
        observed_time=at,
        symptoms=(),
        temperature=Decimal("38.3"),
        measured_at=at,
    )
    coughs = [observation(TODAY - timedelta(days=offset)) for offset in (0, 1, 2)]
    memory = InMemoryHealthMemory([fever, *coughs])

    result = await run_hooks(context(birth_for(2), memory=memory))

    assert [r.kind for r in result.readouts] == ["emergency", "symptom_repeat"]
    assert (result.task_type, result.status, result.model_calls) == (None, "completed", 0)
    assert result.suggestions == () and result.medication_drafts == ()


async def test_병원_찾기는_준비_중_문구_하나_모델_0회():
    # 위치 동의 약관에 병원 찾기 목적이 들어가기 전이라 위치를 묻지도 않는다
    llm = FakeLLM()

    result = await run(task("place_lookup", "지금 문 연 소아과"), context(), client=llm)

    assert result.status == "unsupported" and result.model_calls == 0
    assert [r.body for r in result.readouts] == ["병원 찾기는 아직 준비 중이에요."]
    assert "위치" not in result.readouts[0].body  # place.no_location 이 아니다
    assert llm.seen == []


def test_준비_중_문구는_구현_전_라벨에만_있다():
    assert set(agent._PREPARING) == {HealthTaskType.PLACE_LOOKUP}
    assert not set(agent._PREPARING) & registry.IMPLEMENTED_TASKS


class _SpyMedication(InMemoryMedicationStore):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reads = 0

    async def active_courses(self, **kwargs):
        self.reads += 1
        return await super().active_courses(**kwargs)


async def test_registered_titles_는_진행_중_코스_이름이다():
    course, rows = course_with_doses(
        "해열 시럽", (DoseSlot.of("bedtime"),), TODAY, TODAY + timedelta(days=2)
    )

    titles = await registered_titles(context(medication=InMemoryMedicationStore([course], rows)))

    assert titles == ("해열 시럽",)


async def test_동의가_없으면_코스를_읽지_않는다():
    spy = _SpyMedication()

    assert await registered_titles(context(consent=False, medication=spy)) is None
    assert spy.reads == 0


async def test_코스를_못_읽으면_None(caplog):
    # 어댑터가 없는 자리(UnwiredMedication)는 읽으면 LookupError 다
    from dataclasses import replace

    from app.agents.health.store.ports import UnwiredMedication

    ctx = context()
    ctx = replace(ctx, ports=replace(ctx.ports, medication=UnwiredMedication()))

    with caplog.at_level("ERROR"):
        assert await registered_titles(ctx) is None

    assert "LookupError" in caplog.text


async def _boom(context):
    raise LookupError("생일을 읽지 못했다")


async def _one_readout(context):
    from app.agents.common.readout import Readout

    return (Readout(kind="symptom_repeat", body="기침이 사흘째 기록됐어요", authored_by="code"),)


@pytest.mark.parametrize("broken_first", [True, False], ids=["앞이_터짐", "뒤가_터짐"])
async def test_hook_하나가_터져도_다른_hook_의_안내는_나간다(monkeypatch, caplog, broken_first):
    handlers = {"a": _boom, "b": _one_readout} if broken_first else {"b": _one_readout, "a": _boom}
    monkeypatch.setattr(agent, "HOOK_HANDLERS", handlers)

    with caplog.at_level("ERROR"):
        result = await run_hooks(context())

    assert [r.kind for r in result.readouts] == ["symptom_repeat"]
    # 예외 메시지에는 생일 · 기록이 섞일 수 있어 종류만 남긴다
    assert "LookupError" in caplog.text and "생일" not in caplog.text
