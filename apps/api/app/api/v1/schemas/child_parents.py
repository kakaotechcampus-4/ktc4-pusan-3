"""함께 보는 보호자의 응답 — #336.

🚨 프론트 apps/web/src/lib/api/types.ts 의 ChildParent · ChildParentsResponse 와
   같은 모양이어야 한다.
   다른 점은 nickname 하나 — DB 에서 비어 있을 수 있어 null 로 내린다.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.domains.child.models import ParentChildRelation


class ChildParentOut(BaseModel):
    parent_id: UUID
    nickname: str | None
    """보호자 별명. 정하지 않은 보호자는 null."""

    relation: ParentChildRelation
    role: Literal["owner", "member"]
    """owner = 아이를 등록한 보호자. 연결을 끊을 수 없다."""

    connected_at: datetime


class ChildParentsResponse(BaseModel):
    """GET /children/{cid}/parents — 연결된 순서대로."""

    parents: list[ChildParentOut]
