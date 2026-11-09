"""아이 삭제 — 아이 동의를 앱 안에서 거두는 길 (#751 · 개인정보 보호법 제38조 ④).

아이 기본정보 · 건강정보 동의를 거두는 것과 같다(10-14 PM 결정). 그래서 골라 지우지 않고 아이와
기록을 통째로 지운다. 유예 없이 바로 지운다 — 탈퇴(`domains/identity/withdraw.py`)와 같다.

호출자의 트랜잭션 안에서 돈다 — 여기서 commit 하지 않는다. 하나라도 실패하면 전부 되돌아간다.

지우는 것은 탈퇴가 아이를 지울 때와 같은 `purge_child` 다. 동의 이력은 보관 표로 옮기고 아이 행을
지우면, 기록 · 일정 · 사진 · 대화 · 일기 · 함께 보는 보호자 연결이 FK CASCADE 로 따라온다.
보호자 계정과 계정 동의는 그대로다.

🚨 메모리에 든 그 아이 것(되묻기 · 05 다시 시도 · 사진 읽은 내용)은 여기서 지우지 않는다. 커밋 뒤에
   라우터가 지운다 — 커밋 전에 실패하면 DB 가 되돌아가니 메모리도 남아 있어야 한다.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child
from app.domains.consent.retention import purge_child


class ChildDeletion(StrEnum):
    DELETED = "deleted"
    GONE = "gone"
    """잠금을 기다리는 사이 다른 요청(같은 삭제 · 등록 보호자의 탈퇴)이 먼저 지웠다."""
    NOT_OWNER = "not_owner"
    """등록 보호자가 아니다. 라우터가 먼저 막아서 보통은 오지 않는다."""


async def delete_owned_child(
    session: AsyncSession, *, child_id: uuid.UUID, parent_id: uuid.UUID, now: datetime
) -> ChildDeletion:
    """등록 보호자가 자기 아이를 지운다.

    🚨 아이 행을 보관을 읽기 전에 잠근다 — 탈퇴와 같은 이유다. 아이 동의는 함께 보는 보호자도
       남길 수 있어서, 잠그지 않으면 보관 목록을 읽은 뒤 들어온 동의가 보관 없이 함께 지워진다.
       같은 아이의 삭제가 동시에 둘 돌아도 나중 요청은 앞 요청이 커밋할 때까지 여기서 기다렸다가
       GONE 으로 끝난다.
    🚨 아이 행만 잠그고 보호자 행은 잠그지 않는다. 탈퇴는 보호자 → 아이 순서로 잠그는데, 여기서
       아이 → 보호자로 잡으면 둘이 겹칠 때 교착이 된다. 아이를 지우는 데 보호자 행은 필요 없다.
    """
    owner = await session.scalar(
        select(Child.owner_parent_id).where(Child.id == child_id).with_for_update()
    )
    if owner is None:
        return ChildDeletion.GONE
    if owner != parent_id:
        return ChildDeletion.NOT_OWNER
    await purge_child(session, child_id=child_id, now=now)
    return ChildDeletion.DELETED
