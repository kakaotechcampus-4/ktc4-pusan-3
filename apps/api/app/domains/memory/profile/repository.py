"""관찰에서 파생된 성향의 API 조회·라벨 수정·상태 전이 집계."""

import uuid
from datetime import date, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.correction.models import Correction, CorrectionTargetKind, CorrectionVerdict
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.rules.profile import (
    DEMOTION_WINDOW_DAYS,
    PROFILE_LIMIT_PER_DOMAIN,
    PROMOTION_WINDOW_DAYS,
)

_PROMOTABLE_BY_DOMAIN = {
    MemoryDomain.FOOD: ObservationFood,
    MemoryDomain.ACTIVITY: ObservationActivity,
    MemoryDomain.EDUCATION: ObservationEducation,
}


async def list_affinities(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    domain: MemoryDomain | None = None,
    state: ProfileState | None = None,
) -> list[ProfileAffinity]:
    stmt = select(ProfileAffinity).where(ProfileAffinity.child_id == child_id)
    if domain is not None:
        stmt = stmt.where(ProfileAffinity.domain == MemoryDomain(domain))
    if state is not None:
        stmt = stmt.where(ProfileAffinity.state == ProfileState(state))
    else:
        stmt = stmt.where(ProfileAffinity.state != ProfileState.ARCHIVED)
    stmt = stmt.order_by(ProfileAffinity.last_observed_on.desc(), ProfileAffinity.id.desc())
    return list((await session.scalars(stmt)).all())


async def find_affinity(
    session: AsyncSession, *, child_id: uuid.UUID, affinity_id: uuid.UUID
) -> ProfileAffinity | None:
    return await session.scalar(
        select(ProfileAffinity).where(
            ProfileAffinity.child_id == child_id, ProfileAffinity.id == affinity_id
        )
    )


async def rename_affinity(
    session: AsyncSession, *, child_id: uuid.UUID, affinity_id: uuid.UUID, merge_key: str
) -> ProfileAffinity | None:
    return await session.scalar(
        update(ProfileAffinity)
        .where(ProfileAffinity.child_id == child_id, ProfileAffinity.id == affinity_id)
        .values(merge_key=merge_key)
        .returning(ProfileAffinity)
    )


# ---------------------------------------------------------------------------
# 상태 전이 집계 — recompute_profile 이 쓴다
# ---------------------------------------------------------------------------


async def count_active_in_window(
    session: AsyncSession,
    *,
    affinity_id: uuid.UUID,
    domain: MemoryDomain,
    today: date,
) -> int:
    """승격 윈도우(7일) 안의 active 관찰 건수 (= O)."""
    model = _PROMOTABLE_BY_DOMAIN.get(domain)
    if model is None:
        return 0
    window_start = today - timedelta(days=PROMOTION_WINDOW_DAYS - 1)
    window_end = today + timedelta(days=1)  # 열린 상한
    stmt = (
        select(func.count())
        .select_from(model)
        .where(
            model.affinity_id == affinity_id,
            model.status == ObservationStatus.ACTIVE,
            model.observed_range.op("&&")(func.daterange(window_start, window_end, "[)")),
        )
    )
    return (await session.scalar(stmt)) or 0


async def count_wrong_in_window(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    today: date,
) -> int:
    """하강 윈도우(21일) 안의 wrong correction 수 (= W)."""
    from datetime import datetime, timezone

    window_start = datetime.combine(
        today - timedelta(days=DEMOTION_WINDOW_DAYS), datetime.min.time(), tzinfo=timezone.utc,
    )
    stmt = (
        select(func.count())
        .select_from(Correction)
        .where(
            Correction.target_kind == CorrectionTargetKind.PROFILE_AFFINITY,
            Correction.target_id == profile_id,
            Correction.verdict == CorrectionVerdict.WRONG,
            Correction.created_at >= window_start,
        )
    )
    return (await session.scalar(stmt)) or 0


async def has_strong_signals(
    session: AsyncSession,
    *,
    affinity_id: uuid.UUID,
    domain: MemoryDomain,
    today: date,
) -> bool:
    """승격 윈도우 안의 active 관찰 중 strong_signals 가 있는지 (= G)."""
    model = _PROMOTABLE_BY_DOMAIN.get(domain)
    if model is None:
        return False
    window_start = today - timedelta(days=PROMOTION_WINDOW_DAYS - 1)
    window_end = today + timedelta(days=1)
    stmt = (
        select(func.count())
        .select_from(model)
        .where(
            model.affinity_id == affinity_id,
            model.status == ObservationStatus.ACTIVE,
            model.observed_range.op("&&")(func.daterange(window_start, window_end, "[)")),
            func.array_length(model.strong_signals, 1) > 0,
        )
    )
    return ((await session.scalar(stmt)) or 0) > 0


# ---------------------------------------------------------------------------
# 조회 필터 — Agent / 프론트가 쓴다
# ---------------------------------------------------------------------------


async def list_confirmed_profiles(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    domain: MemoryDomain | None = None,
    limit: int = PROFILE_LIMIT_PER_DOMAIN,
) -> list[ProfileAffinity]:
    """confirmed + strength 내림차순. domain 당 limit 제한."""
    stmt = select(ProfileAffinity).where(
        ProfileAffinity.child_id == child_id,
        ProfileAffinity.state == ProfileState.CONFIRMED,
    )
    if domain is not None:
        stmt = stmt.where(ProfileAffinity.domain == MemoryDomain(domain))
    stmt = stmt.order_by(ProfileAffinity.strength.desc(), ProfileAffinity.id.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())
