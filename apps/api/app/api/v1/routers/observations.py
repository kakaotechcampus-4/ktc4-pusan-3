"""관찰 목록 · 상세 · 삭제 — #259 · #736 (07 기록 탭 · 기록 상세 시트).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 — 연결된 보호자가 아니면 403 (deps/child.py).

🚨 건강 관찰(`observation_health`)은 첫 배포 범위 밖이다 (#259). 목록에서 빼고 상세는 404 다.
   `domain=health` 는 오류가 아니라 빈 목록이다 — 화면의 분류 선택에 "건강" 이 있어서, 막으면
   기록이 없다는 안내 대신 실패 화면이 뜬다.
   삭제는 건강도 받는다 — 대화 삭제(#535)가 이미 받고, §2 가 막는 것은 LLM 이 건강 정보를 만들거나
   고치는 일이지 보호자가 직접 지우는 일이 아니다 (#736).
🚨 `growth` 는 표 이름이 아니다. education · routine 두 표를 읽는다 (루트 CLAUDE.md §5).
"""

import logging
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request, Response

from app.api import idempotency
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.quota import today_kst
from app.api.runs.runner import curator_trigger
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
from app.domains.memory.profile.service import lock_child_memory
from app.domains.memory.store.db_store import DbMemoryStore
from app.domains.suggestion.repository import list_suggestions_using_observation

log = logging.getLogger(__name__)

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


_DELETED_REPLAY = {"status_code": 204}
"""같은 키 재생값. 204 는 본문이 없어서 "지웠다" 는 표시만 적어 둔다 — 값은 보지 않는다."""


@router.delete(
    "/children/{cid}/observations/{kind}/{oid}",
    status_code=204,
    # 503 temporarily_unavailable — Redis 에 닿지 않으면 지우기 전에 막는다 (#671)
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 404, 503)},
)
async def delete_observation(
    child: AccessibleChild,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    kind: str,
    oid: uuid.UUID,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response:
    """기록 1건을 지운다 — 기록 탭의 "삭제" (#736).

    대화 삭제(#535)와 같은 store 함수로 지운다. 상태는 `deleted`, 검색 벡터는 비우고, Curator 보류
    기록을 정리하고, 묶인 기억을 다시 계산한다. 행과 원문은 서버에 남는다(soft delete).
    아이와 연결된 보호자면 owner · member 모두 지운다 — 기록 탭은 함께 보는 공동 기록이다.
    `deleted` 가 아닌 기록은 상태와 상관없이 지운다(고친 기록 `stand_alone` · `inactive` 포함).
    되돌릴 수 없어서 Idempotency-Key 가 필수다. 같은 키로 다시 오면 처음처럼 204 다.

    오류 (순서대로): 403 child_access_denied → 400 idempotency_key_required →
    503 temporarily_unavailable(Redis 장애 — 지우기 전에 막는다) →
    404 not_found(없는 종류 · 없는 기록 · 이미 지운 기록 · 이 아이의 기록이 아님).
    """
    if not idempotency_key:
        raise ApiError(
            400, "idempotency_key_required", "요청을 처리할 수 없어요. 다시 시도해 주세요"
        )
    scope = {
        "parent_id": parent.parent_id,
        "method": request.method,
        "path": request.url.path,
        "key": idempotency_key,
    }
    # 이미 지운 기록은 다시 지우면 404 라서 재생은 지우기 전에 본다
    if await idempotency.recall(**scope) is not None:
        return Response(status_code=204)
    domain = _deletable_domain(kind)
    if domain is None:
        raise ApiError(404, "not_found", "그 기록을 찾지 못했어요")

    # 🚨 아이 잠금이 먼저다 — store 와 같은 잠금이라 아이 잠금 → 관찰 → 기억 순서가 된다.
    #    `/corrections` 처럼 기록 행부터 잠그지 않는다 (#636 · #736)
    await lock_child_memory(session, child.child_id)
    # 같은 키가 동시에 두 번 왔으면 뒤의 것은 앞의 것이 끝날 때까지 잠금에서 기다렸다 — 그 사이
    # 앞의 것이 기억해 둔 응답을 다시 본다. 안 보면 지운 것은 맞는데 화면에 404 가 뜬다
    if await idempotency.recall(**scope) is not None:
        return Response(status_code=204)

    today = today_kst()  # 기억 재계산과 Curator 가 같은 기준일을 쓴다
    store = DbMemoryStore(session, child_id=child.child_id, today=today)
    if not await store.delete_observation(domain=domain, observation_id=str(oid)):
        raise ApiError(404, "not_found", "그 기록을 찾지 못했어요")
    # 🚨 빠뜨리면 지운 기록이 기억 강도에 그대로 남는다 — 계산은 commit 직전에 몰아서 한다 (#717)
    await store.flush_profiles()
    await session.commit()
    await idempotency.remember(**scope, replay=_DELETED_REPLAY)
    _trigger_curator(child.child_id, today)
    return Response(status_code=204)


def _trigger_curator(child_id: uuid.UUID, today: date) -> None:
    """commit 뒤에만 부른다 — 같은 트랜잭션에서 띄우면 Curator 실패가 삭제까지 되돌린다.

    🚨 터져도 응답은 성공이다 — 지운 것은 이미 commit 됐다 (delete_proposals 와 같다).
    """
    try:
        trigger = curator_trigger()
        if trigger is not None:
            trigger(child_id, today)
    except Exception as exc:
        log.error("기록 삭제 뒤 curator trigger 실패 error=%s", type(exc).__name__)


def _deletable_domain(kind: str) -> ObservationDomain | None:
    """경로의 kind 가 관찰 표면 그 도메인. 다섯 종류 모두(건강 포함). 모르는 값은 None (→ 404)."""
    if not kind.startswith("observation_"):
        return None
    try:
        return ObservationDomain(kind.removeprefix("observation_"))
    except ValueError:
        return None


def _visible_domain(kind: str) -> ObservationDomain | None:
    """경로의 kind 가 이 화면이 보여주는 표면 그 도메인. 건강 · 모르는 값은 None (→ 404)."""
    domain = _deletable_domain(kind)
    return domain if domain in VISIBLE_DOMAINS else None
