"""보호자 초대의 요청·응답 — docs/api/invite-v1.md §3.

🚨 프론트 apps/web/src/lib/api/types.ts 의 InviteRequest · InviteResponse 와 같은 모양이어야 한다.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.domains.child.models import ParentChildRelation


class CreateInviteRequest(BaseModel):
    """POST /children/{cid}/invites — 본문은 `{}` 다.

    `relation` 을 받지 않는다. 아이와 어떤 사이인지는 받는 쪽이 수락할 때 고른다 (#96).
    다른 필드가 와도 막지 않고 버린다 — 막으면 낡은 화면이 초대를 아예 못 한다.
    """


class CreateInviteResponse(BaseModel):
    """201 — 원문 코드는 이 응답에서 한 번만 나간다. 서버는 해시만 저장한다."""

    invite_code: str
    """Crockford Base32 8자. 정규화한 값이다 — 화면의 `ABCD-1234` 는 표시 형식이다."""

    expires_at: datetime
    """발행 후 24시간. 1회용이라 이 전에도 한 번 쓰이면 닫힌다."""


class InvitePreviewChild(BaseModel):
    nickname: str
    age_display: str
    """서버가 만든 나이 문구. 프론트는 다시 계산하지 않는다."""


class InvitePreviewInviter(BaseModel):
    nickname: str | None
    """초대한 보호자의 별명만. 다른 정보는 내리지 않는다."""


class InvitePreviewResponse(BaseModel):
    """GET /invites/{code} — 수락 전에 어느 아이에 붙는지 보여 준다.

    🚨 건강 · 알레르기를 싣지 않는다. 아직 연결되지 않은 사람이 코드만으로 부르는 창구다 (NF-04).
    """

    child: InvitePreviewChild
    invited_by: InvitePreviewInviter
    expires_at: datetime


class AcceptInviteRequest(BaseModel):
    """POST /invites/{code}/accept — 관계는 받는 쪽이 고른다. 안 고르면 필드를 뺀다."""

    relation: ParentChildRelation | None = None
    """안 고르면 other 로 저장한다 (#131)."""


class AcceptInviteResponse(BaseModel):
    """200 — 화면이 수락 뒤 그 아이 홈으로 가려면 child_id 가 필요하다."""

    child_id: UUID
    nickname: str
    age_display: str
    role: Literal["member"]
    """초대로 들어온 보호자는 owner 가 아니다. 승인 대기 상태 없이 바로 연결된다."""
