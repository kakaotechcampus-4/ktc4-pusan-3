"""기록 · 기억 고치기 — #259.

인증은 router.py 의 protected_router 가 건다. 경로에 아이 주소가 없고 child_id 가 본문에 있어서
`AccessibleChild` 를 못 쓴다 — 같은 확인(`find_accessible_child`)을 여기서 직접 한다.

🚨 상태는 여기서 쓰지 않는다. 기록 상태 · 기억 재계산은 `profile/service.py` 의 핸들러가 하고,
   핸들러는 flush 까지만 하므로 **commit 은 라우터가 한다** — 이력 · 상태 · 재계산이 한 묶음이다.
🚨 건강 기록(`observation_health`)은 첫 배포 범위 밖이라 받지 않는다 (#259).
"""

import uuid

from fastapi import APIRouter

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.quota import today_kst
from app.api.v1.routers.affinities import affinity_out
from app.api.v1.routers.observations import to_observation_outs
from app.api.v1.schemas.common import Ref
from app.api.v1.schemas.corrections import (
    Cascade,
    CorrectionMade,
    CorrectionRequest,
    CorrectionResponse,
)
from app.domains.child.repository import find_accessible_child
from app.domains.correction.models import (
    OBSERVATION_VERDICTS,
    PROFILE_VERDICTS,
    CorrectionTargetKind,
    CorrectionVerdict,
)
from app.domains.correction.repository import list_corrections
from app.domains.memory.observation.models import ObservationStatus
from app.domains.memory.observation.repository import (
    ObservationDomain,
    find_observation,
    list_active_refs_by_affinity,
)
from app.domains.memory.profile.repository import find_affinity
from app.domains.memory.profile.service import (
    handle_observation_correction,
    handle_profile_correction,
)

router = APIRouter()


@router.post(
    "/corrections",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 404)},
)
async def create_correction(
    body: CorrectionRequest, parent: CurrentParent, session: SessionDep
) -> CorrectionResponse:
    """기록 1건 또는 기억 1건을 고친다. 기록을 고치면 묶인 기억도 다시 계산된다."""
    child_id = _uuid(body.child_id)
    if await find_accessible_child(session, child_id=child_id, parent_id=parent.parent_id) is None:
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")

    kind = CorrectionTargetKind(body.target_ref.kind)
    verdict = CorrectionVerdict(body.verdict)
    allowed = (
        PROFILE_VERDICTS if kind is CorrectionTargetKind.PROFILE_AFFINITY else OBSERVATION_VERDICTS
    )
    if verdict not in allowed:
        raise ApiError(400, "validation_failed", "이 대상에는 쓸 수 없는 고치기예요")

    target_id = _uuid(body.target_ref.id)
    today = today_kst()
    if kind is CorrectionTargetKind.PROFILE_AFFINITY:
        await _check_affinity(session, child_id=child_id, affinity_id=target_id)
        await handle_profile_correction(
            session,
            child_id=child_id,
            profile_id=target_id,
            verdict=verdict.value,
            parent_id=parent.parent_id,
            today=today,
        )
        recomputed = [target_id]
    else:
        domain = ObservationDomain(kind.value.removeprefix("observation_"))
        record = await find_observation(
            session, domain=domain, child_id=child_id, observation_id=target_id, for_update=True
        )
        # 잠근 채로 상태를 본다 — 같은 기록에 동시에 온 요청은 앞 요청이 끝난 뒤 400 을 받는다.
        # 잠금 순서(기록 → 기억)는 Curator 와 같아서 서로 기다리다 멈추지 않는다.
        if record is None:
            raise ApiError(404, "not_found", "그 기록을 찾지 못했어요")
        if record.fields["status"] is not ObservationStatus.ACTIVE:
            raise ApiError(400, "validation_failed", "이미 고친 기록이에요")
        await handle_observation_correction(
            session,
            domain=domain.value,
            child_id=child_id,
            observation_id=target_id,
            verdict=verdict.value,
            parent_id=parent.parent_id,
            today=today,
        )
        affinity_id = record.fields.get("affinity_id")
        recomputed = [affinity_id] if affinity_id else []

    [made] = (
        await list_corrections(session, child_id=child_id, target_kind=kind, target_id=target_id)
    )[:1]
    target = await _target_out(
        session, kind=kind, child_id=child_id, target_id=target_id, today=today
    )
    await session.commit()
    return CorrectionResponse(
        correction=CorrectionMade(
            id=str(made.id), verdict=made.verdict.value, created_at=made.created_at
        ),
        target=target,
        cascade=Cascade(
            affinities_recomputed=[Ref(kind="profile_affinity", id=str(a)) for a in recomputed],
            suggestions_recalculated=[],
        ),
    )


async def _check_affinity(
    session: SessionDep, *, child_id: uuid.UUID, affinity_id: uuid.UUID
) -> None:
    """목록에 보이는 기억만 고칠 수 있다 — 묶인 active 기록이 없는 기억은 화면에 없다."""
    row = await find_affinity(session, child_id=child_id, affinity_id=affinity_id)
    refs = await list_active_refs_by_affinity(
        session, child_id=child_id, affinity_ids=[affinity_id]
    )
    if row is None or not refs.get(affinity_id):
        raise ApiError(404, "not_found", "그 기억을 찾지 못했어요")


async def _target_out(
    session: SessionDep,
    *,
    kind: CorrectionTargetKind,
    child_id: uuid.UUID,
    target_id: uuid.UUID,
    today,
):
    if kind is CorrectionTargetKind.PROFILE_AFFINITY:
        row = await find_affinity(session, child_id=child_id, affinity_id=target_id)
        refs = await list_active_refs_by_affinity(
            session, child_id=child_id, affinity_ids=[target_id]
        )
        # 기억 고치기는 기록을 바꾸지 않아 근거는 그대로다 (_check_affinity 가 1건 이상을 확인했다)
        return affinity_out(row, refs[target_id], today=today)
    record = await find_observation(
        session,
        domain=ObservationDomain(kind.value.removeprefix("observation_")),
        child_id=child_id,
        observation_id=target_id,
    )
    [out] = await to_observation_outs(session, [record], today=today)
    return out


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(400, "validation_failed", "요청 값이 올바르지 않아요") from exc
