"""v1 라우터 집계 — 도메인 라우터를 모아 app/main.py가 한 번에 등록한다.

🚨 등록 순서가 계약이다. Starlette는 **등록한 순서대로 먼저 맞는 경로**를 쓴다.
   순서가 뒤집히면 POST /auth/logout이
   provider="logout" 으로 잡히고, provider 가 enum 이라 422 로 떨어진다 —
   뒤 라우트까지 가지 않는다.
"""

from fastapi import APIRouter

from app.api.v1.routers import auth

v1_router = APIRouter()

# 경로가 고정된 라우터를 먼저 — 위 주석의 이유. 이 두 줄의 순서를 바꾸지 말 것.
v1_router.include_router(auth.fixed_router)
v1_router.include_router(auth.provider_router)
