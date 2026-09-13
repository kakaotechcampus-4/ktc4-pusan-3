"""로그인한 보호자를 라우터에 넣어 준다 — 명세 §6-2 ① authenticate

Spring 대응: @AuthenticationPrincipal + 그 값을 채우는 ArgumentResolver.
    인증 판정을 미들웨어(Spring 의 Filter)가 아니라 의존성으로 두는 이유는 경로별로
    켜고 끄기 위해서다 — GET /me 와 /consents 는 계정 동의 검사를 건너뛴다 (§6-2).

🚨 이 파일의 목적은 **반환 타입을 고정하는 것**이다. 앞으로 27개 엔드포인트가 이
   모양을 그대로 받는다. ORM Parent 객체를 넘기지 않는다 — 세션이 닫히면 만료된
   객체가 되고(Spring 의 LazyInitializationException 과 같은 함정), 핸들러가
   parent 를 통해 다른 테이블까지 긁는 경로가 열린다. 권한 판정은 deps 한 곳에서만
   한다 (명세 §6-2 "핸들러가 직접 권한 쿼리를 쓰지 않는다").

구현 상태: 토큰으로 세션을 찾는 본문은 session 테이블을 만드는 #34 PR 에서 채운다.
"""

import hashlib
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps.db import SessionDep
from app.core.errors import ApiError

# auto_error=False — 헤더가 없을 때 FastAPI 기본 403 이 나가지 않게 하고,
# 우리가 401 unauthenticated 봉투로 답한다 (계약서 §01).
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    """Spring 대응: record LoginUser(UUID parentId, UUID sessionId).

    session_id 를 함께 들고 다니는 이유 — 로그아웃은 "그 행 1건" 만 지운다
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

    # TODO(#34): hash_token(credentials.credentials) 로 session 조회 →
    #   expires_at 확인 → AuthContext(parent_id, session_id) 반환.
    #
    #   🚨 "없음 · 만료 · 이미 삭제됨" 을 구분해 알려주지 않는다 — 전부
    #      401 unauthenticated. 사유를 알려주면 공격자에게 정보를 준다 (명세 §8-1).
    #   관련 테스트: A-14(만료 토큰) · A-15(로그아웃 직후) · A-19(탈퇴한 계정)
    raise ApiError(501, "not_implemented", "아직 구현되지 않았어요")


CurrentParent = Annotated[AuthContext, Depends(get_current_parent)]
"""라우터가 쓰는 이름. `async def logout(auth: CurrentParent)` 처럼 적는다."""
