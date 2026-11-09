"""FastAPI 앱 진입점.

FastAPI는 라우터와 예외 처리기를 자동으로 찾지 않는다. 아래의
include_router와 register_error_handlers 호출이 실제 등록 지점이다.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api import health, llm_usage_writer
from app.api.cors import register_cors
from app.api.errors import register_error_handlers
from app.api.openapi import install_openapi
from app.api.v1.router import v1_router
from app.core.alerts import configure_alerts
from app.core.config import settings
from app.core.constants import API_V1_PREFIX
from app.core.logging_config import configure_logging
from app.integrations import discord
from app.integrations.hospital.client import NmcHospitals
from app.integrations.tour.events import TourEvents
from app.integrations.weather.source import PublicWeather

# 앱 로그(app.*)를 시간 · 레벨 · 이름과 함께 찍는다. uvicorn 은 자기 로거만 설정해서, 이게
# 없으면 info 는 버려지고 경고도 언제 난 건지 모르는 글자만 남는다 (#197 후속).
configure_logging()

# ERROR 이상은 Discord 로도 간다 (멘토 #267 2번). 주소가 비어 있으면 알림만 꺼진다 — register_cors
# 와 같은 모양. 🚨 configure_logging() 뒤에 — 그쪽 dictConfig 가 app 로거의 처리기를 갈아 끼워서,
# 먼저 달면 사라진다.
if settings.alert_webhook_url:
    configure_alerts(discord.webhook_sender(settings.alert_webhook_url), env=settings.APP_ENV)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """프로세스에 하나씩 두는 외부 클라이언트를 만들고 닫는다.

    날씨 어댑터(#380) — 발표분 · 측정소 · 특보 캐시가 프로세스 메모리에 있어 하나를 같이 쓴다
    (`--workers 1` 전제, #134). 입력 창구가 run 마다 Activity 에 넘긴다. 공공데이터포털 키가
    없으면 만들지 않는다 — Activity 는 날씨를 묻지 않고 실내 전용으로 돈다.

    행사 어댑터(TourAPI)도 같다 — 하루치 전국 목록 · 소개정보 캐시가 프로세스 메모리에 있다.
    같은 공공데이터포털 키를 쓰고, 없으면 Activity 는 행사를 찾지 않는다.

    병원 어댑터(국립중앙의료원, #713)도 같다 — 시도별 기관 목록 캐시가 프로세스 메모리에 있다.
    같은 키를 쓰고, 없으면 Health 병원 찾기가 `place.unavailable` 로 닫힌다.

    LLM 사용량 writer(#710)는 키와 상관없이 늘 띄운다 — 큐를 2초마다 `llm_usage` 표에 쓴다.
    프로세스마다 큐 · writer 가 하나라 `--workers` 와 무관하다. 종료 때는 먼저 멈춰서(취소)
    남은 큐를 쓰고 끝낸다. SIGKILL · OOM 이면 최대 2초분을 잃는다.
    """
    has_key = bool(settings.DATA_GO_KR_SERVICE_KEY.strip())
    weather = PublicWeather() if has_key else None
    events = TourEvents() if has_key else None
    hospitals = NmcHospitals() if has_key else None
    app.state.activity_weather = weather
    app.state.activity_events = events
    app.state.health_places = hospitals
    usage_writer = asyncio.create_task(llm_usage_writer.run_writer(), name="llm-usage-writer")
    try:
        yield
    finally:
        usage_writer.cancel()
        # writer 가 예외로 죽어 있었다면(run_writer 가 이미 알렸다) 그 예외가 여기서 나가면서
        # 아래 클라이언트 닫기를 건너뛰면 안 된다
        with suppress(asyncio.CancelledError, Exception):
            await usage_writer
        for client in (weather, events, hospitals):
            if client is not None:
                await client.aclose()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

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

# Swagger 의 형식 오류 모양(400 · 422)을 실제 봉투와 맞춘다 — FastAPI 기본 422 모양은 실제로
# 오지 않는다 (#438 · docs/api/errors-v1.md). 라우터를 다 붙인 뒤에 건다.
install_openapi(app)
