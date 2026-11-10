"""스토어 정책으로 복약을 꺼 둔 동안 (#923) — 코스로만 일하는 경로를 고정 문구로 닫는다.

다른 테스트는 켜진 동작을 건다(tests/conftest.py `_medication_on`). 여기만 끈다.
복약을 다시 켜면 이 파일을 스위치와 같이 지운다.
"""

import pytest

from app.agents.health import registry
from app.agents.health.agent import run
from app.agents.health.readouts import MED_PREPARING, READOUTS
from app.agents.health.store.inmemory import InMemoryHealthSafety, InMemoryMedicationStore
from app.agents.health.store.ports import HealthSafetyEntry
from tests.unit.agents.health.support import FakeLLM, context, task


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.setattr(registry, "MEDICATION_ENABLED", False)


class _NoReads(InMemoryMedicationStore):
    async def active_courses(self, **kwargs):
        raise AssertionError("꺼 둔 동안 코스를 읽으면 안 된다")


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
