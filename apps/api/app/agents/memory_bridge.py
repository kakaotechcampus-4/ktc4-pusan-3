"""같은 run 의 Memory store 를 도메인 Agent 의 기억 포트로 읽는다.

방금 저장한 관찰이 같은 run 의 추천 근거로 잡혀야 혼합형이 성립한다 (루트 §4).
store 종류는 모른다 — MemoryStore Protocol 로만 읽는다.

읽을 store 를 받는 길이 둘이다.
    StoreFoodMemory(store)          이미 열린 store 를 그대로 읽는다. 테스트 · eval 의 InMemoryStore
    StoreFoodMemory(opener=opener)  호출마다 opener 로 store 를 새로 연다. 운영에서 러너가 준다

🚨 운영에서는 opener 로 간다. 러너의 run 세션(Memory 가 쓰는 세션)을 도메인 task 둘이 같이
   타면 AsyncSession 이 쿼리 둘을 동시에 못 돌려 깨진다. 러너는 호출마다 짧은 세션을 따로 열어
   주고(#257), Memory 는 도메인 단계 전에 commit 되므로 따로 연 세션에서도 방금 저장한
   관찰이 보인다.

Agent 패키지는 서로 import 하지 않는다. Memory store 와 Food · Activity ·
Growth 포트를 둘 다 아는 곳은 pipeline · entrypoint 층이라 여기 둔다.
Curator 는 이 다리를 쓰지 않고 DbCuratorStore 를 직접 쓴다 (domains/memory/curator/trigger.py).

profile_affinity 는 Memory store 에 없어 따로 받은 어댑터(`affinities=`, 운영은
`DbAffinityReader`)로 읽는다. 마지막 관찰 뒤 21일이 지난 관심은 그날(`today=`) 기준
archived 로 넘긴다. 어댑터를 안 넘기면(테스트 · eval) 빈 목록이다 — 러너 · 05 는 늘 넘긴다.
"""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from datetime import date
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agents.activity.store.ports import ActivityObservation
from app.agents.common.evidence import AffinityRow
from app.agents.food.store.ports import FoodObservation
from app.agents.growth.store.ports import ActivityObservation as GrowthActivityObservation
from app.agents.growth.store.ports import (
    AffinityDomain,
    EducationObservation,
    RoutineObservation,
)
from app.agents.memory.store.ports import MemoryStore, ObservationRow
from app.rules.profile import archived_by_time

# 검색에 남는 status. stand_alone 은 Correction once_only("이번만 그랬어요") 라
# Curator 집계에서만 빠진다 (data_model §observation). inactive · deleted 는 읽지 않는다
FOOD_STATUSES = frozenset({"active", "stand_alone"})
# Activity는 active만 읽는다. Activity 포트(ActivityObservation 주석)와 activity-agent-v1
#   A-10 이 stand_alone을 근거에서 뺀다 — "이번만 그랬어요" 로 고친 기록이 근거로 돌아오지 않게
ACTIVITY_STATUSES = frozenset({"active"})
# Growth 도 active 만 읽는다 — "이번만 그랬어요" 로 고친 기록으로 루틴 수준을 잡지 않는다
#   (growth/store/ports.py RoutineObservation 주석)
GROWTH_STATUSES = frozenset({"active"})


# 호출 한 번에 쓸 store 를 여는 함수. 러너가 짧은 DB 세션으로 만든다
StoreOpener = Callable[[], AbstractAsyncContextManager[MemoryStore]]


def same_store(store: MemoryStore) -> StoreOpener:
    """이미 열린 store 를 그대로 돌려주는 opener. 세션이 없는 InMemoryStore 용."""

    @asynccontextmanager
    async def opener() -> AsyncIterator[MemoryStore]:
        yield store

    return opener


class AffinityReader(Protocol):
    """`profile_affinity` 를 읽는 어댑터.

    운영에서는 `domains/memory/profile/agent_reader.DbAffinityReader`.
    """

    async def affinities(self, *, child_id: UUID, domains: Sequence[str]) -> list[AffinityRow]: ...


class _StoreReader:
    def __init__(
        self,
        store: MemoryStore | None = None,
        *,
        opener: StoreOpener | None = None,
        affinities: AffinityReader | None = None,
        today: date | None = None,
    ):
        if (store is None) == (opener is None):
            raise TypeError("store 와 opener 중 하나만 넘긴다")
        if affinities is not None and today is None:
            raise TypeError(
                "관심 어댑터를 넘기면 기준일(today)도 넘긴다 — 오래된 관심을 그날 기준으로 거른다"
            )
        self._open: StoreOpener = opener or same_store(store)  # type: ignore[arg-type]
        self._affinity_reader = affinities
        self._today = today

    async def _affinity_rows(self, child_id: UUID, domains: Sequence[str]) -> list[AffinityRow]:
        """어댑터가 없으면 빈 목록. 있으면 읽은 관심에 그날 기준 시간 경과 archived 를 적용한다.

        Curator 는 관찰이 붙을 때만 상태를 다시 계산한다. 같은 공식의 시간 조건만 그날 기준으로
        다시 적용하고, 근거에서 빼는 일은 소비처가 한다.
        """
        if self._affinity_reader is None or self._today is None:
            return []
        rows = await self._affinity_reader.affinities(child_id=child_id, domains=domains)
        return [
            replace(row, state="archived")
            if row.state != "archived"
            and archived_by_time(last_observed_on=row.last_observed_on, today=self._today)
            else row
            for row in rows
        ]


