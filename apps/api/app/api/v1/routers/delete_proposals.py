"""삭제 제안 확정 · 취소 (#535).

`POST /children/{cid}/delete-proposals/{pid}/confirm` · `…/cancel`.

대화로 "지워줘" 하면 Memory 는 지우지 않고 제안을 남긴다. 보호자가 카드에서 체크를 고쳐
[N건 삭제하기] → 한 번 더 확인을 누르면 여기로 온다. 되돌릴 수 없는 삭제를 사람이 승인하는
자리다 (루트 §2 · 이슈 #535 "승인 게이트로서의 의미").

- 인증은 router.py 의 protected_router, 아이 주소는 `AccessibleChild` 가 본문보다 먼저 확인한다.
  연결이 끊긴 보호자는 자기 제안이라도 403 이다.
- **제안한 보호자만** 누른다. 대화는 보호자마다 따로라 다른 보호자에게는 카드가 없다 — 남의 제안은
  없는 것과 같은 404 다.
- 🚨 지우는 것은 **후보 안의 체크한 것만**이다. 하나라도 후보 밖이면 422 이고 아무것도 안
  지운다.
  지우기는 AI 와 같은 store 함수를 쓴다 — 관찰은 soft delete 와 Profile 재계산, 일정은 준비물까지.
  store 가 아이로 다시 거르므로 다른 아이의 행은 지워지지 않는다(세지도 않는다).
- 🚨 만료(24시간) · 이미 끝난 제안으로는 지울 수 없다 (409). 행을 잡고(FOR UPDATE) 보므로 두 번 눌러
  동시에 와도 한 번만 지운다.
- 확정은 Idempotency-Key 가 필수다. 같은 키로 다시 오면 처음 응답을 그대로 돌려준다.
- 관찰을 지웠으면 commit 뒤 Curator 를 띄운다 — 대화 run 이 저장 뒤 띄우는 것과 같다.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.api import idempotency
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.quota import today_kst
from app.api.runs.runner import curator_trigger
from app.api.v1.schemas.delete_proposals import (
    ConfirmDeleteProposalRequest,
    DeleteProposalOut,
    DeleteProposalResponse,
)
from app.domains.delete_proposal.models import DeleteProposal, DeleteProposalStatus
from app.domains.delete_proposal.repository import (
    candidates_of,
    effective_status,
    find_proposal,
    finish,
)
from app.domains.memory.store.db_store import DbMemoryStore

router = APIRouter()

_ERRORS = {status: {"model": ErrorEnvelope} for status in (400, 403, 404, 409, 422)}


def _out(row: DeleteProposal, now: datetime) -> DeleteProposalOut:
    return DeleteProposalOut(
        id=row.id,
        status=effective_status(row, now),
        expires_at=row.expires_at,
        result=row.result,
    )


async def _pending(session, child: AccessibleChild, parent: CurrentParent, pid: UUID, now):
    """잡은 제안. 없으면 404, 끝났거나 만료면 409."""
    row = await find_proposal(
        session,
        proposal_id=pid,
        parent_id=parent.parent_id,
        child_id=child.child_id,
        for_update=True,
    )
    if row is None:
        raise ApiError(404, "not_found", "삭제 확인을 찾지 못했어요")
    state = effective_status(row, now)
    if state == "expired":
        raise ApiError(409, "proposal_expired", "시간이 지나 삭제 확인이 닫혔어요")
    if state != "pending":
        raise ApiError(409, "proposal_closed", "이미 끝난 삭제 확인이에요")
    return row


async def _delete(store: DbMemoryStore, kind: str, oid: str) -> bool:
    if kind == "event":
        return await store.delete_event(event_id=oid)
    return await store.delete_observation(
        domain=kind.removeprefix("observation_"), observation_id=oid
    )


@router.post(
    "/children/{cid}/delete-proposals/{pid}/confirm",
    response_model=DeleteProposalResponse,
    responses=_ERRORS,
)
async def confirm_delete_proposal(
    child: AccessibleChild,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    pid: UUID,
    body: ConfirmDeleteProposalRequest,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """체크한 후보를 지운다.

    오류 (순서대로): 403 child_access_denied → 400 validation_failed(본문) →
    400 idempotency_key_required → 404 not_found(없거나 남의 제안) → 409 proposal_expired ·
    proposal_closed → 422 not_in_proposal(후보 밖 id).
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
    replayed = idempotency.recall(**scope)
    if isinstance(replayed, dict):
        return JSONResponse(status_code=200, content=replayed)

    now = datetime.now(UTC)
    row = await _pending(session, child, parent, pid, now)
    kind_of = {oid: kind for kind, oid in candidates_of(row)}
    checked = list(dict.fromkeys(body.ids))  # 순서를 지키며 중복만 뺀다
    outside = [oid for oid in checked if oid not in kind_of]
    if outside:
        raise ApiError(422, "not_in_proposal", "삭제 확인에 없던 기록이 섞여 있어요")

    store = DbMemoryStore(session, child_id=child.child_id, today=today_kst())
    result: dict[str, int] = {}
    for oid in checked:
        kind = kind_of[oid]
        # 그사이 지워진 행(다른 보호자 · 다른 대화)은 False — 세지 않고 넘어간다
        if await _delete(store, kind, oid):
            result[kind] = result.get(kind, 0) + 1
    await finish(session, row, status=DeleteProposalStatus.DELETED, result=result, now=now)
    response = DeleteProposalResponse(proposal=_out(row, now)).model_dump(mode="json")
    await session.commit()
    idempotency.remember(**scope, replay=response)
    if any(kind != "event" for kind in result):
        # commit 뒤에만 — 같은 트랜잭션에서 띄우면 Curator 실패가 삭제까지 되돌린다
        trigger = curator_trigger()
        if trigger is not None:
            trigger(child.child_id, today_kst())
    return JSONResponse(status_code=200, content=response)


@router.post(
    "/children/{cid}/delete-proposals/{pid}/cancel",
    response_model=DeleteProposalResponse,
    responses=_ERRORS,
)
async def cancel_delete_proposal(
    child: AccessibleChild, parent: CurrentParent, session: SessionDep, pid: UUID
) -> DeleteProposalResponse:
    """아무것도 지우지 않고 닫는다.

    Idempotency-Key 를 받지 않는다 — 지우지 않아서 다시 보내도 잃는 것이 없다. 이미 취소한 제안을
    다시 취소하면 같은 응답을 돌려준다. 오류: 403 child_access_denied → 404 not_found →
    409 proposal_expired · proposal_closed(이미 지운 제안).
    """
    now = datetime.now(UTC)
    row = await find_proposal(
        session, proposal_id=pid, parent_id=parent.parent_id, child_id=child.child_id
    )
    if row is not None and row.status is DeleteProposalStatus.CANCELLED:
        return DeleteProposalResponse(proposal=_out(row, now))
    row = await _pending(session, child, parent, pid, now)
    await finish(session, row, status=DeleteProposalStatus.CANCELLED, result=None, now=now)
    out = DeleteProposalResponse(proposal=_out(row, now))
    await session.commit()
    return out
