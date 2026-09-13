"""FastAPI 앱 진입점.

FastAPI는 라우터와 예외 처리기를 자동으로 찾지 않는다. 아래의
include_router와 register_error_handlers 호출이 실제 등록 지점이다.
"""

from fastapi import FastAPI

from app.api import health
from app.api.errors import register_error_handlers
from app.api.v1.router import v1_router
from app.core.config import settings
from app.core.constants import API_V1_PREFIX

app = FastAPI(title=settings.APP_NAME)

# 에러 봉투 핸들러 (계약서 §01). 등록을 잊으면 FastAPI 기본 {"detail": ...} 가 나가고
# 프론트 apps/web/src/lib/api/client.ts 가 code 를 못 읽는다. 그래서 맨 위에 둔다.
register_error_handlers(app)

# 헬스체크는 /api/v1 밖에 둔다.
# API 계약서 §01: "경로는 모두 /api/v1 하위 · 인증 예외 없음".
# 로드밸런서와 컨테이너 healthcheck 는 토큰 없이 호출해야 하므로,
# 계약이 적용되는 /api/v1 네임스페이스 밖의 운영용 엔드포인트로 분리한다.
app.include_router(health.router)

# 🚨 /api/v1 공통 접두사는 여기서 한 번만 붙인다.
#    라우터가 각자 붙이면 v2 때 전부 고쳐야 하고, 프론트 apps/web/src/lib/env.ts 가
#    이미 오리진 뒤에 /api/v1 을 붙이고 있어서 양쪽이 어긋나면 즉시 404 다.
app.include_router(v1_router, prefix=API_V1_PREFIX)
