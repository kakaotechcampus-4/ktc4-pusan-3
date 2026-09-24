"""인메모리 CuratorStore. DB 구현이 붙기 전까지 테스트에 쓴다.

add_observation · add_profile 은 테스트가 초기 상태를 만드는 용도라 Protocol 밖이다.
재현 가능한 테스트를 위해 Profile id 는 순번(profile_affinity-1)으로 만든다.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from uuid import UUID

from app.agents.curator.embedding.ports import (
    CuratorDomain,
    ObservationItem,
    ObservationKey,
    ProfileItem,
)


@dataclass(frozen=True)
class _StoredObservation:
    child_id: UUID
    status: str  # active / stand_alone / inactive
    item: ObservationItem


@dataclass(frozen=True)
class _StoredProfile:
    child_id: UUID
    last_observed_on: date
    item: ProfileItem


class InMemoryCuratorStore:
    """CuratorStore Protocol 구현. 넣은 순서를 저장된 순서(created_at)로 본다."""

    def __init__(self) -> None:
        self._observations: dict[ObservationKey, _StoredObservation] = {}
        self._profiles: dict[str, _StoredProfile] = {}
        self._profile_seq = 0

    # 테스트용 초기 상태
    def add_observation(
        self,
        *,
        child_id: UUID,
        domain: CuratorDomain,
        id: str,
        subject: str,
        polarity: int = 0,
        observed_on: date = date(2026, 9, 1),
        status: str = "active",
        embedding: list[float] | None = None,
        affinity_id: str | None = None,
    ) -> ObservationItem:
        item = ObservationItem(
            id=id,
            domain=domain,
            subject=subject,
            polarity=polarity,
            observed_on=observed_on,
            embedding=embedding,
            affinity_id=affinity_id,
        )
        self._observations[item.key] = _StoredObservation(child_id, status, item)
        return item

    def add_profile(
        self,
        *,
        child_id: UUID,
        domain: CuratorDomain,
        merge_key: str,
        polarity: int | None,
        embedding: list[float] | None,
        state: str = "candidate",
        last_observed_on: date = date(2026, 9, 1),
    ) -> ProfileItem:
        item = ProfileItem(
            id=self._next_profile_id(),
            domain=domain,
            polarity=polarity,
            merge_key=merge_key,
            embedding=embedding,
            state=state,
        )
        self._profiles[item.id] = _StoredProfile(child_id, last_observed_on, item)
        return item

    def observation(self, domain: CuratorDomain, id: str) -> ObservationItem:
        """테스트가 결과를 확인할 때 쓴다."""
        return self._observations[(domain, id)].item

    @property
    def profiles(self) -> list[ProfileItem]:
        return [stored.item for stored in self._profiles.values()]

    # CuratorStore
    async def list_unembedded(self, *, child_id: UUID) -> list[ObservationItem]:
        return [item for item in self._active(child_id) if item.embedding is None]

    async def save_embeddings(self, *, vectors: Mapping[ObservationKey, list[float]]) -> None:
        for key, vector in vectors.items():
            stored = self._observations[key]
            self._observations[key] = replace(
                stored, item=replace(stored.item, embedding=list(vector))
            )

    async def list_unlinked(self, *, child_id: UUID) -> list[ObservationItem]:
        return [
            item
            for item in self._active(child_id)
            if item.embedding is not None and item.affinity_id is None
        ]

    async def list_profiles(
        self, *, child_id: UUID, domain: CuratorDomain, polarity: int
    ) -> list[ProfileItem]:
        return [
            stored.item
            for stored in self._profiles.values()
            if stored.child_id == child_id
            and stored.item.domain == domain
            and stored.item.polarity == polarity
        ]

    async def link(self, *, domain: CuratorDomain, observation_id: str, affinity_id: str) -> None:
        key = (domain, observation_id)
        stored = self._observations[key]
        self._observations[key] = replace(
            stored, item=replace(stored.item, affinity_id=affinity_id)
        )

    async def create_profile(
        self,
        *,
        child_id: UUID,
        domain: CuratorDomain,
        polarity: int,
        merge_key: str,
        embedding: list[float],
        last_observed_on: date,
    ) -> ProfileItem:
        return self.add_profile(
            child_id=child_id,
            domain=domain,
            merge_key=merge_key,
            polarity=polarity,
            embedding=list(embedding),
            last_observed_on=last_observed_on,
        )

    def _active(self, child_id: UUID) -> list[ObservationItem]:
        return [
            stored.item
            for stored in self._observations.values()
            if stored.child_id == child_id and stored.status == "active"
        ]

    def _next_profile_id(self) -> str:
        self._profile_seq += 1
        return f"profile_affinity-{self._profile_seq}"
