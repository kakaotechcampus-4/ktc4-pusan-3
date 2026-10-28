"""관찰에서 파생된 성향의 API 조회·라벨 수정·상태 전이 집계."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.correction.models import Correction, CorrectionTargetKind, CorrectionVerdict
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.memory.search import contains_pattern
from app.rules.profile import (
    PROFILE_LIMIT_PER_DOMAIN,
    PROMOTION_WINDOW_DAYS,
    WRONG_COUNT_WINDOW_DAYS,
)

_KST = timezone(timedelta(hours=9))

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


@dataclass(frozen=True)
class AffinityPage:
    items: list[ProfileAffinity]
    total: int


def _has_active_observation():
    """묶인 active 관찰이 하나라도 있는 기억. 근거 없는 성향은 보여주지 않는다 (루트 CLAUDE.md §2).

    `list_active_refs_by_affinity` 가 세는 것과 같은 기준이다 — 세 표 · 같은 아이 · active.
    기억 한 건에 관찰이 여러 건 묶여도 한 번만 센다 (JOIN 이 아니라 EXISTS).
    """
    return or_(
        *(
            exists().where(
                model.affinity_id == ProfileAffinity.id,
                model.child_id == ProfileAffinity.child_id,
                model.status == ObservationStatus.ACTIVE,
            )
            for model in _PROMOTABLE_BY_DOMAIN.values()
        )
    )


async def page_affinities(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    domain: MemoryDomain | None = None,
    state: ProfileState | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> AffinityPage:
    """기억 목록 한 쪽 + 같은 필터의 전체 건수. 마지막 관찰이 최근인 것이 앞.

    `list_affinities` 와 달리 묶인 active 관찰이 없는 기억은 목록 · total 어디에도 없다 —
    쿼리 뒤에서 걸러내면 쪽마다 개수가 달라지고 total 이 부풀기 때문이다.
    `date_from` · `date_to` 는 `last_observed_on` 이 범위 안인 기억이다(양 끝 포함).
    `q` 는 이름(`merge_key`) 부분 일치다. 띄어쓰기와 대소문자는 무시한다 — "계란반찬" 으로
    "계란 반찬" 이 찾아진다 (Curator 가 이름을 비교하는 규칙과 같다).
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit은 1~100이어야 한다")
    if offset < 0:
        raise ValueError("offset은 0 이상이어야 한다")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from은 date_to보다 늦을 수 없다")

    conditions = [ProfileAffinity.child_id == child_id, _has_active_observation()]
    if domain is not None:
        conditions.append(ProfileAffinity.domain == MemoryDomain(domain))
    if state is not None:
        conditions.append(ProfileAffinity.state == ProfileState(state))
    else:
        conditions.append(ProfileAffinity.state != ProfileState.ARCHIVED)
    if date_from is not None:
        conditions.append(ProfileAffinity.last_observed_on >= date_from)
    if date_to is not None:
        conditions.append(ProfileAffinity.last_observed_on <= date_to)
    squashed = "".join((q or "").split())
    if squashed:
        conditions.append(
            func.regexp_replace(ProfileAffinity.merge_key, r"\s", "", "g").ilike(
                contains_pattern(squashed), escape="\\"
            )
        )

    total = await session.scalar(
        select(func.count()).select_from(ProfileAffinity).where(*conditions)
    )
    items = await session.scalars(
        select(ProfileAffinity)
        .where(*conditions)
        .order_by(ProfileAffinity.last_observed_on.desc(), ProfileAffinity.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return AffinityPage(items=list(items.all()), total=total or 0)


async def find_affinity(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    affinity_id: uuid.UUID,
    for_update: bool = False,
) -> ProfileAffinity | None:
    stmt = select(ProfileAffinity).where(
        ProfileAffinity.child_id == child_id, ProfileAffinity.id == affinity_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


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
    """승격 윈도우(PROMOTION_WINDOW_DAYS) 안의 active 관찰 일수 (= O).

    같은 날 같은 Profile 에 연결된 관찰 여러 건은 1일로 센다.
    §2 "한 번의 관찰을 성향으로 확정하지 않는다" — 입력 한 번에
    Memory Agent 가 관찰 여러 건을 만들어도 바로 승격되지 않게 한다.
    """
    model = _PROMOTABLE_BY_DOMAIN.get(domain)
    if model is None:
        return 0
    window_start = today - timedelta(days=PROMOTION_WINDOW_DAYS - 1)
    window_end = today + timedelta(days=1)  # 열린 상한
    stmt = (
        select(func.count(func.distinct(func.lower(model.observed_range))))
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
    window_start = datetime.combine(
        today - timedelta(days=WRONG_COUNT_WINDOW_DAYS - 1),
        datetime.min.time(),
        tzinfo=_KST,
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


async def get_latest_active_observed_on(
    session: AsyncSession,
    *,
    affinity_id: uuid.UUID,
    domain: MemoryDomain,
) -> date | None:
    """해당 profile 에 연결된 active 관찰 중 가장 최근 시작일을 반환한다."""
    model = _PROMOTABLE_BY_DOMAIN.get(domain)
    if model is None:
        return None
    stmt = (
        select(func.max(func.lower(model.observed_range)))
        .select_from(model)
        .where(
            model.affinity_id == affinity_id,
            model.status == ObservationStatus.ACTIVE,
        )
    )
    return await session.scalar(stmt)


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
    """confirmed + strength 내림차순. domain 을 넘기면 해당 도메인에서,
    넘기지 않으면 전체 도메인에서 합산 limit 개를 반환한다."""
    stmt = select(ProfileAffinity).where(
        ProfileAffinity.child_id == child_id,
        ProfileAffinity.state == ProfileState.CONFIRMED,
    )
    if domain is not None:
        stmt = stmt.where(ProfileAffinity.domain == MemoryDomain(domain))
    stmt = stmt.order_by(ProfileAffinity.strength.desc(), ProfileAffinity.id.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())
