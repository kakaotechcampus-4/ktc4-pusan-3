"""Health 게이트 — 동의 · 쓰는 라벨에서만 health_safety · 조회 실패 · 아이 타임존 월령."""

import pytest

from app.agents.health.context import HealthRunState, build_gate
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.inmemory import InMemoryHealthSafety
from app.agents.health.store.ports import HealthSafetyEntry
from tests.unit.agents.health.support import BIRTH_12_IN_KST, context


async def test_동의가_없으면_안전_정보를_읽지_않는다():
    safety = InMemoryHealthSafety([HealthSafetyEntry("allergy", "땅콩", "active")])
    ctx = context(consent=False, safety=safety)

    gate = await build_gate(ctx, HealthTaskType.VISIT_SUMMARY)

    assert gate.consent_child_health is False
    assert safety.calls == 0


@pytest.mark.parametrize("label", ["visit_summary", "schedule_check", "care_handoff"])
async def test_쓰는_라벨에서만_안전_정보를_읽는다(label):
    entry = HealthSafetyEntry("allergy", "땅콩", "active")
    safety = InMemoryHealthSafety([entry])
    ctx = context(safety=safety)

    gate = await build_gate(ctx, HealthTaskType(label))

    assert safety.calls == 1
    assert ctx.state.safety_entries == (entry,)
    assert gate.safety_ok is True
    assert gate.allergy_states == ("active",)


@pytest.mark.parametrize("label", ["place_lookup", "medication"])
async def test_병원_찾기와_복약은_안전_정보를_읽지_않는다(label):
    safety = InMemoryHealthSafety([HealthSafetyEntry("allergy", "땅콩", "active")])
    ctx = context(safety=safety)

    gate = await build_gate(ctx, HealthTaskType(label))

    assert safety.calls == 0
    assert ctx.state.safety_entries == ()
    assert gate.safety_ok is True


async def test_안전_조회가_실패하면_safety_ok_가_False():
    ctx = context(safety=InMemoryHealthSafety(fail=True))

    gate = await build_gate(ctx, HealthTaskType.VISIT_SUMMARY)

    # 빈 목록은 "알레르기 없음" 으로 읽힌다 — 실패는 목록이 아니라 플래그로만 전한다
    assert gate.safety_ok is False
    assert ctx.state.safety_entries == ()
    assert gate.allergy_states == ()


async def test_만성질환도_읽는다():
    chronic = HealthSafetyEntry("chronic_disease", "만성 면역력 저하증", "active")
    ctx = context(safety=InMemoryHealthSafety([chronic]))

    gate = await build_gate(ctx, HealthTaskType.CARE_HANDOFF)

    assert ctx.state.safety_entries == (chronic,)
    assert gate.allergy_states == ()


async def test_월령은_아이_타임존_날짜로_센다():
    gate = await build_gate(context(BIRTH_12_IN_KST), HealthTaskType.SCHEDULE_CHECK)

    # NOW 는 UTC 9/27 16:00 — UTC 날짜로 세면 11개월이다
    assert gate.stage.months == 12


def test_for_task_는_run_상태를_비운다():
    ctx = context()
    ctx.state.safety_entries = (HealthSafetyEntry("allergy", "땅콩", "active"),)

    fresh = ctx.for_task()

    assert fresh.state == HealthRunState()
    assert fresh.state is not ctx.state
    assert fresh.ports is ctx.ports
    assert ctx.state.safety_entries  # 원래 것은 그대로
