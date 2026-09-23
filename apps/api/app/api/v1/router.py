"""v1 라우터 집계 — 도메인 라우터를 모아 app/main.py가 한 번에 등록한다.

🚨 인증이 기본값이다. 라우터를 protected_router 에 붙이면 인증이 자동으로 걸리고,
   public_router 에 붙일 때만 무인증이 된다. 루트 CLAUDE.md §9 는 "/api/v1 은 기본적으로
   Bearer 인증을 요구한다" 인데, 라우터마다 의존성을 손으로 달게 두면 한 번 깜빡한
   엔드포인트가 조용히 공개된다. 붙이는 곳을 고르는 일로 바꿔 눈에 보이게 한다.

🚨 등록 순서가 계약이다. Starlette는 **등록한 순서대로 먼저 맞는 경로**를 쓴다.
   순서가 뒤집히면 POST /auth/logout이 provider="logout" 으로 잡히고,
   provider 가 enum 이라 422 로 떨어진다 — 뒤 라우트까지 가지 않는다.
"""

from fastapi import APIRouter, Depends

from app.api.deps.auth import get_current_parent
from app.api.v1.routers import auth, runs

public_router = APIRouter()
"""무인증. 로그인 자체를 시작·완료하는 5개만 (루트 CLAUDE.md §9).

여기에 라우터를 붙이는 것은 보안 예외 목록을 늘리는 일이다. 늘려야 한다면 구현 전에
그 목록과 API 계약을 먼저 고친다.
"""

protected_router = APIRouter(dependencies=[Depends(get_current_parent)])
"""나머지 전부. 앞으로 추가되는 도메인 라우터는 여기에 붙인다."""

protected_router.include_router(auth.fixed_router)
protected_router.include_router(runs.router)
public_router.include_router(auth.provider_router)

v1_router = APIRouter()

# 경로가 고정된 쪽을 먼저 — 위 주석의 이유. 이 두 줄의 순서를 바꾸지 말 것.
v1_router.include_router(protected_router)
v1_router.include_router(public_router)
