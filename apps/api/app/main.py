"""FastAPI 앱 진입점.

FastAPI는 라우터와 예외 처리기를 자동으로 찾지 않는다. 아래의
include_router와 register_error_handlers 호출이 실제 등록 지점이다.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api import health, llm_usage_writer, startup_report
from app.api.cors import register_cors
from app.api.errors import register_error_handlers
from app.api.metrics import MetricsMiddleware, start_metrics_server
from app.api.openapi import install_openapi
from app.api.runs import hold_purge, registry
from app.api.v1.router import v1_router
from app.core.alerts import configure_alerts
from app.core.config import settings
from app.core.constants import API_V1_PREFIX
from app.core.logging_config import configure_logging
from app.domains.memory.curator import trigger as curator_trigger
from app.integrations import discord
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


log = logging.getLogger(__name__)

DRAIN_SECONDS = 30.0
"""종료 때 남은 run · 표 미러 · Curator 를 기다리는 상한(초). 넘기면 취소한다 (#882).

uvicorn 은 SIGTERM 뒤 열린 요청 · SSE 를 먼저 기다린다(`--timeout-graceful-shutdown`, Dockerfile).
그래서 여기 오는 것은 화면이 안 붙은 run 과 run 뒤에 도는 Curator 다.
🚨 Dockerfile 의 그 값 + 이 값 + 취소 정리 5초 + 사용량 flush 5초
   < compose 의 api `stop_grace_period` — 넘으면 SIGKILL 이 먼저 와서 drain 이 중간에 끊긴다
   (docs/ops/deploy-v1.md §drain).
"""


async def drain_background() -> None:
    """남은 run → 그 run 의 표 미러 → Curator 순서로 기다린다. 상한을 넘긴 것은 취소한다.

    run 이 먼저다 — run 이 끝나며 close 를 넣어야 미러가 끝나고, Curator 를 띄우므로 run 이 다
    끝난 뒤에 나머지를 모은다. 미러를 안 기다리면 끝 신호가 표에 안 남아, 새 api 에서 같은
    Idempotency-Key 로 다시 누른 화면이 옛 run 을 재생받고 90초 뒤에야 끊긴다 (#897 리뷰).
    취소하면 러너의 finally 가 failed 를 내고 Idempotency 키를 놓는다. 안 하면 프로세스와 함께
    흔적 없이 사라진다 (uvicorn 은 정리 뒤 SIGTERM 을 다시 일으켜 죽는다 — #882 실측).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DRAIN_SECONDS
    stages = (
        ("run", registry.running_tasks),
        ("mirror", registry.running_mirrors),  # run 이 close 를 넣은 뒤라야 끝난다
        ("curator", curator_trigger.running_tasks),
    )
    for kind, running in stages:
        tasks = running()
        if not tasks:
            continue
        log.info("drain — %s %d개 기다림", kind, len(tasks))
        _, late = await asyncio.wait(tasks, timeout=max(0.0, deadline - loop.time()))
        if late:
            # WARNING 이다 — ERROR 는 Discord 로 가는데, 배포마다 울리면 알림이 무뎌진다
            log.warning(
                "drain — %s %d개가 %s초 안에 안 끝나 취소한다", kind, len(late), DRAIN_SECONDS
            )
            for task in late:
                task.cancel()
            # 정리 5초는 단계마다가 아니라 drain 전체에서 한 번 — 단계마다면 합계가 단계 수만큼 는다
            await asyncio.wait(late, timeout=max(0.0, deadline + 5 - loop.time()))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """프로세스에 하나씩 두는 외부 클라이언트를 만들고 닫는다.

    날씨 어댑터(#380) — 발표분 · 측정소 · 특보 캐시가 프로세스 메모리에 있어 하나를 같이 쓴다
    (`--workers 1` 전제, #134). 입력 창구가 run 마다 Activity 에 넘긴다. 공공데이터포털 키가
    없으면 만들지 않는다 — Activity 는 날씨를 묻지 않고 실내 전용으로 돈다.

    행사 어댑터(TourAPI)도 같다 — 하루치 전국 목록 · 소개정보 캐시가 프로세스 메모리에 있다.
    같은 공공데이터포털 키를 쓰고, 없으면 Activity 는 행사를 찾지 않는다.

    LLM 사용량 writer(#710)는 키와 상관없이 늘 띄운다 — 큐를 2초마다 `llm_usage` 표에 쓴다.
    프로세스마다 큐 · writer 가 하나라 `--workers` 와 무관하다. 종료 때는 먼저 멈춰서(취소)
    남은 큐를 쓰고 끝낸다. SIGKILL · OOM 이면 최대 2초분을 잃는다.

    뜰 때 한 번, 설정이 비어 꺼져 있는 기능을 한 줄로 남긴다 (`api/startup_report.py`). 서버를
    막지는 않는다 — 배포가 성공으로 끝난 뒤 무엇이 꺼진 채 도는지 로그 한 곳에서 보이게 할 뿐이다.
    """
    startup_report.log_at_startup(settings)
    has_key = bool(settings.DATA_GO_KR_SERVICE_KEY.strip())
    weather = PublicWeather() if has_key else None
    events = TourEvents() if has_key else None
    app.state.activity_weather = weather
    app.state.activity_events = events
    usage_writer = asyncio.create_task(llm_usage_writer.run_writer(), name="llm-usage-writer")
    # 만료된 되묻기 맥락을 1분마다 지운다 (#877). 처리방침이 "15분 안에 지운다" 라
    # 하루 배치로는 늦다
    hold_purger = asyncio.create_task(hold_purge.run_purger(), name="run-hold-purger")
    # 메트릭 창구 (#975) — import 가 아니라 여기서 연다. 테스트 · 스크립트도 app.main 을 import 한다
    metrics_server = (
        start_metrics_server(settings.METRICS_PORT, settings.METRICS_ALLOWED_IP)
        if settings.METRICS_PORT
        else None
    )
    try:
        yield
    finally:
        # 🚨 writer 를 멈추기 전에 — run · Curator 가 끝나며 남기는 사용량도 마지막 flush 에 실린다
        try:
            await drain_background()
        except Exception as exc:
            log.warning("drain 이 %s 로 중간에 멈췄다", type(exc).__name__)
        hold_purger.cancel()
        with suppress(asyncio.CancelledError):
            await hold_purger
        usage_writer.cancel()
        # writer 가 예외로 죽어 있었다면(run_writer 가 이미 알렸다) 그 예외가 여기서 나가면서
        # 아래 클라이언트 닫기를 건너뛰면 안 된다
        with suppress(asyncio.CancelledError, Exception):
            await usage_writer
        for client in (weather, events):
            if client is not None:
                await client.aclose()
        if metrics_server is not None:
            metrics_server.shutdown()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# 에러 봉투 핸들러 (계약서 §01). 등록을 잊으면 FastAPI 기본 {"detail": ...} 가 나가고
# 프론트 apps/web/src/lib/api/client.ts 가 code 를 못 읽는다. 그래서 맨 위에 둔다.
register_error_handlers(app)

# 브라우저에서 다른 오리진의 이 API 를 부를 수 있게 한다 (이슈 #34).
# 허용 오리진은 CORS_ALLOW_ORIGINS 환경변수에서만 오고, * 는 부팅에서 막힌다.
register_cors(app)

# 요청 수 · 응답 코드 · 응답 시간 (#975, app/api/metrics.py). 창구는 lifespan 에서 연다.
app.add_middleware(MetricsMiddleware)

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
