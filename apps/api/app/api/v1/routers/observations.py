"""관찰 목록 · 상세 — #259 (07 기록 탭 · 기록 상세 시트).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 — 연결된 보호자가 아니면 403 (deps/child.py).

🚨 건강 관찰(`observation_health`)은 첫 배포 범위 밖이다 (#259). 목록에서 빼고 상세는 404 다.
   `domain=health` 는 오류가 아니라 빈 목록이다 — 화면의 분류 선택에 "건강" 이 있어서, 막으면
   기록이 없다는 안내 대신 실패 화면이 뜬다.
🚨 `growth` 는 표 이름이 아니다. education · routine 두 표를 읽는다 (루트 CLAUDE.md §5).
"""

import uuid
from typing import Literal

from fastapi import APIRouter

from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.quota import today_kst
from app.api.v1.list_query import ListQueryDep
from app.api.v1.observation_outs import to_observation_outs
from app.api.v1.schemas.observations import (
    CorrectionOut,
    ObservationDetailResponse,
    ObservationsResponse,
    UsedInOut,
)
from app.domains.correction.models import CorrectionTargetKind
from app.domains.correction.repository import list_corrections
from app.domains.memory.observation.models import ObservationStatus
from app.domains.memory.observation.repository import (
    ObservationDomain,
    find_observation,
    page_observations,
)
from app.domains.suggestion.repository import list_suggestions_using_observation

router = APIRouter()

AgentFilter = Literal["food", "activity", "growth", "health"]
StatusFilter = Literal["active", "stand_alone", "inactive"]

# 화면의 분류(Agent) → 읽을 관찰 표. health 는 범위 밖이라 빈 목록이다.
_DOMAINS_BY_AGENT: dict[str, tuple[ObservationDomain, ...]] = {
    "food": (ObservationDomain.FOOD,),
    "activity": (ObservationDomain.ACTIVITY,),
    "growth": (ObservationDomain.EDUCATION, ObservationDomain.ROUTINE),
    "health": (),
}
# 목록에 보이는 상태. 보호자가 고친 기록(이번만 · 잘못된 기록)도 남는다 — deleted 만 빠진다.
LISTED_STATUSES = (
    ObservationStatus.ACTIVE,
    ObservationStatus.STAND_ALONE,
    ObservationStatus.INACTIVE,
)
VISIBLE_DOMAINS = (
    ObservationDomain.FOOD,
    ObservationDomain.EDUCATION,
    ObservationDomain.ACTIVITY,
    ObservationDomain.ROUTINE,
)


@router.get(
    "/children/{cid}/observations",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403)},
)
async def list_observations(
    child: AccessibleChild,
    session: SessionDep,
    query: ListQueryDep,
    domain: AgentFilter | None = None,
    status: StatusFilter | None = None,
) -> ObservationsResponse:
    """deleted 가 아닌 관찰을 `observed_to` 최신순으로, `page` · `size` 쪽으로 나눠서.

    `date_from` · `date_to` 는 관찰 기간이 그 범위와 **겹치면** 포함한다(양 끝 날짜 포함).
    `q` 는 원문(`raw_text`)과 주제(`subject`)의 부분 일치다. 필터를 건 건수가 `total` 이다.
    status 를 주면 그 상태만, 주지 않으면 세 상태 모두다.
    """
    domains = _DOMAINS_BY_AGENT[domain] if domain else VISIBLE_DOMAINS
    if not domains:
        return ObservationsResponse(
            items=[], total=0, page=query.page, size=query.size, total_pages=0
        )

    page = await page_observations(
        session,
        child_id=child.child_id,
        domains=domains,
        statuses=(ObservationStatus(status),) if status else LISTED_STATUSES,
        date_from=query.date_from,
        date_to=query.date_to,
        q=query.q,
        offset=query.offset,
        limit=query.size,
    )
    items = await to_observation_outs(session, page.items, today=today_kst())
    return ObservationsResponse(
        items=items,
        total=page.total,
        page=query.page,
        size=query.size,
        total_pages=query.total_pages(page.total),
    )


@router.get(
    "/children/{cid}/observations/{kind}/{oid}",
    responses={status: {"model": ErrorEnvelope} for status in (403, 404)},
)
async def get_observation(
    child: AccessibleChild,
    kind: str,
    oid: uuid.UUID,
    session: SessionDep,
) -> ObservationDetailResponse:
    """관찰 1건 + 그 관찰을 근거로 쓴 추천 + 교정 이력."""
    domain = _visible_domain(kind)
    record = (
        await find_observation(session, domain=domain, child_id=child.child_id, observation_id=oid)
        if domain
        else None
    )
    if record is None:
        raise ApiError(404, "not_found", "그 기록을 찾지 못했어요")

    [observation] = await to_observation_outs(session, [record], today=today_kst())
    suggestions = await list_suggestions_using_observation(
        session, child_id=child.child_id, kind=kind, observation_id=oid
    )
    corrections = await list_corrections(
        session,
        child_id=child.child_id,
        target_kind=CorrectionTargetKind(kind),
        target_id=oid,
    )
    return ObservationDetailResponse(
        observation=observation,
        used_in=[
            UsedInOut(suggestion_id=str(s.id), content=s.content, status=s.status.value)
            for s in suggestions
        ],
        corrections=[
            CorrectionOut(id=str(c.id), verdict=c.verdict.value, created_at=c.created_at)
            for c in corrections
        ],
    )


def _visible_domain(kind: str) -> ObservationDomain | None:
    """경로의 kind 가 이 화면이 보여주는 표면 그 도메인. 건강 · 모르는 값은 None (→ 404)."""
    if not kind.startswith("observation_"):
        return None
    try:
        domain = ObservationDomain(kind.removeprefix("observation_"))
    except ValueError:
        return None
    return domain if domain in VISIBLE_DOMAINS else None