def _uuid(row_id: str) -> UUID:
    """InMemory id(observation_food-1)는 UUID 가 아니다. 같은 id 는 늘 같은 UUID 로 바꾼다."""
    try:
        return UUID(row_id)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"inmemory:{row_id}")


async def _rows(
    open_store: StoreOpener,
    domain: str,
    *,
    child_id: UUID,
    date_from: date,
    date_to: date,
    statuses: frozenset[str],
) -> list[ObservationRow]:
    async with open_store() as store:
        rows = await store.query_observations(
            domain=domain, child_id=child_id, date_from=date_from, date_to=date_to
        )
    # status가 없으면 읽지 않는다
    return [row for row in rows if row.fields.get("status") in statuses]


class StoreFoodMemory(_StoreReader):
    """`FoodMemoryReader` 구현. `observation_food`를 Memory store 에서 읽는다."""

    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        return await self._affinity_rows(child_id, ("food",))

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[FoodObservation]:
        rows = await _rows(
            self._open,
            "food",
            child_id=child_id,
            date_from=date_from,
            date_to=date_to,
            statuses=FOOD_STATUSES,
        )
        return [
            FoodObservation(
                id=_uuid(row.id),
                child_id=child_id,
                observed_on=row.observed_on,
                subject=row.fields["subject"],
                amount_text=row.fields.get("amount"),
                polarity=int(row.fields.get("polarity") or 0),
                updated_at=row.created_at,
                confidence_source=row.fields.get("confidence_source"),
                source_notice_id=row.fields.get("source_notice_id"),
            )
            for row in rows
        ]


class StoreActivityMemory(_StoreReader):
    """`ActivityMemoryReader` 구현. `observation_activity`를 Memory store 에서 읽는다."""

    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        return await self._affinity_rows(child_id, ("activity",))

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ActivityObservation]:
        rows = await _rows(
            self._open,
            "activity",
            child_id=child_id,
            date_from=date_from,
            date_to=date_to,
            statuses=ACTIVITY_STATUSES,
        )
        return [
            ActivityObservation(
                id=_uuid(row.id),
                child_id=child_id,
                observed_on=row.observed_on,
                subject=row.fields["subject"],
                activity=row.fields["activity"],
                polarity=int(row.fields.get("polarity") or 0),
            )
            for row in rows
        ]


class StoreGrowthMemory(_StoreReader):
    """`GrowthMemoryReader` 구현. 교육 · 루틴 · 놀이 관찰을 Memory store 에서 읽는다.

    놀이 관찰은 Growth 가 근거로 **읽기만** 한다 — 쓰지 않는다. profile_affinity 는 Memory store 에
    없어 빈 목록이다(Food · Activity 와 같다).
    """

    async def affinities(
        self, *, child_id: UUID, domains: tuple[AffinityDomain, ...]
    ) -> list[AffinityRow]:
        return []

    async def education(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[EducationObservation]:
        rows = await _rows(
            self._open,
            "education",
            child_id=child_id,
            date_from=date_from,
            date_to=date_to,
            statuses=GROWTH_STATUSES,
        )
        return [
            EducationObservation(
                id=_uuid(row.id),
                child_id=child_id,
                observed_on=row.observed_on,
                subject=row.fields["subject"],
                topic=row.fields["topic"],
                polarity=int(row.fields.get("polarity") or 0),
                engagement_level=row.fields.get("engagement_level"),
            )
            for row in rows
        ]

    async def routine(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[RoutineObservation]:
        rows = await _rows(
            self._open,
            "routine",
            child_id=child_id,
            date_from=date_from,
            date_to=date_to,
            statuses=GROWTH_STATUSES,
        )
        return [
            RoutineObservation(
                id=_uuid(row.id),
                child_id=child_id,
                observed_on=row.observed_on,
                subject=row.fields["subject"],
                routine_category=row.fields["routine_category"],
                polarity=int(row.fields.get("polarity") or 0),
                assistance_level=row.fields.get("assistance_level"),
                completion_status=row.fields.get("completion_status"),
                trigger=row.fields.get("trigger"),
            )
            for row in rows
        ]

    async def activities(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[GrowthActivityObservation]:
        rows = await _rows(
            self._open,
            "activity",
            child_id=child_id,
            date_from=date_from,
            date_to=date_to,
            statuses=GROWTH_STATUSES,
        )
        return [
            GrowthActivityObservation(
                id=_uuid(row.id),
                child_id=child_id,
                observed_on=row.observed_on,
                subject=row.fields["subject"],
                activity=row.fields["activity"],
                polarity=int(row.fields.get("polarity") or 0),
            )
            for row in rows
        ]
