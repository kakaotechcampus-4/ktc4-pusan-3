"""아이 주소(`/children/{cid}/*`)의 접근 확인 — 연결된 보호자만 (#134 9단계).

계약서 §01 "아이 스코프 — 호출자의 parent_child 연결을 매 요청 확인, 없으면 403
child_access_denied". 라우터는 경로의 cid 를 그대로 믿지 않고 이 의존성이 확인해 돌려준 id 를 쓴다.

🚨 없는 아이 · 남의 아이 · 보관된 아이를 전부 같은 403 으로 막는다. 404 를 주면 그 id 의
   아이가 있다 / 없다가 드러난다.
🚨 FastAPI 는 의존성을 라우터 본문보다 먼저 돌린다. 그래서 막힌 요청은 하루 입력 횟수도
   Idempotency 키도 건드리지 않는다 — 같은 키 재생보다도 앞이다 (docs/api/idempotency-v1.md §3-1).
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ApiError
from app.domains.child.repository import find_accessible_child


@dataclass(frozen=True)
class ChildAccess:
    """확인을 거친 아이. 라우터는 ORM 객체 대신 이 값만 받는다 (CurrentParent 와 같은 방식).

    birth_date — Agent 가 식이 단계(12개월 경계)를 정하는 데 쓴다. 나이 계산은 진입점이 한다.
    """

    child_id: UUID
    birth_date: date


async def get_accessible_child(
    cid: UUID, parent: CurrentParent, session: SessionDep
) -> ChildAccess:
    """경로의 cid 가 지금 보호자에게 연결된 아이인지 확인하고 그 아이를 돌려준다."""
    child = await find_accessible_child(session, child_id=cid, parent_id=parent.parent_id)
    if child is None:
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    return ChildAccess(child_id=child.id, birth_date=child.birth_date)


AccessibleChild = Annotated[ChildAccess, Depends(get_accessible_child)]
"""라우터가 쓰는 이름. `child: AccessibleChild` 로 받으면 확인을 거친 아이가 온다."""
