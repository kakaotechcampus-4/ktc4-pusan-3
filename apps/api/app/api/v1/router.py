"""v1 라우터 집계 — 도메인 라우터를 모아 app/main.py 가 한 번에 마운트한다.

Spring 대응: 컨트롤러가 컴포넌트 스캔으로 자동 등록되는 자리.
    FastAPI 는 스캔이 없어서 include_router 로 직접 모은다.

🚨 등록 순서가 계약이다. Spring 의 PathPattern 은 더 구체적인 매핑을 먼저 고르지만
   (그래서 /auth/logout 이 /auth/{provider} 를 이긴다), Starlette 은 **등록한
   순서대로 먼저 맞는 것**을 쓴다. 순서가 뒤집히면 POST /auth/logout 이
   provider="logout" 으로 잡히고, provider 가 enum 이라 422 로 떨어진다 —
   뒤 라우트까지 가지 않는다.
"""

from fastapi import APIRouter

from app.api.v1.routers import auth

v1_router = APIRouter()

# 경로가 고정된 라우터를 먼저 — 위 주석의 이유. 이 두 줄의 순서를 바꾸지 말 것.
v1_router.include_router(auth.fixed_router)
v1_router.include_router(auth.provider_router)
