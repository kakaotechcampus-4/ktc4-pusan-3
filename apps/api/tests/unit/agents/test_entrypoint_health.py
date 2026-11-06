"""Health 포트 묶음이 `_domain_contexts` 까지 오는 길. Supervisor 가 Health 를 부르기 전의 배선."""

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from app.agents import entrypoint
from app.agents.activity.store.ports import CoarseLocation
from app.agents.common.child_values import ChildValues
from app.agents.common.ports import SafetyLookupError
from app.agents.health.context import HealthContext
from app.agents.memory.store import InMemoryStore
from app.agents.memory_bridge import StoreHealthMemory
from app.agents.suggestion_save import InMemorySuggestions

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
    assert ports.medication is medication
    # 쓰기 포트는 그 run 의 쓰기 표시(RunWrites)로 감싸서 넘긴다 — 안쪽은 넘긴 어댑터다
    assert ports.medication_writer._inner is medication
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


async def _child_months(monkeypatch: pytest.MonkeyPatch, birth_date: date | None) -> int | None:
    """entrypoint 가 pipeline 에 넘긴 월령."""
    seen: dict[str, Any] = {}

    async def capture(*args: Any, **kwargs: Any) -> None:
        seen.update(kwargs)

    async def no_commit() -> None:
        """호출되지 않는다 — pipeline 을 바꿔 끼웠다."""

    monkeypatch.setattr(entrypoint, "_handle_input", capture)
    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=UUID(int=2),
        raw_text="열이 38도야",
        run_id="r",
        birth_date=birth_date,
        commit=no_commit,
        save_suggestions=InMemorySuggestions(),
    )
    return seen["child_months"]


async def test_응급_사전검사에_생일로_계산한_월령을_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = datetime.now(entrypoint.KST).date()

    assert await _child_months(monkeypatch, today - timedelta(days=70)) == 2


async def test_생일을_못_받으면_사전검사에_지어낸_월령을_넘기지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await _child_months(monkeypatch, None) is None


async def test_Health_중단_쓰기는_run_의_쓰기_표시에_남는다() -> None:
    """멈춘 뒤 시간 초과가 나도 러너가 쓴 run 으로 보게, 진입점이 쓰기 포트를 감싼다."""

    class Writer:
        async def stop_course(self, **_: object) -> str:
            return "stopped"

    now = datetime.now(timezone.utc)
    contexts, writes = entrypoint._domain_contexts(
        child_id=CHILD,
        run_id="r",
        now=now,
        store=InMemoryStore(now=now),
        birth_date=None,
        child_health_granted=None,
        location=None,
        activity_weather=None,
        domain_store=None,
        health_medication_writer=Writer(),
    )
    await contexts["health"].ports.medication_writer.stop_course(
        child_id=CHILD, schedule_id=UUID(int=2), stopped_at=now
    )

    assert writes.wrote


# ── 병원 찾기 위치 · 병원 어댑터 (#713 후속) ─────────────────────────────────────────

# 지어낸 좌표 — 실제 보호자 위치가 아니다
SOMEWHERE = CoarseLocation(lat=35.18, lon=129.08)


class _Hospitals:
    """`FacilitySource` 자리 — 부르지 않는다. 넘긴 객체가 그대로 가는지만 본다."""

    async def facilities(self, **_: object) -> list[object]:
        return []


async def _contexts_of(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> dict[str, Any]:
    """`handle_input` 이 pipeline 에 넘긴 도메인 context."""
    seen: dict[str, Any] = {}

    async def capture(raw_text: str, memory_context: Any, contexts: Any, **_: Any) -> None:
        seen.update(contexts)

    async def no_commit() -> None:
        """호출되지 않는다 — pipeline 을 바꿔 끼웠다."""

    monkeypatch.setattr(entrypoint, "_handle_input", capture)
    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=UUID(int=2),
        raw_text="지금 문 연 소아과 있어?",
        run_id="r",
        commit=no_commit,
        save_suggestions=InMemorySuggestions(),
        **kwargs,
    )
    return seen


def test_병원_어댑터와_Health_위치를_넘기면_Health_가_받는다() -> None:
    hospitals = _Hospitals()

    context = _health(health_places=hospitals, health_location=SOMEWHERE)

    assert context.ports.places is hospitals
    assert context.location is SOMEWHERE


def test_병원_어댑터와_위치를_안_넘기면_둘_다_없다() -> None:
    # 없으면 병원 찾기가 place.no_location · place.unavailable 로 닫힌다 — 지어낸 값으로 열지 않는다
    context = _health()

    assert context.ports.places is None
    assert context.location is None


async def test_handle_input_이_Health_위치와_병원_어댑터를_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospitals = _Hospitals()

    contexts = await _contexts_of(monkeypatch, health_location=SOMEWHERE, health_places=hospitals)

    assert contexts["health"].location is SOMEWHERE
    assert contexts["health"].ports.places is hospitals


async def test_Activity_위치는_Health_로_넘어가지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """🚨 위치 약관은 판마다 목적이 다르다 — 날씨 · 놀이 목적의 이전 판에만 동의한 보호자의 위치를
    병원 찾기에 쓰지 않게, Health 위치는 Activity 위치와 따로 받는다(#586 · 위치 약관 제2조 ④⑤)."""
    contexts = await _contexts_of(monkeypatch, location=SOMEWHERE)

    assert contexts["activity"].location is SOMEWHERE
    assert contexts["health"].location is None


def test_task_마다_새로_연_context_도_위치와_병원_어댑터를_그대로_갖는다() -> None:
    hospitals = _Hospitals()

    context = _health(health_places=hospitals, health_location=SOMEWHERE).for_task()

    assert context.location is SOMEWHERE
    assert context.ports.places is hospitals
