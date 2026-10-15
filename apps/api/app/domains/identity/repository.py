"""identity 도메인의 저장 규칙 — 명세 docs/api/auth-kakao-v1.md §5-3 · §5-4

라우터가 SQL 을 직접 쓰지 않게 여기로 모은다. 단건 조회·삽입이라 ORM 을 쓴다
(apps/api/CLAUDE.md "ORM vs raw SQL").

🚨 원문을 받지 않는다. 세션 토큰·1회용 코드·bind 는 전부 해시로만 들어온다 (§7-4).
   해시로 바꾸는 일은 부르는 쪽에서 끝내고, 여기는 bytes 만 받는다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import (
    AuthHandoff,
    AuthIdentity,
    AuthProvider,
    AuthSession,
    Parent,
    SessionAccess,
)


async def find_parent_id_by_identity(
    session: AsyncSession,
    *,
    provider: AuthProvider,
    provider_user_id: str,
) -> uuid.UUID | None:
    """회원번호로 기존 보호자를 찾는다. 처음 보는 회원번호면 None (§3-3 4번).

    None 이 "신규" 를 뜻하므로 parent 를 만들지 않는다 — 신규 생성은 필수 동의를 검증한
    signup 트랜잭션에서만 일어난다 (§6-1).

    탈퇴하면 auth_identity 가 보호자 행과 함께 지워진다(#379). 그래서 탈퇴한 사람이 다시
    로그인하면 여기서 None — 신규 가입이다 (§10 1번).
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


@dataclass(frozen=True)
class SessionRow:
    """access 1건으로 찾은 세션. 인증 판정에 필요한 칸만 담는다.

    ORM 객체를 올려보내지 않는다 — 세션이 닫힌 뒤 필드를 더 읽다 실패하는 경로와,
    핸들러가 parent 를 통해 다른 테이블까지 조회하는 경로를 둘 다 막는다.

    expires_at 은 access 의 만료다. 세션(refresh) 만료는 refresh 가 판정한다.
    """

    session_id: uuid.UUID
    parent_id: uuid.UUID
    expires_at: datetime


async def find_session_by_access_hash(
    session: AsyncSession,
    *,
    token_hash: bytes,
) -> SessionRow | None:
    """access 토큰 해시로 그 세션을 찾는다 (§6-2 ①).

    탈퇴는 보호자 행을 지우고 세션 · access 도 CASCADE 로 함께 지워진다(#379). 그래서 탈퇴
    여부를 따로 보지 않는다 — 행이 없으면 그것으로 끝이다. 로그아웃 · 재사용 탐지도 세션
    행 삭제라 같다.
    """
    stmt = (
        select(AuthSession.id, AuthSession.parent_id, SessionAccess.expires_at)
        .join(AuthSession, AuthSession.id == SessionAccess.session_id)
        .where(SessionAccess.token_hash == token_hash)
    )
    row = (await session.execute(stmt)).first()
    return SessionRow(*row) if row is not None else None


async def delete_session(session: AsyncSession, *, session_id: uuid.UUID) -> None:
    """세션 행 1건 삭제 — 로그아웃 (§5-3 · A-15).

    🚨 id 로만 지운다. parent_id 로 지우면 같은 계정의 다른 기기 세션까지 끊긴다.
       폐기를 행 삭제로 하는 이유는 §5-3 — 세션은 증빙이 아니라 유출 표면이다.
    """
    await session.execute(delete(AuthSession).where(AuthSession.id == session_id))


@dataclass(frozen=True)
class HandoffRow:
    """소비된 1회용 코드 1건. parent_id 와 provider_user_id 중 하나만 차 있다."""

    bind_hash: bytes
    provider_user_id: str | None
    parent_id: uuid.UUID | None


async def consume_handoff(
    session: AsyncSession, *, code_hash: bytes, provider: AuthProvider
) -> HandoffRow | None:
    """1회용 코드를 원자적으로 소비한다 — 명세 §5-4 · A-04 · A-12.

    🚨 DELETE … RETURNING 한 문장이다. 먼저 SELECT 하고 나중에 DELETE 하면 "조회했는데
       그 사이 남이 썼다" 는 틈이 생긴다. 두 번째 요청은 지울 행이 없어 None 을 받는다.

    🚨 provider 도 조건이다 (#83). 코드가 어느 provider 로 발급됐는지 보지 않으면,
       카카오로 받은 코드를 /auth/google/signup 에 보내 카카오 회원번호를 구글
       식별자로 등록할 수 있다. 경로의 provider 를 그대로 믿고 identity 를 만들기
       때문이다. 조건을 WHERE 에 두면 불일치가 "없는 코드" 와 똑같이 보이고
       (§8-1 — 어느 쪽이 틀렸는지 알려주지 않는다), 코드가 타지 않아 진짜 사용자는
       올바른 경로로 다시 시도할 수 있다.

    만료는 DB 시각으로 본다. 애플리케이션 시계를 쓰면 두 시계가 어긋난 만큼 창이 생긴다.
    """
    stmt = (
        delete(AuthHandoff)
        .where(
            AuthHandoff.code_hash == code_hash,
            AuthHandoff.provider == provider,
            AuthHandoff.expires_at > func.now(),
        )
        .returning(AuthHandoff.bind_hash, AuthHandoff.provider_user_id, AuthHandoff.parent_id)
    )
    row = (await session.execute(stmt)).first()
    return HandoffRow(*row) if row is not None else None


async def create_parent(session: AsyncSession, *, nickname: str) -> Parent:
    """보호자 1건. 이름은 가입 화면에서 보호자가 직접 적은 "부르는 이름" 이다 (#172).

    카카오에서 가져오지 않는다 (§5-2 · NF-04 최소 수집) — 카카오 닉네임을 쓰면 보호자가
    적지 않은 값이 저장되고, 동의문 1 이 "카카오에 요청하지 않는 것" 으로 알린 약속이 깨진다.
    """
    parent = Parent(nickname=nickname)
    session.add(parent)
    await session.flush()
    return parent


async def create_identity(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    provider: AuthProvider,
    provider_user_id: str,
) -> None:
    """provider + 회원번호를 보호자에 묶는다 (§5-2). UNIQUE 가 중복 가입을 막는다."""
    session.add(
        AuthIdentity(
            parent_id=parent_id,
            provider=provider,
            provider_user_id=provider_user_id,
        )
    )
    await session.flush()


async def create_session(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    refresh_hash: bytes,
    refresh_expires_at: datetime,
    absolute_expires_at: datetime,
) -> uuid.UUID:
    """기기 로그인 1건 (§5-3). 토큰 원문은 받지 않는다 — 해시만 저장한다 (A-18).

    access 는 따로 create_access 로 만든다. 로그인 1건에 access 가 여러 개 딸린다.
    """
    row = AuthSession(
        parent_id=parent_id,
        refresh_hash=refresh_hash,
        refresh_expires_at=refresh_expires_at,
        absolute_expires_at=absolute_expires_at,
    )
    session.add(row)
    await session.flush()
    return row.id


async def create_access(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    token_hash: bytes,
    expires_at: datetime,
) -> None:
    """그 로그인에 access 1건을 단다.

    앞서 나간 access 는 지우지 않는다 — 다른 탭이 쓰는 중일 수 있다. 만료된 것은 배치가 지운다.
    """
    session.add(SessionAccess(session_id=session_id, token_hash=token_hash, expires_at=expires_at))
    await session.flush()


async def find_parent(session: AsyncSession, parent_id: uuid.UUID) -> Parent | None:
    """세션 응답에 실을 보호자."""
    return await session.get(Parent, parent_id)
