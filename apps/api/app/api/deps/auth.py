"""로그인한 보호자를 라우터에 넣어 준다 — 명세 §6-2 ① authenticate

인증 판정을 전체 요청에 적용하는 미들웨어가 아니라 의존성으로 두어 경로별로 켜고 끈다.
GET /me와 /consents는 계정 동의 검사를 건너뛴다 (§6-2).

🚨 이 파일의 목적은 **반환 타입을 고정하는 것**이다. 앞으로 27개 엔드포인트가 이
   모양을 그대로 받는다. ORM Parent 객체를 넘기지 않는다 — 세션이 닫히면 만료된
   객체의 필드를 추가로 읽을 때 실패할 수 있고, 핸들러가 parent를 통해 다른 테이블까지
   조회하는 경로가 열린다. 권한 판정은 deps 한 곳에서만
   한다 (명세 §6-2 "핸들러가 직접 권한 쿼리를 쓰지 않는다").

🚨 실패를 두 갈래로 나눈다 — 세션이 없음·만료·삭제됨은 전부 401 unauthenticated,
   계정 자체가 탈퇴 처리된 경우만 404 not_found (명세 §8-1 · A-19).
   앞의 셋을 구분해 알려주면 공격자에게 정보를 주고, 뒤는 만료를 기다리지 않는다.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps.db import SessionDep
from app.api.errors import ApiError
from app.domains.identity.repository import find_session_by_token_hash

# auto_error=False — 헤더가 없을 때 FastAPI 기본 403 이 나가지 않게 하고,
# 우리가 401 unauthenticated 봉투로 답한다 (계약서 §01).
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    """인증된 보호자와 현재 세션의 식별자를 함께 담는다.

    session_id를 함께 들고 다니는 이유 — 로그아웃은 "그 행 1건"만 지운다
    (명세 §5-3). 같은 계정의 다른 기기 세션을 건드리지 않는다.
    """

    parent_id: UUID
    session_id: UUID


def hash_token(token: str) -> bytes:
    """session.token_hash 대조용 해시.

    🚨 hexdigest 가 아니라 digest 다 — 컬럼이 bytea 다 (명세 §5-3).
       서버는 토큰 원문을 저장하지 않고, 받은 값을 그때그때 해시해 비교한다 (§5).
    """
    return hashlib.sha256(token.encode()).digest()


async def get_current_parent(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthContext:
    """Bearer 토큰 → SHA-256 → session 조회 → parent_id (명세 §6-2 ①)."""
    if credentials is None:
        raise ApiError(401, "unauthenticated", "다시 로그인해 주세요")

    row = await find_session_by_token_hash(
        session, token_hash=hash_token(credentials.credentials)
    )

    # 🚨 "없음 · 만료 · 이미 삭제됨" 을 구분해 알려주지 않는다 — 전부 401 이다.
    #    사유를 알려주면 공격자에게 정보를 준다 (명세 §8-1).
    #    관련 테스트: A-14(만료 토큰) · A-15(로그아웃 직후)
    if row is None or row.expires_at <= datetime.now(UTC):
        raise ApiError(401, "unauthenticated", "다시 로그인해 주세요")

    # A-19 만 다른 곳으로 간다. 탈퇴한 계정은 세션 만료를 기다리지 않고 즉시 끊는다.
    # 유예기간 N일이 미정이라 그전까지 404 로 막아둔다 (§8-1 · §10-1).
    if row.parent_deleted_at is not None:
        raise ApiError(404, "not_found", "계정을 찾을 수 없어요")

    return AuthContext(parent_id=row.parent_id, session_id=row.session_id)


CurrentParent = Annotated[AuthContext, Depends(get_current_parent)]
"""라우터가 쓰는 이름. `async def logout(auth: CurrentParent)` 처럼 적는다."""
