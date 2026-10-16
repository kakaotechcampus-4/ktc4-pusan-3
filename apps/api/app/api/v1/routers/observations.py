"""관찰 목록 · 상세 — #259 (07 기록 탭 · 기록 상세 시트).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 — 연결된 보호자가 아니면 403 (deps/child.py).

🚨 건강 관찰(`observation_health`)은 첫 배포 범위 밖이다 (#259). 목록에서 빼고 상세는 404 다.
   `domain=health` 는 오류가 아니라 빈 목록이다 — 화면의 분류 선택에 "건강" 이 있어서, 막으면
   기록이 없다는 안내 대신 실패 화면이 뜬다.
🚨 `growth` 는 표 이름이 아니다. education · routine 두 표를 읽는다 (루트 CLAUDE.md §5).
"""

import base64
import binascii
import json
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.quota import today_kst
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
    ObservationCursor,
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
    domain: AgentFilter | None = None,
    status: StatusFilter | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ObservationsResponse:
    """deleted 가 아닌 관찰을 `observed_to` 최신순으로. 필터를 건 건수가 `total` 이다.

    status 를 주면 그 상태만, 주지 않으면 세 상태 모두다.
    """
    domains = _DOMAINS_BY_AGENT[domain] if domain else VISIBLE_DOMAINS
    if not domains:
        return ObservationsResponse(items=[], next_cursor=None, total=0)

    page = await page_observations(
        session,
        child_id=child.child_id,
        domains=domains,
        statuses=(ObservationStatus(status),) if status else LISTED_STATUSES,
        cursor=_decode_cursor(cursor) if cursor else None,
        limit=limit,
    )
    items = await to_observation_outs(session, page.items, today=today_kst())
    return ObservationsResponse(
        items=items,
        next_cursor=_encode_cursor(page.next_cursor) if page.next_cursor else None,
        total=page.total,
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


def _encode_cursor(cursor: ObservationCursor) -> str:
    raw = json.dumps(
        [cursor.observed_to_exclusive.isoformat(), cursor.domain.value, str(cursor.id)]
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> ObservationCursor:
    """깨진 커서는 400. 화면이 받은 값을 그대로 돌려주는 한 일어나지 않는다.

    세 칸이 문자열인지 먼저 본다 — `uuid.UUID(1)` 은 ValueError 가 아니라 AttributeError 를
    던져서, 형만 틀린 커서가 500 으로 새어 나갔다 (#259 리뷰).
    """
    try:
        padded = value + "=" * (-len(value) % 4)
        parts = json.loads(base64.urlsafe_b64decode(padded))
        if not (
            isinstance(parts, list) and len(parts) == 3 and all(isinstance(p, str) for p in parts)
        ):
            raise ValueError("커서는 문자열 세 칸이다")
        upper, domain, oid = parts
        return ObservationCursor(
            observed_to_exclusive=date.fromisoformat(upper),
            domain=ObservationDomain(domain),
            id=uuid.UUID(oid),
        )
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ApiError(400, "validation_failed", "목록 위치가 올바르지 않아요") from exc
