"""준비물 체크 — #318 (09 캘린더).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 — 연결된 보호자가 아니면 403 (deps/child.py). 그 아이의 일정에 없는 준비물은 404 다.

체크는 되돌릴 수 있어서 승인 게이트가 아니다. 최종 상태를 보내는 요청이라 Idempotency-Key 를
받지 않는다 — 같은 상태를 여러 번 보내도 결과가 같다 (`set_event_item_prepared`).
🚨 체크 · 해제 요청의 순서가 뒤바뀌는 것은 서버가 못 막는다.
   화면이 준비물마다 요청을 한 줄로 보낸다.
"""

import uuid

from fastapi import APIRouter

from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.event_items import EventItemUpdateRequest, EventItemUpdateResponse
from app.api.v1.schemas.suggestions import EventItemOut
from app.domains.schedule.repository import set_event_item_prepared

router = APIRouter()


@router.patch(
    "/children/{cid}/event-items/{iid}",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 404, 422)},
)
async def update_event_item(
    child: AccessibleChild, session: SessionDep, iid: uuid.UUID, body: EventItemUpdateRequest
) -> EventItemUpdateResponse:
    """준비물을 체크하거나 해제한다."""
    item = await set_event_item_prepared(
        session, child_id=child.child_id, item_id=iid, is_prepared=body.is_prepared
    )
    if item is None:
        raise ApiError(404, "not_found", "그 준비물을 찾지 못했어요")
    out = EventItemUpdateResponse(
        item=EventItemOut(
            item_id=str(item.item_id),
            item_name=item.item_name,
            is_prepared=item.is_prepared,
            prepared_at=item.prepared_at.isoformat() if item.prepared_at else None,
        )
    )
    await session.commit()
    return out
