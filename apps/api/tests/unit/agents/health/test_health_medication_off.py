"""스토어 정책으로 복약을 꺼 둔 동안 (#923) — 코스로만 일하는 경로를 고정 문구로 닫고, 등록된 약
이름도 읽지 않는다.

다른 테스트는 켜진 동작을 건다(tests/conftest.py `_medication_on`). 여기만 끈다.
복약을 다시 켜면 이 파일을 스위치와 같이 지운다.
"""

from datetime import timedelta

import pytest

from app.agents.health import registry
from app.agents.health.agent import registered_titles, run
from app.agents.health.readouts import MED_PREPARING, NOTICE_PREPARING, READOUTS
from app.agents.health.store.inmemory import (
    InMemoryHealthSafety,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot, HealthSafetyEntry
from tests.unit.agents.health.support import TODAY, FakeLLM, context, task


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.setattr(registry, "MEDICATION_ENABLED", False)


class _NoReads(InMemoryMedicationStore):
    async def active_courses(self, **kwargs):
        raise AssertionError("꺼 둔 동안 코스를 읽으면 안 된다")


class _SpyMedication(InMemoryMedicationStore):
    # registered_titles 는 조회 예외를 삼키고 None 을 낸다 — 읽었는지는 예외가 아니라 횟수로 본다
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reads = 0

    async def active_courses(self, **kwargs):
        self.reads += 1
        return await super().active_courses(**kwargs)


@pytest.mark.parametrize(
    "text",
    ["하루 세 번 항생제 먹여야 해", "다음 약 언제야?", "시럽 이제 안 먹여", "용량 늘려도 돼?"],
)
async def test_복약_라벨은_아무것도_읽지_않고_준비_중_문구로_닫힌다(text):
    safety = InMemoryHealthSafety([HealthSafetyEntry("allergy", "땅콩", "active")])
    llm = FakeLLM()

    result = await run(
        task("medication", text), context(safety=safety, medication=_NoReads()), client=llm
    )

    assert (result.status, result.model_calls) == ("unsupported", 0)
    assert result.readouts == (READOUTS.render(MED_PREPARING),)
    assert result.medication_drafts == () and result.pending is None
    assert llm.seen == [] and safety.calls == 0


async def test_동의가_없어도_동의_안내가_아니라_준비_중_문구다():
    result = await run(task("medication", "항생제 먹여야 해"), context(consent=False))

    assert result.readouts == (READOUTS.render(MED_PREPARING),)


@pytest.mark.parametrize(
    "text", ["어린이집에 낼 투약의뢰서 써줘.", "내일 어린이집에 약 보낼 거 써줘"]
)
async def test_투약의뢰서는_아무것도_읽지_않고_준비_중_문구로_닫힌다(text):
    llm = FakeLLM()

    result = await run(task("care_handoff", text), context(medication=_NoReads()), client=llm)

    assert (result.status, result.model_calls) == ("unsupported", 0)
    assert result.readouts == (READOUTS.render(NOTICE_PREPARING),)
    assert llm.seen == []


async def test_등록된_약_이름을_읽지_않는다():
    # 읽으면 pipeline 이 먹인 약 말을 Memory 게이트로 막고 체크 화면으로 보내는데, 꺼 둔 동안은 그
    # 화면이 없다. 꺼 두기 전에 만든 코스가 남아 있어도 같다
    course, rows = course_with_doses(
        "해열 시럽", (DoseSlot.of("bedtime"),), TODAY, TODAY + timedelta(days=2)
    )
    spy = _SpyMedication([course], rows)

    assert await registered_titles(context(medication=spy)) is None
    assert spy.reads == 0


async def test_응급_카드는_닫지_않는다():
    result = await run(task("care_handoff", "시터한테 줄 알레르기랑 먹는 약 정리해줘."), context())

    assert result.status == "completed"
    assert [r.kind for r in result.readouts] == ["emergency_card"]
