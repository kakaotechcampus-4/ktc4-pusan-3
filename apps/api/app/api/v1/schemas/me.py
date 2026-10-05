"""내 정보 응답 — `GET /me` (#92).

🚨 프론트 apps/web/src/lib/api/types.ts 의 Me 와 같은 모양이어야 한다.
"""

from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.schemas.children import Role
from app.domains.child.models import ParentChildRelation
from app.domains.consent.models import ConsentScope


class MeChild(BaseModel):
    """연결된 아이 하나."""

    child_id: UUID
    nickname: str
    age_display: str = Field(description="화면에 그대로 쓰는 나이 문구 (생후 N일 · N개월 · 만 N세)")
    relation: ParentChildRelation = Field(description="이 보호자와 아이의 관계")
    role: Role
    consent_required: list[ConsentScope] = Field(
        description="아직 동의하지 않은 아이 필수 동의. 비어 있지 않으면 화면이 동의를 다시 받는다"
    )


class MeResponse(BaseModel):
    """로그인한 보호자와 연결된 아이들. 아이가 0명이면 화면은 /start 로 간다."""

    id: UUID
    nickname: str | None = Field(description="가입 화면의 부르는 이름")
    children: list[MeChild]
