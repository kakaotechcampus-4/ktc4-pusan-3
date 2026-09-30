"""CuratorStore 의 DB 구현. 인메모리(inmemory.py)와 동일한 동작을 보장한다.

세션은 밖에서 받는다 — 트랜잭션 경계는 호출하는 쪽이 정한다.
link 와 clear_hold 는 같은 세션 안이므로 같은 트랜잭션에서 실행된다.
"""

import uuid
from collections.abc import Mapping
from datetime import date
from heapq import merge as heapmerge
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.curator.embedding.ports import (
    CURATOR_DOMAINS,
    CuratorDomain,
    ObservationItem,
    ObservationKey,
    ProfileItem,
)
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationLinkHold,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.rules.profile import STRENGTH_DEFAULT

_MODEL_BY_DOMAIN: dict[str, type] = {
    "food": ObservationFood,
    "activity": ObservationActivity,
    "education": ObservationEducation,
}

# observation_link_hold 의 FK 칸 이름
_HOLD_FK: dict[str, str] = {
    "food": "food_id",
    "activity": "activity_id",
    "education": "education_id",
}


class DbCuratorStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    async def list_unembedded(self, *, child_id: UUID) -> list[ObservationItem]:
        return await self._list(child_id, embedding_is_null=True, affinity_id_is_null=None)

    async def list_unlinked(self, *, child_id: UUID) -> list[ObservationItem]:
        return await self._list(child_id, embedding_is_null=False, affinity_id_is_null=True)

    async def _list(
        self,
        child_id: UUID,
        *,
        embedding_is_null: bool,
        affinity_id_is_null: bool | None,
    ) -> list[ObservationItem]:
        """도메인별 쿼리 → created_at 오름차순 병합."""
        per_domain: list[list[tuple]] = []
        for domain in CURATOR_DOMAINS:
            model = _MODEL_BY_DOMAIN[domain]
            stmt = (
                select(
                    model.id, model.subject, model.polarity,
                    func.lower(model.observed_range).label("observed_on"),
                    model.embedding, model.affinity_id, model.created_at,
                )
                .where(
                    model.child_id == child_id,
                    model.status == ObservationStatus.ACTIVE,
                )
                .order_by(model.created_at, model.id)
            )
            if embedding_is_null:
                stmt = stmt.where(model.embedding.is_(None))
            else:
                stmt = stmt.where(model.embedding.isnot(None))
            if affinity_id_is_null is True:
                stmt = stmt.where(model.affinity_id.is_(None))

            rows = (await self._session.execute(stmt)).all()
            per_domain.append([(domain, r) for r in rows])

        merged = list(heapmerge(
            *per_domain, key=lambda pair: (pair[1].created_at, str(pair[1].id)),
        ))
        return [
            ObservationItem(
                id=str(row.id),
                domain=domain,
                subject=row.subject,
                polarity=row.polarity,
                observed_on=row.observed_on,
                embedding=list(row.embedding) if row.embedding is not None else None,
                affinity_id=str(row.affinity_id) if row.affinity_id is not None else None,
            )
            for domain, row in merged
        ]

    async def list_profiles(
        self, *, child_id: UUID, domain: CuratorDomain, polarity: int
    ) -> list[ProfileItem]:
        stmt = (
            select(ProfileAffinity)
            .where(
                ProfileAffinity.child_id == child_id,
                ProfileAffinity.domain == MemoryDomain(domain),
                ProfileAffinity.polarity == polarity,
            )
            .order_by(ProfileAffinity.created_at, ProfileAffinity.id)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [
            ProfileItem(
                id=str(row.id),
                domain=domain,
                polarity=row.polarity,
                merge_key=row.merge_key,
                embedding=list(row.embedding) if row.embedding is not None else None,
                state=str(row.state),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 쓰기
    # ------------------------------------------------------------------

    async def save_embeddings(self, *, vectors: Mapping[ObservationKey, list[float]]) -> None:
        for (domain, obs_id), vec in vectors.items():
            model = _MODEL_BY_DOMAIN[domain]
            await self._session.execute(
                update(model).where(model.id == uuid.UUID(obs_id)).values(embedding=vec)
            )
        await self._session.flush()

    async def link(self, *, domain: CuratorDomain, observation_id: str, affinity_id: str) -> None:
        model = _MODEL_BY_DOMAIN[domain]
        await self._session.execute(
            update(model)
            .where(model.id == uuid.UUID(observation_id))
            .values(affinity_id=uuid.UUID(affinity_id))
        )
        await self._session.flush()

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
        profile = ProfileAffinity(
            child_id=child_id,
            domain=MemoryDomain(domain),
            polarity=polarity,
            merge_key=merge_key,
            embedding=embedding,
            state=ProfileState.CANDIDATE,
            strength=STRENGTH_DEFAULT,
            last_observed_on=last_observed_on,
        )
        self._session.add(profile)
        await self._session.flush()
        return ProfileItem(
            id=str(profile.id),
            domain=domain,
            polarity=profile.polarity,
            merge_key=profile.merge_key,
            embedding=list(profile.embedding) if profile.embedding is not None else None,
            state=str(profile.state),
        )

    # ------------------------------------------------------------------
    # 보류 기록
    # ------------------------------------------------------------------

    async def record_uncertain(
        self, *, domain: CuratorDomain, observation_id: str, subject_hash: str
    ) -> int:
        fk_col = _HOLD_FK[domain]
        obs_uuid = uuid.UUID(observation_id)

        # UPSERT: 있으면 갱신, 없으면 삽입
        stmt = (
            pg_insert(ObservationLinkHold)
            .values(**{fk_col: obs_uuid, "uncertain_count": 1, "subject_hash": subject_hash})
            .on_conflict_do_update(
                index_elements=[fk_col],
                set_={
                    "uncertain_count": func.coalesce(
                        # hash 같으면 +1, 다르면 1로 리셋
                        select(ObservationLinkHold.uncertain_count + 1)
                        .where(
                            getattr(ObservationLinkHold, fk_col) == obs_uuid,
                            ObservationLinkHold.subject_hash == subject_hash,
                        )
                        .correlate(ObservationLinkHold)
                        .scalar_subquery(),
                        1,
                    ),
                    "subject_hash": subject_hash,
                },
            )
            .returning(ObservationLinkHold.uncertain_count)
        )
        count = await self._session.scalar(stmt)
        await self._session.flush()
        return count  # type: ignore[return-value]

    async def clear_hold(self, *, domain: CuratorDomain, observation_id: str) -> None:
        fk_col = _HOLD_FK[domain]
        await self._session.execute(
            delete(ObservationLinkHold).where(
                getattr(ObservationLinkHold, fk_col) == uuid.UUID(observation_id)
            )
        )
        await self._session.flush()
