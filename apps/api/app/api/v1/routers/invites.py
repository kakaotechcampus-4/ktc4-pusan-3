"""보호자 초대 — docs/api/invite-v1.md §3. 코드 발행 · 수락 전 확인 · 수락.

인증은 router.py 의 protected_router 가 건다. 세 엔드포인트 모두 Bearer 다.

🚨 원문 코드는 저장하지 않는다. 정규화한 값의 SHA-256 만 둔다 (session · auth_handoff 와 같다).
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentParent, hash_token
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.quota import today_kst
from app.api.v1.schemas.invites import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    CreateInviteRequest,
    CreateInviteResponse,
    InvitePreviewChild,
    InvitePreviewInviter,
    InvitePreviewResponse,
)
from app.domains.child.models import Child, Invite, ParentChildRelation
from app.domains.child.repository import (
    connect_parent,
    consume_invite,
    create_invite,
    find_accessible_child,
    find_invite_with_child,
    list_children_for_parent,
)
from app.rules.age import age_display

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


async def _reject_if_has_child(session: AsyncSession, parent_id: UUID) -> None:
    """이미 아이가 있는 보호자는 코드를 보기 전에 돌려보낸다.

    코드의 유효 여부를 알려 주지 않으므로 시도 제한의 실패로 세지 않는다 (#198).
    연결되지 않을 아이의 별명 · 나이를 보여 준 뒤에 거절하는 일도 막는다 (invite-v1.md §5).
    """
    if await list_children_for_parent(session, parent_id=parent_id):
        raise ApiError(409, "child_already_exists", "이미 등록한 아이가 있어요")


async def _find_open_invite(
    session: AsyncSession, raw_code: str
) -> tuple[Invite, Child, str | None]:
    """쓸 수 있는 초대를 찾는다. 없음 404 · 사용됨 409 · 만료 410."""
    code = normalize_invite_code(raw_code)
    found = None
    if len(code) == _CODE_LENGTH:
        found = await find_invite_with_child(session, code_hash=hash_token(code))
    if found is None:
        raise ApiError(404, "invite_not_found", "이 코드를 찾을 수 없어요")
    invite = found[0]
    if invite.used_at is not None:
        raise ApiError(409, "invite_used", "이미 사용된 코드예요")
    if invite.expires_at <= datetime.now(UTC):
        raise ApiError(410, "invite_expired", "코드 기한이 지났어요")
    return found


@router.get(
    "/invites/{code}",
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        410: {"model": ErrorEnvelope},
    },
)
async def preview_invite(
    code: str, parent: CurrentParent, session: SessionDep
) -> InvitePreviewResponse:
    """수락 전 확인 — 어느 아이에 붙는지 보여 준다.

    🚨 코드를 소비하지 않는다. 확인하고 그만둔 사람이 코드를 다시 받지 않게 한다.
    """
    await _reject_if_has_child(session, parent.parent_id)
    invite, child, inviter_nickname = await _find_open_invite(session, code)
    return InvitePreviewResponse(
        child=InvitePreviewChild(
            nickname=child.nickname, age_display=age_display(child.birth_date, today_kst())
        ),
        invited_by=InvitePreviewInviter(nickname=inviter_nickname),
        expires_at=invite.expires_at,
    )


@router.post(
    "/invites/{code}/accept",
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        410: {"model": ErrorEnvelope},
    },
)
async def accept_invite(
    code: str, body: AcceptInviteRequest, parent: CurrentParent, session: SessionDep
) -> AcceptInviteResponse:
    """수락 — 코드 소비와 parent_child 생성을 한 트랜잭션으로. 수락하면 바로 member 다."""
    await _reject_if_has_child(session, parent.parent_id)

    normalized = normalize_invite_code(code)
    invite = None
    if len(normalized) == _CODE_LENGTH:
        invite = await consume_invite(
            session,
            code_hash=hash_token(normalized),
            parent_id=parent.parent_id,
            now=datetime.now(UTC),
        )
    if invite is None:
        # 소비하지 못했다. 다시 읽어 왜인지 가른다 — 없음 404 · 사용됨 409 · 만료 410.
        await _find_open_invite(session, normalized)
        # 다시 읽었는데 쓸 수 있게 보이는 경우는 논리상 없다. 오면 쓸 수 없는 코드로 답한다.
        raise ApiError(409, "invite_used", "이미 사용된 코드예요")

    try:
        await connect_parent(
            session,
            child_id=invite.child_id,
            parent_id=parent.parent_id,
            relation=body.relation or ParentChildRelation.OTHER,
        )
    except IntegrityError as exc:
        # 앞의 확인과 여기 사이에 이 보호자에게 아이가 생겼다 (아이 등록 · 다른 초대 수락).
        # 롤백하면 코드 소비도 되돌아간다 — 코드는 다른 사람이 쓸 수 있게 남는다.
        await session.rollback()
        # asyncpg 의 원래 예외(UniqueViolationError)가 제약 이름을 들고 있다.
        if getattr(exc.orig.__cause__, "constraint_name", None) != "uq_parent_child_parent_id":
            raise
        raise ApiError(409, "child_already_exists", "이미 등록한 아이가 있어요") from exc

    child = await session.get(Child, invite.child_id)
    await session.commit()
    return AcceptInviteResponse(
        child_id=child.id,
        nickname=child.nickname,
        age_display=age_display(child.birth_date, today_kst()),
        role="member",
    )
