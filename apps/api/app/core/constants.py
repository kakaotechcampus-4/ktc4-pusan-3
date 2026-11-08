"""경로 접두사 상수 — 같은 문자열을 여러 곳에서 손으로 적지 않기 위한 유일한 출처.

FastAPI에서는 app/main.py의 include_router(prefix=...)가 모든 v1 라우터 앞에
이 접두사를 붙인다.

🚨 /api/v1 은 두 곳에서 필요하다 — ① 라우터 마운트 ② GET /auth/{provider}/status 가
   내려주는 start_url. 한 곳만 어긋나면 에러 없이 로그인만 조용히 깨진다.
   인증 쿠키는 #783 부터 `__Host-` 이름이라 Path=/ 로 고정이다 (AUTH_COOKIE_PATH 참조).
   근거: docs/api/auth-kakao-v1.md §3-1 · §5-5 · M-01
"""

API_V1_PREFIX = "/api/v1"
"""계약서 §01 "경로는 모두 /api/v1 하위".

프론트 apps/web/src/lib/env.ts 도 오리진 뒤에 이 값을 직접 붙인다. 양쪽이 어긋나면
서버가 살아 있어도 프론트의 모든 호출이 404 가 된다.
"""

AUTH_PREFIX = "/auth"
"""인증 라우터의 접두사. /api/v1 은 마운트 시점에 붙으므로 여기 넣지 않는다."""

AUTH_COOKIE_PATH = f"{API_V1_PREFIX}{AUTH_PREFIX}"
"""#783 이전 인증 쿠키(oauth_state · icatch_rt)의 Path (명세 §5-5 · #466).

지금 쿠키는 `__Host-` 이름이라 Path=/ 가 강제다. 이 값은 옛 icatch_rt 를 지울 때만 쓴다 —
심을 때와 지울 때 Path 가 다르면 지워지지 않는다.
TODO(#783): 옛 이름 대체 읽기를 뺄 때(routers/auth.py 의 LEGACY_REFRESH_COOKIE) 함께 뺀다.
"""
