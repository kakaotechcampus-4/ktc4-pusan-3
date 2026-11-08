"""경로 접두사 상수 — 같은 문자열을 여러 곳에서 손으로 적지 않기 위한 유일한 출처.

FastAPI에서는 app/main.py의 include_router(prefix=...)가 모든 v1 라우터 앞에
이 접두사를 붙인다.

🚨 /api/v1 은 두 곳에서 필요하다 — ① 라우터 마운트 ② GET /auth/{provider}/status 가
   내려주는 start_url. 한 곳만 어긋나면 에러 없이 로그인만 조용히 깨진다.
   인증 쿠키는 #783 부터 `__Host-` 이름이라 Path=/ 로 고정이다 (routers/auth.py REFRESH_COOKIE).
   근거: docs/api/auth-kakao-v1.md §3-1 · §5-5 · M-01
"""

API_V1_PREFIX = "/api/v1"
"""계약서 §01 "경로는 모두 /api/v1 하위".

프론트 apps/web/src/lib/env.ts 도 오리진 뒤에 이 값을 직접 붙인다. 양쪽이 어긋나면
서버가 살아 있어도 프론트의 모든 호출이 404 가 된다.
"""

AUTH_PREFIX = "/auth"
"""인증 라우터의 접두사. /api/v1 은 마운트 시점에 붙으므로 여기 넣지 않는다."""
