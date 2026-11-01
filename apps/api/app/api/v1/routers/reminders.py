"""내 일정 알림 두기 · 지우기 — #594.

일정을 만든 보호자는 제출(`POST /children/{cid}/events`)에서 알림을 함께 건다. 함께 보는 다른
보호자는 캘린더의 그 일정에서 여기로 각자 건다. 알림은 보호자 개인 것이라 남의 알림은 바꾸지 않는다.

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 — 연결된 보호자가 아니면 403 (deps/child.py). PUT 은 그 아이의 일정이 아니면 404 이고,
DELETE 는 지울 알림이 없으면(남의 아이 일정 · 없는 일정 포함) 그냥 204 다 — 멱등이라서.

알림은 일정 쓰기가 아니라 개인 설정이라 승인 게이트가 아니다 (루트 CLAUDE.md §2 — 게이트를
늘리지 않는다). 최종 상태를 보내는 요청이라 Idempotency-Key 를 받지 않는다 — 같은 값을 여러 번
보내도 결과가 같다.
"""

import uuid

from fastapi import APIRouter, Response

from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.reminder_check import check_reminder
from app.api.v1.schemas.reminders import ReminderIn, ReminderOut, ReminderResponse
from app.domains.schedule.repository import delete_reminder, find_event, lock_link, put_reminder

router = APIRouter()


@router.put(
    "/children/{cid}/events/{eid}/reminder",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 404, 422)},
)
async def put_my_reminder(
    child: AccessibleChild,
    parent: CurrentParent,
    session: SessionDep,
    eid: uuid.UUID,
    body: ReminderIn,
) -> ReminderResponse:
    """이 일정에 내 알림을 건다. 이미 있으면 바꾼다.

    연결 → 일정 순으로 잠그고 검사한다. 같은 일정을 종일로 바꾸는 수정이 겹치면 그 커밋을 기다렸다가
    바뀐 일정으로 본다 — 잠그지 않으면 종일 일정에 시간 있는 일정의 알림이 저장된다.
    """
    if not await lock_link(session, child_id=child.child_id, parent_id=parent.parent_id):
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")  # #427
    event = await find_event(session, child_id=child.child_id, event_id=eid, lock="share")
    if event is None:
        raise ApiError(404, "not_found", "일정을 찾을 수 없어요")
    check_reminder(body.lead, starts_at=event.starts_at, all_day=event.all_day)
    row = await put_reminder(
        session,
        child_id=child.child_id,
        event_id=eid,
        parent_id=parent.parent_id,
        lead=body.lead,
    )
    if row is None:  # 위에서 연결 · 일정을 잡아 두어 오지 않는다. 저장 함수의 계약대로 막아만 둔다
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    out = ReminderResponse(reminder=ReminderOut(lead=row.lead))
    await session.commit()
    return out


@router.delete(
    "/children/{cid}/events/{eid}/reminder",
    status_code=204,
    responses={status: {"model": ErrorEnvelope} for status in (403, 422)},
)
async def delete_my_reminder(
    child: AccessibleChild, parent: CurrentParent, session: SessionDep, eid: uuid.UUID
) -> Response:
    """이 일정의 내 알림을 지운다. 없어도 204 — 두 번 눌러도 결과가 같다."""
    await delete_reminder(
        session, child_id=child.child_id, event_id=eid, parent_id=parent.parent_id
    )
    await session.commit()
    return Response(status_code=204)
