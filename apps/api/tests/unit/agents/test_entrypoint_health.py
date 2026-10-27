"""Health 포트 묶음이 `_domain_contexts` 까지 오는 길. Supervisor 가 Health 를 부르기 전의 배선."""

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import pytest

from app.agents import entrypoint
from app.agents.common.child_values import ChildValues
from app.agents.common.ports import SafetyLookupError
from app.agents.health.context import HealthContext
from app.agents.memory.store import InMemoryStore
from app.agents.memory_bridge import StoreHealthMemory

CHILD = UUID(int=1)
BIRTH = date(2024, 3, 1)
TODAY = date(2026, 9, 28)


def _health(**kwargs: Any) -> HealthContext:
    now = datetime.now(timezone.utc)
    contexts, _ = entrypoint._domain_contexts(
        child_id=CHILD,
        run_id="r",
        now=now,
        store=InMemoryStore(now=now),
        birth_date=kwargs.pop("birth_date", None),
        child_health_granted=kwargs.pop("child_health_granted", None),
        location=None,
        activity_weather=None,
        domain_store=kwargs.pop("domain_store", None),
        **kwargs,
    )
    return contexts["health"]


def test_넘긴_Health_어댑터를_그대로_쓴다() -> None:
    safety, medication, child = object(), object(), object()

    ports = _health(
        health_safety=safety,
        health_medication=medication,
        health_medication_writer=medication,
        health_child=child,
    ).ports

    assert ports.safety is safety
    assert ports.medication is medication and ports.medication_writer is medication
    assert ports.child is child


def test_기억_포트는_같은_run_의_store_를_읽는다() -> None:
    assert isinstance(_health().ports.memory, StoreHealthMemory)


async def test_생일과_동의는_API_가_읽어_넘긴_값이다() -> None:
    context = _health(birth_date=BIRTH, child_health_granted=True)

    assert isinstance(context.ports.profile, ChildValues)
    assert await context.ports.profile.birth_date(child_id=CHILD) == BIRTH
    assert await context.ports.consent.child_health_granted(child_id=CHILD) is True


async def test_동의_값을_못_받은_호출은_동의_없음이다() -> None:
    # 값이 없으면 건강정보를 읽지 않는 쪽으로 닫는다 — 열린 채로 두지 않는다
    context = _health(child_health_granted=None)

    assert await context.ports.consent.child_health_granted(child_id=CHILD) is False


async def test_Health_어댑터를_안_넘기면_읽을_때_실패한다() -> None:
    # 빈 저장소로 열리면 복약이 "없음", 알레르기가 "없음" 으로 읽혀 응급 카드가 비어 나간다
    ports = _health().ports

    with pytest.raises(SafetyLookupError):
        await ports.safety.health_safety(child_id=CHILD)
    with pytest.raises(LookupError):
        await ports.medication.active_courses(child_id=CHILD, today=TODAY)
    with pytest.raises(LookupError):
        await ports.medication.courses_overlapping(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    with pytest.raises(LookupError):
        await ports.medication.doses(schedule_ids=[], date_from=TODAY, date_to=TODAY)
    with pytest.raises(LookupError):
        await ports.medication_writer.stop_course(
            child_id=CHILD, schedule_id=UUID(int=2), stopped_at=datetime.now(timezone.utc)
        )
    with pytest.raises(LookupError):
        await ports.child.basic(child_id=CHILD)


@pytest.mark.parametrize("granted", [True, False, None])
async def test_생일을_못_받으면_지어낸_생일로_열지_않는다(granted) -> None:
    # Food 는 생일이 없으면 만 2세로 열지만, Health 는 3개월 미만 발열처럼 나이로 응급을 가른다
    ports = _health(birth_date=None, child_health_granted=granted).ports

    with pytest.raises(LookupError):
        await ports.profile.birth_date(child_id=CHILD)
