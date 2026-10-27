"""memory_bridge 검증. 같은 run 의 Memory store 를 Food · Activity 기억 포트로 읽는다."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.datetime_rules import build_observed_range
from app.agents.common.evidence import AffinityRow
from app.agents.memory.store import InMemoryStore, MemoryStore
from app.agents.memory.store.ports import ObservationRow
from app.agents.memory_bridge import (
    StoreActivityMemory,
    StoreFoodMemory,
    StoreGrowthMemory,
    same_store,
)

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=KST)
TODAY = NOW.date()
CHILD = UUID(int=1)
PARENT = UUID(int=2)
OTHER = UUID(int=3)


async def _save(
    store: InMemoryStore,
    domain: str,
    *,
    child_id: UUID = CHILD,
    day: date = TODAY,
    **fields: Any,
) -> ObservationRow:
    return await store.create_observation(
        domain=domain,
        child_id=child_id,
        source_writer=PARENT,
        raw_text=f"{fields['subject']} 기록",
        observed_on=day,
        observed_range=build_observed_range(day),
        fields=fields,
    )


async def test_Food_포트는_저장한_food_관찰을_FoodObservation_으로_읽는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(store, "food", subject="딸기", amount="반 개", polarity=1)
    reader = StoreFoodMemory(store)

    rows = await reader.observations(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    again = await reader.observations(child_id=CHILD, date_from=TODAY, date_to=TODAY)

    assert len(rows) == 1
    row = rows[0]
    assert (row.child_id, row.observed_on, row.subject) == (CHILD, TODAY, "딸기")
    assert (row.amount_text, row.polarity, row.updated_at) == ("반 개", 1, NOW)
    # 근거로 인용한 id 가 다음 조회와 맞아야 한다
    assert again[0].id == row.id


async def test_Food_포트는_관찰의_출처를_같이_넘긴다() -> None:
    # 기관 식사가 daycare_meal 과 겹치는지는 출처로 가른다(영양소_계산_설계 N-10)
    store = InMemoryStore(now=NOW)
    notice = UUID(int=50)
    await _save(store, "food", subject="카레", confidence_source="parent_hearsay")
    await _save(
        store,
        "food",
        subject="미역국",
        confidence_source="institution_notice",
        source_notice_id=notice,
    )
    await _save(store, "food", subject="딸기")

    rows = await StoreFoodMemory(store).observations(child_id=CHILD, date_from=TODAY, date_to=TODAY)

    by_subject = {row.subject: (row.confidence_source, row.source_notice_id) for row in rows}
    assert by_subject == {
        "카레": ("parent_hearsay", None),
        "미역국": ("institution_notice", notice),
        "딸기": (None, None),
    }


async def test_Food_는_stand_alone_까지_읽고_inactive_deleted_는_읽지_않는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(store, "food", subject="딸기")
    once = await _save(store, "food", subject="사과")
    wrong = await _save(store, "food", subject="배")
    gone = await _save(store, "food", subject="귤")
    # once_only("이번만 그랬어요") 는 검색에 남고, wrong("잘못된 기록") 은 빠진다
    await store.update_observation(
        domain="food", observation_id=once.id, fields={"status": "stand_alone"}
    )
    await store.update_observation(
        domain="food", observation_id=wrong.id, fields={"status": "inactive"}
    )
    await store.delete_observation(domain="food", observation_id=gone.id)

    rows = await StoreFoodMemory(store).observations(child_id=CHILD, date_from=TODAY, date_to=TODAY)

    assert sorted(row.subject for row in rows) == ["딸기", "사과"]


async def test_Activity_는_active_만_읽는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(store, "activity", subject="레고", activity="레고로 성 만들기", polarity=1)
    once = await _save(store, "activity", subject="수영장", activity="수영장 놀이")
    await store.update_observation(
        domain="activity", observation_id=once.id, fields={"status": "stand_alone"}
    )

    rows = await StoreActivityMemory(store).observations(
        child_id=CHILD, date_from=TODAY, date_to=TODAY
    )

    assert [(row.subject, row.activity, row.polarity) for row in rows] == [
        ("레고", "레고로 성 만들기", 1)
    ]


async def test_다른_아이_다른_도메인_구간_밖은_읽지_않는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(store, "food", subject="딸기")
    await _save(store, "food", subject="사과", child_id=OTHER)
    await _save(store, "activity", subject="레고", activity="레고")
    await _save(store, "food", subject="배", day=date(2026, 9, 1))

    rows = await StoreFoodMemory(store).observations(
        child_id=CHILD, date_from=date(2026, 9, 5), date_to=TODAY
    )

    assert [row.subject for row in rows] == ["딸기"]


class _Affinities:
    """`DbAffinityReader` 자리. 어느 도메인을 물었는지 남긴다."""

    def __init__(self, *rows: AffinityRow) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[str, ...]] = []

    async def affinities(self, *, child_id: UUID, domains: Sequence[str]) -> list[AffinityRow]:
        self.calls.append(tuple(domains))
        return list(self.rows)


def _affinity(key: str, *, days_ago: int, state: str = "confirmed") -> AffinityRow:
    return AffinityRow(
        id=UUID(int=900 + days_ago),
        merge_key=key,
        state=state,
        polarity=1,
        strength=0.9,
        last_observed_on=TODAY - timedelta(days=days_ago),
    )


async def test_관심_어댑터를_안_넘기면_빈_목록이다() -> None:
    store = InMemoryStore(now=NOW)

    assert await StoreFoodMemory(store).affinities(child_id=CHILD) == []
    assert await StoreActivityMemory(store).affinities(child_id=CHILD) == []


async def test_Food_는_food_Activity_는_activity_로_읽는다() -> None:
    reader = _Affinities(_affinity("공룡", days_ago=3))
    store = InMemoryStore(now=NOW)

    await StoreFoodMemory(store, affinities=reader, today=TODAY).affinities(child_id=CHILD)
    await StoreActivityMemory(store, affinities=reader, today=TODAY).affinities(child_id=CHILD)

    assert reader.calls == [("food",), ("activity",)]


async def test_마지막_관찰_뒤_21일이_지난_관심은_그날_기준_archived_로_읽는다() -> None:
    reader = _Affinities(
        _affinity("공룡", days_ago=3),
        _affinity("블록", days_ago=21),
        _affinity("기차", days_ago=22),
        _affinity("퍼즐", days_ago=30, state="candidate"),
    )
    memory = StoreActivityMemory(InMemoryStore(now=NOW), affinities=reader, today=TODAY)

    rows = await memory.affinities(child_id=CHILD)

    assert [(r.merge_key, r.state) for r in rows] == [
        ("공룡", "confirmed"),
        ("블록", "confirmed"),
        ("기차", "archived"),
        ("퍼즐", "archived"),  # 오래된 candidate 는 Food 되묻기에서도 빠진다
    ]


def test_관심_어댑터만_넘기고_기준일을_안_넘기면_막는다() -> None:
    with pytest.raises(TypeError, match="기준일"):
        StoreFoodMemory(InMemoryStore(now=NOW), affinities=_Affinities())


async def test_opener_로_열어도_관심_어댑터를_같이_받는다() -> None:
    reader = _Affinities(_affinity("공룡", days_ago=3))
    memory = StoreActivityMemory(
        opener=same_store(InMemoryStore(now=NOW)), affinities=reader, today=TODAY
    )

    assert [r.merge_key for r in await memory.affinities(child_id=CHILD)] == ["공룡"]


class _NoStatusStore:
    """status 를 fields 에 싣지 않는 store. 어댑터가 빠뜨려도 근거로 새면 안 된다."""

    async def query_observations(self, **_: Any) -> list[ObservationRow]:
        return [
            ObservationRow(
                id="x-1",
                domain="food",
                raw_text="배 기록",
                created_at=NOW,
                observed_on=TODAY,
                fields={"subject": "배"},
            )
        ]


async def test_status_가_없는_관찰은_읽지_않는다() -> None:
    # 모르는 상태를 active 로 보면 inactive("잘못된 기록") 가 근거로 돌아올 수 있다
    rows = await StoreFoodMemory(_NoStatusStore()).observations(  # type: ignore[arg-type]
        child_id=CHILD, date_from=TODAY, date_to=TODAY
    )

    assert rows == []


async def test_opener_를_주면_호출마다_store_를_새로_연다() -> None:
    """DB 에서는 호출마다 짧은 세션을 연다. 같은 세션을 두 task 가 동시에 쓰지 않게 (#257)."""
    store = InMemoryStore(now=NOW)
    await _save(store, "food", subject="딸기", polarity=1, status="active")
    opened: list[int] = []

    @asynccontextmanager
    async def opener() -> AsyncIterator[MemoryStore]:
        opened.append(len(opened))
        yield store

    reader = StoreFoodMemory(opener=opener)
    first, second = await asyncio.gather(
        reader.observations(child_id=CHILD, date_from=TODAY, date_to=TODAY),
        reader.observations(child_id=CHILD, date_from=TODAY, date_to=TODAY),
    )

    assert len(opened) == 2
    assert [o.subject for o in first] == [o.subject for o in second] == ["딸기"]


def test_store_와_opener_는_하나만_받는다() -> None:
    store = InMemoryStore(now=NOW)

    @asynccontextmanager
    async def opener() -> AsyncIterator[MemoryStore]:
        yield store

    with pytest.raises(TypeError):
        StoreFoodMemory(store, opener=opener)
    with pytest.raises(TypeError):
        StoreActivityMemory()


async def test_Growth_포트는_교육_루틴_놀이_관찰을_읽는다() -> None:
    store = InMemoryStore(now=NOW)
    await _save(
        store,
        "education",
        subject="숫자",
        topic="계단 숫자 세기",
        polarity=1,
        engagement_level="high",
    )
    await _save(
        store,
        "routine",
        subject="양치",
        routine_category="self_care",
        polarity=0,
        assistance_level="full_assist",
        completion_status=None,
        trigger=None,
    )
    await _save(store, "activity", subject="블록", activity="블록 쌓기", polarity=1)
    reader = StoreGrowthMemory(store)

    (education,) = await reader.education(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    (routine,) = await reader.routine(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    (activity,) = await reader.activities(child_id=CHILD, date_from=TODAY, date_to=TODAY)

    assert (education.subject, education.topic, education.engagement_level) == (
        "숫자",
        "계단 숫자 세기",
        "high",
    )
    assert (routine.routine_category, routine.assistance_level) == ("self_care", "full_assist")
    assert (activity.subject, activity.activity) == ("블록", "블록 쌓기")
    assert await reader.affinities(child_id=CHILD, domains=("education",)) == []


async def test_Growth_포트는_이번만_그랬어요로_고친_루틴을_읽지_않는다() -> None:
    store = InMemoryStore(now=NOW)
    row = await _save(store, "routine", subject="양치", routine_category="self_care", polarity=0)
    await store.update_observation(
        domain="routine", observation_id=row.id, fields={"status": "stand_alone"}
    )
    reader = StoreGrowthMemory(store)

    assert await reader.routine(child_id=CHILD, date_from=TODAY, date_to=TODAY) == []
