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

ALLOW_METHODS = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
"""계약서가 쓰는 메서드만 적는다. `*` 로 열면 나중에 추가되는 메서드가 조용히 통과한다."""

ALLOW_HEADERS = ["Authorization", "Content-Type", "Idempotency-Key"]
"""프론트 apps/web/src/lib/api/client.ts 가 실제로 싣는 세 개.

Idempotency-Key 는 계약서 §01 의 재시도 안전 장치다 (docs/api/idempotency-v1.md).
빠지면 그 헤더를 싣는 요청만 preflight 에서 막혀 원인을 찾기 어렵다.
"""


def register_cors(app: FastAPI) -> None:
    """허용 오리진이 있을 때만 CORS 를 켠다.

    같은 오리진으로 배포하면 필요 없고, 비어 있는데 미들웨어를 달면 "켰는데 아무것도
    허용되지 않는" 상태가 된다 — 안 켠 것과 구분되지 않아 진단만 어려워진다.

    🚨 allow_credentials 를 켜지 않는다. 세션은 sessionStorage 의 Bearer 토큰으로
       오간다 (명세 §4-3). oauth_state 쿠키는 카카오 왕복(최상위 이동)에서만 쓰이고
       XHR 에 실리지 않으므로, 자격증명 동반 요청을 열어 줄 이유가 없다.
    """
    origins = settings.cors_allow_origins
    if not origins:
        log.info("CORS_ALLOW_ORIGINS 가 비어 있어 CORS 를 켜지 않는다")
        return

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=ALLOW_METHODS,
        allow_headers=ALLOW_HEADERS,
    )
