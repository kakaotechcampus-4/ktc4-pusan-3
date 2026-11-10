"""스토어 정책으로 복약을 꺼 둔 동안 (#923) — 코스로만 일하는 경로를 고정 문구로 닫고, 등록된 약
이름도 읽지 않는다.

다른 테스트는 켜진 동작을 건다(tests/conftest.py `_medication_on`). 여기만 끈다.
복약을 다시 켜면 이 파일을 스위치와 같이 지운다.
"""

import ast
import inspect
from datetime import timedelta
from pathlib import Path

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


def test_출고값은_꺼짐이다():
    # 다른 테스트는 모두 스위치를 바꿔 끼우므로 출고값을 아무도 보지 않는다. 충돌 해결 · 디버깅 중에
    # True 로 바뀌면 웹은 꺼진 채 서버만 켜져 나간다. 다시 켤 때는 이 파일을 통째로 지운다
    # 원문 한 줄만 보면 아래에 `MEDICATION_ENABLED = True` 를 한 줄 더 써도 통과한다 — 값을 넣는
    # 곳이 하나뿐이고 그 값이 상수 False 인지 본다(환경 변수 등으로 덮어쓰면 상수가 아니다)
    tree = ast.parse(Path(inspect.getfile(registry)).read_text(encoding="utf-8"))
    values = [node.value for node in ast.walk(tree) if _assigns_switch(node)]

    assert len(values) == 1
    assert isinstance(values[0], ast.Constant) and values[0].value is False


def _assigns_switch(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    else:
        return False
    return any(isinstance(t, ast.Name) and t.id == "MEDICATION_ENABLED" for t in targets)


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
async def test_복약_라벨은_모델도_코스도_없이_준비_중_문구로_닫힌다(text):
    llm = FakeLLM()

    result = await run(task("medication", text), context(medication=_NoReads()), client=llm)

    assert (result.status, result.model_calls) == ("unsupported", 0)
    assert result.readouts == (READOUTS.render(MED_PREPARING),)
    assert result.medication_drafts == () and result.pending is None
    assert llm.seen == []


async def test_동의가_없어도_동의_안내가_아니라_준비_중_문구다():
    # 동의 닫힘보다 먼저 닫는다 — 동의 안내가 나가면 꺼 둔 기능 때문에 동의를 받게 된다. 게이트의
    # 조회보다 앞인지는 투약의뢰서 테스트(safety.calls)가 같은 자리(`_switched_off`)로 건다
    result = await run(task("medication", "항생제 먹여야 해"), context(consent=False))

    assert result.readouts == (READOUTS.render(MED_PREPARING),)


@pytest.mark.parametrize(
    "text", ["어린이집에 낼 투약의뢰서 써줘.", "내일 어린이집에 약 보낼 거 써줘"]
)
async def test_투약의뢰서는_아무것도_읽지_않고_준비_중_문구로_닫힌다(text):
    # care_handoff 는 게이트가 안전 정보를 읽는 라벨이다 — 안 읽었으면 게이트 앞에서 닫은 것이다
    safety = InMemoryHealthSafety([HealthSafetyEntry("allergy", "땅콩", "active")])
    llm = FakeLLM()

    result = await run(
        task("care_handoff", text), context(safety=safety, medication=_NoReads()), client=llm
    )

    assert (result.status, result.model_calls) == ("unsupported", 0)
    assert result.readouts == (READOUTS.render(NOTICE_PREPARING),)
    assert llm.seen == [] and safety.calls == 0


async def test_동의가_없어도_투약의뢰서는_준비_중_문구다():
    result = await run(task("care_handoff", "투약의뢰서 써줘"), context(consent=False))

    assert result.readouts == (READOUTS.render(NOTICE_PREPARING),)


async def test_등록된_약_이름을_읽지_않는다():
    # 읽으면 pipeline 이 먹인 약 말을 Memory 게이트로 막고 체크 화면으로 보내는데, 꺼 둔 동안은 그
    # 화면이 없다. 꺼 두기 전에 만든 코스가 남아 있어도 같다
    course, rows = course_with_doses(
        "해열 시럽", (DoseSlot.of("bedtime"),), TODAY, TODAY + timedelta(days=2)
    )
    spy = _SpyMedication([course], rows)

    assert await registered_titles(context(medication=spy)) is None
    assert spy.reads == 0


@pytest.mark.parametrize(
    "text",
    [
        "시터한테 줄 알레르기랑 먹는 약 정리해줘.",
        # 넓은 투약 표현("투약")이 있어도 카드 표현이 먼저다 — care_handoff 경로와 같은 표로 가른다
        "응급 카드에 투약 중인 약도 넣어줘",
    ],
)
async def test_응급_카드는_닫지_않는다(text):
    result = await run(task("care_handoff", text), context())

    assert result.status == "completed"
    assert [r.kind for r in result.readouts] == ["emergency_card"]
