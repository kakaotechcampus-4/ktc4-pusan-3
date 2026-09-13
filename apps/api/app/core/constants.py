"""경로 접두사 상수 — 같은 문자열을 여러 곳에서 손으로 적지 않기 위한 유일한 출처.

Spring 대응: application.yml 의 server.servlet.context-path.
    거기서는 설정 한 줄이 모든 컨트롤러 앞에 붙지만, FastAPI 는
    include_router(prefix=...) 로 직접 붙인다 (app/main.py).

🚨 /api/v1 은 세 곳에서 필요하다 — ① 라우터 마운트 ② oauth_state 쿠키의 Path
   ③ GET /auth/{provider}/status 가 내려주는 start_url.
   한 곳만 어긋나면 쿠키가 콜백에 실리지 않고, 에러 없이 로그인만 조용히 깨진다.
   근거: docs/api/auth-kakao-v1.md §3-1 · §5-5 · M-01
"""

API_V1_PREFIX = "/api/v1"
"""계약서 §01 "경로는 모두 /api/v1 하위".

프론트 apps/web/src/lib/env.ts 도 오리진 뒤에 이 값을 직접 붙인다. 양쪽이 어긋나면
서버가 살아 있어도 프론트의 모든 호출이 404 가 된다.
"""

AUTH_PREFIX = "/auth"
"""인증 라우터의 접두사. /api/v1 은 마운트 시점에 붙으므로 여기 넣지 않는다."""

OAUTH_COOKIE_PATH = f"{API_V1_PREFIX}{AUTH_PREFIX}"
"""oauth_state 쿠키의 Path (명세 §5-5).

쿠키를 인증 요청에만 실리게 좁힌다. 이 값을 문자열로 다시 적지 말고 여기서 가져간다 —
마운트 지점을 바꾸면 쿠키 Path 도 함께 따라와야 한다.
"""
