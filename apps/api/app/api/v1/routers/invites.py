"""보호자 초대 — docs/api/invite-v1.md §3. 지금은 코드 발행 하나.

인증은 router.py 의 protected_router 가 건다. 세 엔드포인트 모두 Bearer 다.

🚨 원문 코드는 저장하지 않는다. 정규화한 값의 SHA-256 만 둔다 (session · auth_handoff 와 같다).
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter

from app.api.deps.auth import CurrentParent, hash_token
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.invites import CreateInviteRequest, CreateInviteResponse
from app.domains.child.repository import create_invite, find_accessible_child

router = APIRouter()

# Crockford Base32 — 옮겨 적다 헷갈리는 I · L · O · U 가 없다.
# 프론트 apps/web/src/lib/invite-code.ts 의 ALPHABET 과 같아야 한다.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 8
_INVITE_TTL = timedelta(hours=24)


def generate_invite_code() -> str:
    """예측할 수 없는 8자. 🚨 random 이 아니라 secrets 다 — 8자(40비트)라 시도 제한과 함께 쓴다."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def normalize_invite_code(raw: str) -> str:
    """받은 코드를 저장할 때와 같은 모양으로 되돌린다.

    대문자화 → 혼동 문자 치환(O→0, I·L→1) → 알파벳 밖 문자(하이픈 · 공백) 제거.
    프론트 normalizeInviteCode 와 같은 규칙이다. 길이는 자르지 않는다 — 8자가 아니면 없는 코드다.
    """
    upper = raw.upper().translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))
    return "".join(c for c in upper if c in _ALPHABET)


def hash_invite_code(raw: str) -> bytes:
    return hash_token(normalize_invite_code(raw))


@router.post(
    "/children/{cid}/invites",
    status_code=201,
    responses={403: {"model": ErrorEnvelope}},
)
async def issue_invite(
    cid: UUID,
    body: CreateInviteRequest,
    parent: CurrentParent,
    session: SessionDep,
) -> CreateInviteResponse:
    """코드 발행 — 아이를 등록한 보호자(owner)만. 부를 때마다 새 코드다."""
    child = await find_accessible_child(session, child_id=cid, parent_id=parent.parent_id)
    if child is None:
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    if child.owner_parent_id != parent.parent_id:
        # 연결은 됐지만 owner 가 아니다. child_access_denied(연결 없음)와 가른다 —
        # 화면은 이 버튼을 member 에게도 보여 주므로, 왜 안 되는지를 코드로 알려야 한다.
        raise ApiError(403, "owner_only", "아이를 등록한 보호자만 초대할 수 있어요")

    code = generate_invite_code()
    expires_at = datetime.now(UTC) + _INVITE_TTL
    await create_invite(
        session,
        child_id=child.id,
        created_by=parent.parent_id,
        code_hash=hash_invite_code(code),
        expires_at=expires_at,
    )
    await session.commit()
    return CreateInviteResponse(invite_code=code, expires_at=expires_at)
