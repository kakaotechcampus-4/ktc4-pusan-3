"""내 일기 쓰기 · 지우기 — #318 (09 캘린더).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 — 연결된 보호자가 아니면 403 (deps/child.py).

🚨 작성자는 요청이 아니라 로그인한 보호자(`CurrentParent`)다. 요청에서 작성자를 받지 않으므로
   다른 보호자의 일기를 덮어쓰거나 지울 길이 없다. 하루 한 행 · 같은 날 다시 쓰면 덮어쓴다.
   일기는 관찰이나 기억으로 바꾸지 않는다 — 저장만 한다.
"""

import datetime as dt

from fastapi import APIRouter, Response

from app.api.calendar_dates import check_day
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.calendar import MyDiaryOut
from app.api.v1.schemas.diary import DiaryRequest
from app.domains.schedule.repository import delete_diary, put_diary

router = APIRouter()


@router.put(
    "/children/{cid}/diary/{date}",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 422)},
)
async def put_my_diary(
    child: AccessibleChild,
    parent: CurrentParent,
    session: SessionDep,
    date: dt.date,
    body: DiaryRequest,
) -> MyDiaryOut:
    """그날 내 일기를 쓴다. 이미 있으면 덮어쓴다."""
    check_day(date, status=422)
    if not body.content.strip():
        raise ApiError(422, "validation_failed", "일기 내용을 적어 주세요")
    row = await put_diary(
        session, child_id=child.child_id, parent_id=parent.parent_id, day=date, content=body.content
    )
    if row is None:  # 확인한 뒤 연결이 끊겼다 (#427)
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    out = MyDiaryOut(content=row.content, updated_at=row.updated_at)
    await session.commit()
    return out


@router.delete(
    "/children/{cid}/diary/{date}",
    status_code=204,
    responses={status: {"model": ErrorEnvelope} for status in (403, 422)},
)
async def delete_my_diary(
    child: AccessibleChild, parent: CurrentParent, session: SessionDep, date: dt.date
) -> Response:
    """그날 내 일기를 지운다. 없어도 204 — 두 번 눌러도 결과가 같다."""
    check_day(date, status=422)
    await delete_diary(session, child_id=child.child_id, parent_id=parent.parent_id, day=date)
    await session.commit()
    return Response(status_code=204)
