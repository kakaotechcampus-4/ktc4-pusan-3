"""Curator 임베딩 파트의 저장소 계약. 구현체는 이 Protocol 만 만족하면 된다.

관찰과 Profile 을 읽고, 벡터·연결·새 candidate 만 쓴다.
Profile 의 state · strength 는 쓰지 않는다 — 승격·감쇠 파트의 값이다.

조회 조건을 둘로 나눈다.
    임베딩 대상  active · embedding 없음
    연결 대상    active · embedding 있음 · affinity_id 없음
임베딩은 됐는데 연결 전에 멈춘 관찰은 다음 실행에서 연결만 다시 한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol
from uuid import UUID

# 승격 대상 도메인. health 는 승격 파이프라인 밖, routine 은 승격 대상에서 제외 (PR #138)
CuratorDomain = Literal["food", "activity", "education"]
CURATOR_DOMAINS: tuple[CuratorDomain, ...] = ("food", "activity", "education")

# 관찰 하나를 가리키는 키. 도메인마다 테이블이 달라 id 만으로는 못 찾는다
ObservationKey = tuple[CuratorDomain, str]


@dataclass(frozen=True)
class ObservationItem:
    """Curator 가 보는 관찰 한 건. 원문(raw_text)은 싣지 않는다."""

    id: str
    domain: CuratorDomain
    subject: str
    polarity: int  # 1 좋아함 / 0 중립 / -1 싫어함
    observed_on: date
    embedding: list[float] | None
    affinity_id: str | None

    @property
    def key(self) -> ObservationKey:
        return (self.domain, self.id)


@dataclass(frozen=True)
class ProfileItem:
    """연결 후보로 보는 Profile 한 건."""

    id: str
    domain: CuratorDomain
    polarity: int | None
    merge_key: str
    embedding: list[float] | None
    state: str  # candidate / confirmed / archived. 읽기만 한다


class CuratorStore(Protocol):
    """전부 async. child_id 로 범위를 먼저 좁힌다."""

    async def list_unembedded(self, *, child_id: UUID) -> list[ObservationItem]:
        """임베딩 대상. 저장된 순서(created_at 오름차순)로 돌려준다."""
        ...

    async def save_embeddings(self, *, vectors: Mapping[ObservationKey, list[float]]) -> None:
        """관찰마다 벡터를 저장한다."""
        ...

    async def list_unlinked(self, *, child_id: UUID) -> list[ObservationItem]:
        """연결 대상. 저장된 순서(created_at 오름차순)로 돌려준다.

        먼저 저장된 관찰의 subject 가 새 Profile 의 merge_key 가 된다.
        observed_on 순이 아니다 — "지난주에 ~했어" 처럼 나중에 적힌 과거 관찰이 있다.
        """
        ...

    async def list_profiles(
        self, *, child_id: UUID, domain: CuratorDomain, polarity: int
    ) -> list[ProfileItem]:
        """같은 아이 · 도메인 · polarity 의 Profile. archived 도 포함한다."""
        ...

    async def link(self, *, domain: CuratorDomain, observation_id: str, affinity_id: str) -> None:
        """관찰의 affinity_id 를 채운다."""
        ...

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
        """새 candidate Profile. strength 는 기본값(0.3) 그대로 둔다."""
        ...
