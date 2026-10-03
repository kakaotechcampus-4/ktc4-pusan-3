"""보호자 초대의 요청·응답 — docs/api/invite-v1.md §3.

🚨 프론트 apps/web/src/lib/api/types.ts 의 InviteRequest · InviteResponse 와 같은 모양이어야 한다.
"""

from datetime import datetime

from pydantic import BaseModel


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
