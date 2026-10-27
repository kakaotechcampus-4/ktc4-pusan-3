"""라이브 eval 세계가 운영 run 과 같은 포트로 만들어지는지 — 모델을 부르지 않는다(키 없이 돈다).

🚨 `growth_safety` 를 넘기면서 `child_health_granted` 를 빠뜨리면 동의 없음으로 읽혀 알레르기 대조
없이 교육이 열린다. 세계는 둘을 늘 같이 넘긴다 — 동의 있음 · 없음 양쪽을 여기서 고정한다.
"""

from datetime import timedelta

import pytest

from app.agents import entrypoint
from app.agents.growth.context import build_gate
from app.agents.growth.doc_seed import growth_doc_seed
from app.agents.growth.registry import closed_readout_key
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import InMemorySafety
from app.agents.growth.store.ports import SafetyEntry
from app.agents.memory.store import InMemoryStore
from tests.eval.agents.growth.world import CHILD, NOW, WORLDS, birth_date_for, build_world

LEARNING = GrowthTaskType.LEARNING_SUGGESTION


async def test_동의가_있는_세계는_알레르기를_한_번_읽는다():
    world = await build_world("C48-allergy")
    gate = await build_gate(world.context, LEARNING)
    assert (gate.consent_child_health, gate.safety_ok, world.safety.calls) == (True, True, 1)
    labels = {entry.label for entry in world.context.state.safety_entries}
    assert labels == {"라텍스", "꽃가루", "고소공포"}


async def test_동의가_없는_세계는_알레르기를_읽지_않고_교육이_열린다():
    world = await build_world("C48-allergy-noconsent")
    gate = await build_gate(world.context, LEARNING)
    assert (gate.consent_child_health, world.safety.calls) == (False, 0)
    assert closed_readout_key(LEARNING, gate) is None


async def test_동의를_빠뜨리면_동의_없음으로_읽힌다():
    """세계가 이 길로 가지 않는 이유 — 동의 값을 안 넘긴 호출은 동의 없음이다.

    키 · 몸무게를 안 읽게 하려는 기본값이다.
    """
    safety = InMemorySafety([SafetyEntry("allergy", "라텍스", "active")])
    contexts, _ = entrypoint._domain_contexts(
        child_id=CHILD,
        run_id="probe",
        now=NOW,
        store=InMemoryStore(now=NOW),
        birth_date=birth_date_for(48, NOW.date()),
        child_health_granted=None,
        location=None,
        activity_weather=None,
        domain_store=None,
        growth_safety=safety,
    )
    gate = await build_gate(contexts["growth"], LEARNING)
    assert (gate.consent_child_health, safety.calls) == (False, 0)


async def test_운영과_같은_문서_행이고_관심은_0행이다():
    world = await build_world("C48-empty")
    affinities = await world.context.ports.memory.affinities(
        child_id=CHILD, domains=("education", "activity")
    )
    assert affinities == []
    rows = await world.context.ports.docs.search(
        months=48,
        row_type="learning_activity",
        routine_category=None,
        trigger_tags=(),
        query="",
        limit=100,
    )
    approved = [
        e
        for e in growth_doc_seed()
        if e.meta.status == "approved"
        and e.meta.row_type == "learning_activity"
        and e.meta.covers(48)
    ]
    assert len(rows) == len(approved)


async def test_C48_은_Memory_store_의_관찰과_덧댄_관심을_같이_읽는다():
    world = await build_world("C48")
    memory, today = world.context.ports.memory, world.today
    routine = await memory.routine(
        child_id=CHILD, date_from=today - timedelta(days=14), date_to=today
    )
    assert {"양치하기", "숟가락 쓰기", "손톱 물어뜯기"} <= {row.subject for row in routine}
    affinities = await memory.affinities(child_id=CHILD, domains=("education", "activity"))
    assert {row.merge_key for row in affinities} == {"농구", "공룡"}


@pytest.mark.parametrize("name", sorted(WORLDS))
async def test_모든_세계를_만들_수_있다(name):
    world = await build_world(name)
    assert world.context.child_id == CHILD and world.birth == birth_date_for(
        WORLDS[name].months, world.today
    )
