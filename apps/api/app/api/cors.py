"""브라우저에서 이 API 를 부를 수 있게 하는 최소 설정 — 이슈 #34

프론트(apps/web)는 NEXT_PUBLIC_API_BASE_URL 로 **다른 오리진**의 이 서버를 부르고,
Authorization 헤더를 싣기 때문에 브라우저가 preflight 를 먼저 보낸다. 이게 없으면
서버 로그에는 200 이 찍히는데 화면에서는 네트워크 오류만 보인다.

🚨 허용 오리진은 환경변수로만 온다. `*` 는 Settings 검증에서 부팅을 막는다 —
   이 API 는 Bearer 토큰으로 아이 정보를 내려주므로 아무 사이트나 부를 수 있으면 안 된다.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

log = logging.getLogger(__name__)

ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
"""계약서가 쓰는 메서드만 적는다. `*` 로 열면 나중에 추가되는 메서드가 조용히 통과한다.

🚨 새 메서드를 쓰는 라우트를 만들면 여기에도 더한다. 빠지면 서버 테스트는 통과하고 브라우저에서만
   preflight 가 400 으로 막힌다 (`PUT /diary` 가 그랬다).
   test_auth_boundary.py 가 라우트와 이 목록을 맞춰 본다.
"""

ALLOW_HEADERS = ["Authorization", "Content-Type", "Idempotency-Key"]
"""프론트 apps/web/src/lib/api/client.ts 가 실제로 싣는 세 개.

Idempotency-Key 는 계약서 §01 의 재시도 안전 장치다 (docs/api/idempotency-v1.md).
빠지면 그 헤더를 싣는 요청만 preflight 에서 막혀 원인을 찾기 어렵다.
"""


def register_cors(app: FastAPI) -> None:
    """허용 오리진이 있을 때만 CORS 를 켠다.

    같은 오리진으로 배포하면 필요 없고, 비어 있는데 미들웨어를 달면 "켰는데 아무것도
    허용되지 않는" 상태가 된다 — 안 켠 것과 구분되지 않아 진단만 어려워진다.

    allow_credentials 를 켠다 (#466). refresh 는 HttpOnly 쿠키(icatch_rt)로 오가고, 웹은
    교환 · 가입 · refresh · 로그아웃 · 탈퇴를 `credentials: "include"` 로 부른다. 이게 꺼져
    있으면 브라우저가 그 응답을 스크립트에 넘기지 않고 Set-Cookie 도 버린다.

    🚨 응답을 읽을 수 있는 오리진은 기존 허용 목록(환경변수, `*` 는 부팅에서 막힌다)으로 제한된다.
       CORS 가 막는 것은 응답 읽기뿐이다 — preflight 가 필요 없는 단순 요청은 서버에서 실행된다.
       쿠키는 SameSite=Strict 라 다른 사이트의 요청에는 실리지 않지만, 같은 사이트의 다른
       오리진까지 막지는 않는다. Path=/api/v1/auth 라 일반 API 요청에는 실리지 않는다.
    """
    origins = settings.cors_allow_origins
    if not origins:
        log.info("CORS_ALLOW_ORIGINS 가 비어 있어 CORS 를 켜지 않는다")
        return

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=ALLOW_METHODS,
        allow_headers=ALLOW_HEADERS,
    )
