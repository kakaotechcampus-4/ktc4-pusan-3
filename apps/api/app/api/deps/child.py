"""아이 주소(`/children/{cid}/*`)의 접근 확인 — 연결된 보호자만 (#134 9단계 · 계약서 §01).

라우터는 경로의 cid 를 그대로 믿지 않고 이 의존성이 확인해 돌려준 id 를 쓴다.

🚨 없는 아이 · 남의 아이 · 보관된 아이를 전부 같은 403 으로 막는다. 404 를 주면 그 id 의
   아이가 있다 / 없다가 드러난다 (auth-kakao-v1 부록 A "간접 식별자는 소유를 검사한다").
🚨 FastAPI 는 의존성을 라우터 본문보다 먼저 돌린다. 그래서 막힌 요청은 하루 입력 횟수도
   Idempotency 키도 건드리지 않는다.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ApiError
from app.domains.child.repository import find_accessible_child


async def get_accessible_child(cid: UUID, parent: CurrentParent, session: SessionDep) -> UUID:
    """경로의 cid 가 지금 보호자에게 연결된 아이인지 확인하고 그 id 를 돌려준다."""
    child = await find_accessible_child(session, child_id=cid, parent_id=parent.parent_id)
    if child is None:
        raise ApiError(403, "child_access_denied", "이 아이의 기록에 접근할 수 없어요")
    return child.id


AccessibleChild = Annotated[UUID, Depends(get_accessible_child)]
"""라우터가 쓰는 이름. `child_id: AccessibleChild` 로 받으면 확인을 거친 아이 id 가 온다."""
