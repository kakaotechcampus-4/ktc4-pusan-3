"""프로필 상태 재계산과 correction 처리.

상태를 바꾸는 코드 경로는 이 모듈의 recompute_profile 하나뿐이다.
grep 으로 직접 `.state =` 대입이 이 함수 밖에 없어야 한다.

호출 시점:
  - correction 핸들러 (handle_observation_correction, handle_profile_correction)
  - Curator 연결 후 (curator/recompute.recompute_after_linking)
TODO(#149-followup): 시간 경과에 의한 상태 변화(21일 무관찰 → archived)를
  반영하는 배치 호출이 아직 없다. Agent 가 DB 의 state 를 그대로 읽으므로
  오래 전에 confirmed 된 성향이 배치 전까지 개인화 근거로 남을 수 있다.
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.correction.models import CorrectionTargetKind, CorrectionVerdict
from app.domains.correction.repository import append_correction
from app.domains.memory.observation.models import ObservationStatus
from app.domains.memory.observation.repository import ObservationDomain, set_observation_status
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
from app.domains.memory.profile.repository import (
    count_active_in_window,
    count_wrong_in_window,
    find_affinity,
    get_latest_active_observed_on,
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

    # 2.5. Curator 보류 기록(hold) 정리 — 교정된 관찰은 다시 연결 대상이 안 되므로
    from sqlalchemy import delete

    from app.domains.memory.observation.models import ObservationLinkHold

    await session.execute(
        delete(ObservationLinkHold).where(
            ObservationLinkHold.domain == domain,
            ObservationLinkHold.observation_id == observation_id,
        )
    )

    # 3. last_observed_on 재계산 — 교정으로 최신 관찰이 빠졌을 수 있다
    affinity_id = record.fields.get("affinity_id")
    if affinity_id is not None:
        latest = await get_latest_active_observed_on(
            session, affinity_id=affinity_id, domain=MemoryDomain(domain),
        )
        if latest is not None:
            profile = await session.get(ProfileAffinity, affinity_id)
            if profile is not None:
                profile.last_observed_on = latest
                await session.flush()

        # 4. profile 상태 재계산
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
    profile = await find_affinity(
        session, child_id=child_id, affinity_id=profile_id, for_update=True,
    )
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
