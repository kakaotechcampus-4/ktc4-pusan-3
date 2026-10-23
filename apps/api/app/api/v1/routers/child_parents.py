"""함께 보는 보호자 — 목록 · 연결 끊기 (#336).

인증은 router.py 의 protected_router 가 건다. 등록 보호자(owner)는 child.owner_parent_id 로 가린다 —
parent_child 에는 역할 칸이 없다. 그래서 AccessibleChild 대신 초대 발행(invites.py)처럼
find_accessible_child 로 아이 행을 받아 owner 를 본다.
막는 조건은 같다(연결 없음 · 보관된 아이 → 403).

연결 끊기 — 누가 누구를 (재형님 결정 10-06)
- owner 는 함께 보는 보호자(member) 누구든 끊는다
- member 는 자기 자신만 끊는다(스스로 나가기)
- owner 는 끊는 대상이 아니다. 자기 자신이어도 — owner 가 떠나는 길은 탈퇴뿐이다

`Idempotency-Key` 는 받지 않는다. 다시 보내면 대상이 이미 연결되지 않아 404 다
(스스로 나간 경우는 아이 접근 확인에서 403).
"""

from uuid import UUID

from fastapi import APIRouter, Response

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.runs import pending_reply, suggestion_retry
from app.api.v1.schemas.child_parents import ChildParentOut, ChildParentsResponse
from app.domains.child.models import Child
from app.domains.child.parents import list_parents_with_nickname, unlink_parent
from app.domains.child.repository import find_accessible_child

router = APIRouter()


async def _child(session: SessionDep, *, cid: UUID, parent_id: UUID) -> Child:
    child = await find_accessible_child(session, child_id=cid, parent_id=parent_id)
    if child is None:
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    return child


@router.get(
    "/children/{cid}/parents",
    response_model=ChildParentsResponse,
    responses={403: {"model": ErrorEnvelope}},
)
async def list_parents(
    cid: UUID, parent: CurrentParent, session: SessionDep
) -> ChildParentsResponse:
    """이 아이에 연결된 보호자 전부. 연결된 보호자만 본다."""
    child = await _child(session, cid=cid, parent_id=parent.parent_id)
    rows = await list_parents_with_nickname(session, child_id=child.id)
    return ChildParentsResponse(
        parents=[
            ChildParentOut(
                parent_id=r.parent_id,
                nickname=r.nickname,
                relation=r.relation,
                role="owner" if r.parent_id == child.owner_parent_id else "member",
                connected_at=r.connected_at,
            )
            for r in rows
        ]
    )


@router.delete(
    "/children/{cid}/parents/{pid}",
    status_code=204,
    responses={status: {"model": ErrorEnvelope} for status in (403, 404, 409)},
)
async def unlink(cid: UUID, pid: UUID, parent: CurrentParent, session: SessionDep) -> Response:
    """연결 끊기 · 스스로 나가기. 그 보호자의 이 아이 일기 · 개인 알림을 함께 지운다.

    공동 기록(관찰 · 건강 · 사진 · 고치기 이력)과 그 "적은 사람" 칸은 남는다.
    """
    child = await _child(session, cid=cid, parent_id=parent.parent_id)
    # 판정 순서는 이슈 #336 표 그대로 — 대상 연결 여부(404)를 권한(409 · 403)보다 먼저 본다.
    if await find_accessible_child(session, child_id=child.id, parent_id=pid) is None:
        raise ApiError(404, "not_found", "그 보호자를 찾지 못했어요")
    if pid == child.owner_parent_id:
        raise ApiError(409, "owner_required", "아이를 만든 보호자는 연결을 끊을 수 없어요")
    if parent.parent_id != child.owner_parent_id and pid != parent.parent_id:
        raise ApiError(
            403, "owner_only", "아이를 등록한 보호자만 다른 보호자의 연결을 끊을 수 있어요"
        )
    # 위 확인과 이 사이에 다른 요청이 먼저 끊었으면 지울 행이 없다 — 같은 404 다.
    if not await unlink_parent(session, child_id=child.id, parent_id=pid):
        raise ApiError(404, "not_found", "그 보호자를 찾지 못했어요")
    await session.commit()
    # 원문이 든 메모리 보관함(되묻기 · 05 다시 시도)도 그 자리에서 비운다. 커밋 뒤여야 한다 (#561)
    for store in (pending_reply, suggestion_retry):
        store.forget_parent(parent_id=pid, child_id=child.id)
    return Response(status_code=204)
