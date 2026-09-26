"""프로필 상태 재계산과 correction 처리.

상태를 바꾸는 코드 경로는 이 모듈의 recompute_profile 하나뿐이다.
grep 으로 직접 `.state =` 대입이 이 함수 밖에 없어야 한다.
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.correction.models import CorrectionTargetKind, CorrectionVerdict
from app.domains.correction.repository import append_correction
from app.domains.memory.observation.models import ObservationStatus
from app.domains.memory.observation.repository import ObservationDomain, set_observation_status
from app.domains.memory.profile.models import ProfileAffinity
from app.domains.memory.profile.repository import (
    count_active_in_window,
    count_wrong_in_window,
    find_affinity,
    has_strong_signals,
)
from app.rules.profile import apply_correction, apply_transition, compute_profile_status

# observation domain → correction target_kind 매핑
_OBS_TO_TARGET: dict[str, CorrectionTargetKind] = {
    "food": CorrectionTargetKind.OBSERVATION_FOOD,
    "health": CorrectionTargetKind.OBSERVATION_HEALTH,
    "education": CorrectionTargetKind.OBSERVATION_EDUCATION,
    "activity": CorrectionTargetKind.OBSERVATION_ACTIVITY,
    "routine": CorrectionTargetKind.OBSERVATION_ROUTINE,
}

# observation verdict → observation status 매핑
_VERDICT_TO_OBS_STATUS: dict[str, ObservationStatus] = {
    "wrong": ObservationStatus.INACTIVE,
    "once_only": ObservationStatus.STAND_ALONE,
}


async def recompute_profile(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    today: date,
) -> ProfileAffinity:
    """O, W, G 를 수집하고 순수 함수로 상태·strength 를 갱신한다.

    멱등: 같은 입력으로 여러 번 호출해도 상태·strength 가 동일하다.
    전이 보너스는 상태가 실제로 바뀔 때만 1회 적용된다.
    """
    profile = await session.get(ProfileAffinity, profile_id)
    if profile is None:
        raise ValueError(f"프로필을 찾을 수 없다: {profile_id}")

    obs_count = await count_active_in_window(
        session, affinity_id=profile.id, domain=profile.domain, today=today,
    )
    wrong_count = await count_wrong_in_window(session, profile_id=profile.id, today=today)
    signal = await has_strong_signals(
        session, affinity_id=profile.id, domain=profile.domain, today=today,
    )

    prev_state = str(profile.state)
    next_state = compute_profile_status(
        obs_count=obs_count, wrong_count=wrong_count, has_signal=signal,
        last_observed_on=profile.last_observed_on, today=today,
    )

    if prev_state != next_state:
        profile.strength = apply_transition(profile.strength, prev_state, next_state)
        profile.state = next_state  # type: ignore[assignment]
        profile.last_transition_at = datetime.now(timezone.utc)
        profile.last_transition_from = prev_state

    await session.flush()
    return profile


async def handle_observation_correction(
    session: AsyncSession,
    *,
    domain: str,
    child_id: uuid.UUID,
    observation_id: uuid.UUID,
    verdict: str,
    parent_id: uuid.UUID,
    today: date,
) -> None:
    """observation correction → 상태 변경 → 이력 저장 → profile 재계산."""
    obs_status = _VERDICT_TO_OBS_STATUS.get(verdict)
    if obs_status is None:
        raise ValueError(f"observation 에 적용할 수 없는 verdict: {verdict!r}")

    target_kind = _OBS_TO_TARGET[domain]

    # 1. correction 이력 저장
    await append_correction(
        session,
        child_id=child_id,
        target_kind=target_kind,
        target_id=observation_id,
        verdict=CorrectionVerdict(verdict),
        parent_id=parent_id,
    )

    # 2. observation 상태 변경
    record = await set_observation_status(
        session,
        domain=ObservationDomain(domain),
        child_id=child_id,
        observation_id=observation_id,
        status=obs_status,
    )
    if record is None:
        raise ValueError("관찰을 찾을 수 없다")

    # 3. 해당 observation 에 연결된 profile 재계산
    affinity_id = record.fields.get("affinity_id")
    if affinity_id is not None:
        await recompute_profile(session, profile_id=affinity_id, today=today)


async def handle_profile_correction(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    profile_id: uuid.UUID,
    verdict: str,
    parent_id: uuid.UUID,
    today: date,
) -> None:
    """profile correction → strength 감소 → 이력 저장 → 재계산."""
    profile = await find_affinity(session, child_id=child_id, affinity_id=profile_id)
    if profile is None:
        raise ValueError("프로필을 찾을 수 없다")

    # 1. correction 이력 저장
    await append_correction(
        session,
        child_id=child_id,
        target_kind=CorrectionTargetKind.PROFILE_AFFINITY,
        target_id=profile_id,
        verdict=CorrectionVerdict(verdict),
        parent_id=parent_id,
    )

    # 2. strength 감소
    profile.strength = apply_correction(profile.strength, verdict)
    await session.flush()

    # 3. 재계산 (W 가 늘었으므로 상태가 바뀔 수 있다)
    await recompute_profile(session, profile_id=profile_id, today=today)
