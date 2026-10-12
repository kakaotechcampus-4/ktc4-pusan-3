"""기억 목록 — #259 (기억 탭).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 — 연결된 보호자가 아니면 403 (deps/child.py).

🚨 `growth` 는 표 이름이 아니다 (루트 CLAUDE.md §5). 기억 표에서는 education 이다 — routine 은
   승격 대상이 아니라 기억이 없다. `health` 도 기억이 없어서 빈 목록이다.
🚨 상태(state)를 여기서 다시 계산하지 않는다. 상태를 바꾸는 경로는 `recompute_profile` 하나다.
🚨 묶인 active 기록이 0개인 기억은 목록에서 뺀다 — 근거 없는 성향을 보여주지 않는다.
"""

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter

from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ErrorEnvelope
from app.api.quota import today_kst
from app.api.v1.schemas.affinities import AffinitiesResponse, AffinityOut
from app.api.v1.schemas.common import Ref
from app.domains.memory.observation.repository import (
    ObservationDomain,
    list_active_refs_by_affinity,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.memory.profile.repository import list_affinities
from app.rules.affinity_reason import is_stale, state_reason

router = APIRouter()

# 화면의 분류(Agent) → 기억 표의 domain. health 는 기억이 없다.
_MEMORY_DOMAIN_BY_AGENT: dict[str, MemoryDomain | None] = {
    "food": MemoryDomain.FOOD,
    "activity": MemoryDomain.ACTIVITY,
    "growth": MemoryDomain.EDUCATION,
    "health": None,
}
_AGENT_BY_MEMORY_DOMAIN = {
    MemoryDomain.FOOD: "food",
    MemoryDomain.ACTIVITY: "activity",
    MemoryDomain.EDUCATION: "growth",
}


@router.get(
    "/children/{cid}/affinities",
    responses={403: {"model": ErrorEnvelope}},
)
async def get_affinities(
    child: AccessibleChild,
    session: SessionDep,
    domain: Literal["food", "activity", "growth", "health"] | None = None,
    state: Literal["confirmed", "candidate"] | None = None,
) -> AffinitiesResponse:
    """기억 목록. state 를 주지 않으면 archived 를 뺀 전부. 마지막 관찰이 최근인 것이 앞."""
    memory_domain = _MEMORY_DOMAIN_BY_AGENT[domain] if domain else None
    if domain and memory_domain is None:
        return AffinitiesResponse(affinities=[])

    rows = await list_affinities(
        session,
        child_id=child.child_id,
        domain=memory_domain,
        state=ProfileState(state) if state else None,
    )
    refs = await list_active_refs_by_affinity(
        session, child_id=child.child_id, affinity_ids=[row.id for row in rows]
    )
    today = today_kst()
    # 묶인 기록이 모두 교정되면 기억은 후보로 남지만 근거가 없다. 근거 없는 것을 성향으로
    # 보여주지 않는다 (루트 CLAUDE.md §2). 상태는 바꾸지 않고 목록에서만 뺀다.
    affinities = [affinity_out(row, refs[row.id], today=today) for row in rows if refs.get(row.id)]
    return AffinitiesResponse(affinities=affinities)


def affinity_out(
    row: ProfileAffinity, linked: list[tuple[ObservationDomain, uuid.UUID]], *, today: date
) -> AffinityOut:
    """기억 1건의 응답. 고치기 응답(`POST /corrections`)도 같은 모양을 쓴다. linked 는 1건 이상."""
    return AffinityOut(
        id=str(row.id),
        merge_key=row.merge_key,
        domain=_AGENT_BY_MEMORY_DOMAIN[row.domain],
        state=row.state.value,
        polarity=row.polarity,
        strength=row.strength,
        last_observed_on=row.last_observed_on,
        observation_count=len(linked),
        state_reason=state_reason(
            observation_count=len(linked), last_observed_on=row.last_observed_on, today=today
        ),
        is_stale=is_stale(row.last_observed_on, today=today),
        source_refs=[Ref(kind=f"observation_{d.value}", id=str(oid)) for d, oid in linked],
    )
