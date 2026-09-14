"""identity 도메인의 저장 규칙 — 명세 docs/api/auth-kakao-v1.md §5-3 · §5-4

라우터가 SQL 을 직접 쓰지 않게 여기로 모은다. 단건 조회·삽입이라 ORM 을 쓴다
(apps/api/CLAUDE.md "ORM vs raw SQL").

🚨 원문을 받지 않는다. 세션 토큰·1회용 코드·bind 는 전부 해시로만 들어온다 (§7-4).
   해시로 바꾸는 일은 부르는 쪽에서 끝내고, 여기는 bytes 만 받는다.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import AuthHandoff, AuthIdentity, AuthProvider


async def find_parent_id_by_identity(
    session: AsyncSession,
    *,
    provider: AuthProvider,
    provider_user_id: str,
) -> uuid.UUID | None:
    """회원번호로 기존 보호자를 찾는다. 처음 보는 회원번호면 None (§3-3 4번).

    None 이 "신규" 를 뜻하므로 parent 를 만들지 않는다 — 신규 생성은 필수 동의를 검증한
    signup 트랜잭션에서만 일어난다 (§6-1).

    🚨 deleted_at 이 찍힌 계정도 그대로 찾아 돌려준다. 탈퇴 계정 차단은 세션을 내주는
       교환 시점의 판정이고(§10-1 · A-19), 여기서 None 으로 만들면 탈퇴한 사람이
       신규 가입으로 흘러가 같은 회원번호로 parent 가 하나 더 생긴다.
    """
    stmt = select(AuthIdentity.parent_id).where(
        AuthIdentity.provider == provider,
        AuthIdentity.provider_user_id == provider_user_id,
    )
    return await session.scalar(stmt)


async def create_handoff(
    session: AsyncSession,
    *,
    provider: AuthProvider,
    code_hash: bytes,
    bind_hash: bytes,
    parent_id: uuid.UUID | None,
    provider_user_id: str | None,
    expires_at: datetime,
) -> None:
    """1회용 코드 1건을 남긴다 (§5-4).

    parent_id 와 provider_user_id 는 정확히 하나만 찬다. 둘 다 차거나 둘 다 비면
    CHECK 제약이 막는다 — 여기서 다시 검사하지 않는 이유다.
    """
    session.add(
        AuthHandoff(
            provider=provider,
            code_hash=code_hash,
            bind_hash=bind_hash,
            parent_id=parent_id,
            provider_user_id=provider_user_id,
            expires_at=expires_at,
        )
    )
    await session.flush()
