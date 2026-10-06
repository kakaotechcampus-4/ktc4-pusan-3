"""교정 이력의 append-only 저장과 대상별 조회.

교정이 성향 재계산·제안 재산출에 미치는 효과는 아직 확정된 Curator 규칙이 없어
여기서 수행하지 않는다. 호출자는 대상 상태 갱신을 같은 트랜잭션에서 한다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.correction.models import Correction, CorrectionTargetKind, CorrectionVerdict
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
)
from app.domains.memory.profile.models import ProfileAffinity

_TARGET_MODELS = {
    CorrectionTargetKind.OBSERVATION_FOOD: ObservationFood,
    CorrectionTargetKind.OBSERVATION_HEALTH: ObservationHealth,
    CorrectionTargetKind.OBSERVATION_EDUCATION: ObservationEducation,
    CorrectionTargetKind.OBSERVATION_ACTIVITY: ObservationActivity,
    CorrectionTargetKind.OBSERVATION_ROUTINE: ObservationRoutine,
    CorrectionTargetKind.PROFILE_AFFINITY: ProfileAffinity,
}


async def list_corrections(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    target_kind: CorrectionTargetKind,
    target_id: uuid.UUID,
) -> list[Correction]:
    stmt = (
        select(Correction)
        .where(
            Correction.child_id == child_id,
            Correction.target_kind == CorrectionTargetKind(target_kind),
            Correction.target_id == target_id,
        )
        .order_by(Correction.created_at.desc(), Correction.id.desc())
    )
    return list((await session.scalars(stmt)).all())


async def append_correction(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    target_kind: CorrectionTargetKind,
    target_id: uuid.UUID,
    verdict: CorrectionVerdict,
    parent_id: uuid.UUID,
) -> Correction:
    """실재하는 같은 아이의 대상에만 이력을 남긴다. 상태 전이는 호출자가 묶어 처리한다."""
    target_kind = CorrectionTargetKind(target_kind)
    model = _TARGET_MODELS[target_kind]
    target_exists = await session.scalar(
        select(model.id).where(model.id == target_id, model.child_id == child_id)
    )
    if target_exists is None:
        raise ValueError("교정 대상이 없거나 다른 아이의 기록이다")
    row = Correction(
        child_id=child_id,
        target_kind=target_kind,
        target_id=target_id,
        verdict=CorrectionVerdict(verdict),
        created_by=parent_id,
    )
    session.add(row)
    await session.flush()
    return row
