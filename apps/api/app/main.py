"""FastAPI 앱 진입점.

FastAPI는 라우터와 예외 처리기를 자동으로 찾지 않는다. 아래의
include_router와 register_error_handlers 호출이 실제 등록 지점이다.
"""

from fastapi import FastAPI

from app.api import health
from app.api.cors import register_cors
from app.api.errors import register_error_handlers
from app.api.v1.router import v1_router
from app.core.alerts import configure_alerts
from app.core.config import settings
from app.core.constants import API_V1_PREFIX
from app.core.logging_config import configure_logging
from app.integrations import discord

# 앱 로그(app.*)를 시간 · 레벨 · 이름과 함께 찍는다. uvicorn 은 자기 로거만 설정해서, 이게
# 없으면 info 는 버려지고 경고도 언제 난 건지 모르는 글자만 남는다 (#197 후속).
configure_logging()

# ERROR 이상은 Discord 로도 간다 — 서버 로그는 아무도 보고 있지 않다 (멘토 #267 2번). 주소가 비어
# 있으면 알림만 꺼진다. 로그 설정 뒤에 둔다 — 전송 실패를 알리는 WARNING 이 stderr 로 찍히려면
# app 로거의 처리기가 먼저 있어야 한다.
configure_alerts(
    settings.ALERT_WEBHOOK_URL, env=settings.APP_ENV, make_sender=discord.webhook_sender
)

app = FastAPI(title=settings.APP_NAME)

# 에러 봉투 핸들러 (계약서 §01). 등록을 잊으면 FastAPI 기본 {"detail": ...} 가 나가고
# 프론트 apps/web/src/lib/api/client.ts 가 code 를 못 읽는다. 그래서 맨 위에 둔다.
register_error_handlers(app)

# 브라우저에서 다른 오리진의 이 API 를 부를 수 있게 한다 (이슈 #34).
# 허용 오리진은 CORS_ALLOW_ORIGINS 환경변수에서만 오고, * 는 부팅에서 막힌다.
register_cors(app)

# 헬스체크는 /api/v1 밖에 둔다.
# API 계약서 §01: "경로는 모두 /api/v1 하위 · 인증 예외 없음".
# 로드밸런서와 컨테이너 healthcheck 는 토큰 없이 호출해야 하므로,
# 계약이 적용되는 /api/v1 네임스페이스 밖의 운영용 엔드포인트로 분리한다.
app.include_router(health.router)

# 🚨 /api/v1 공통 접두사는 여기서 한 번만 붙인다.
#    라우터가 각자 붙이면 v2 때 전부 고쳐야 하고, 프론트 apps/web/src/lib/env.ts 가
#    이미 오리진 뒤에 /api/v1 을 붙이고 있어서 양쪽이 어긋나면 즉시 404 다.
app.include_router(v1_router, prefix=API_V1_PREFIX)
