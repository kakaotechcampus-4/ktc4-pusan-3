"""운영 api 의 요청 수 · 응답 코드 · 응답 시간 — Alloy 가 긁어 Grafana Cloud 로 보낸다 (#975).

Caddy 메트릭은 prod · dev 네 사이트 합계라 dev 의 5xx 가 운영 알림에 섞인다. 이 숫자는 이 프로세스가
받은 요청만 센다 — Alloy 는 운영 컨테이너(`ktc4-api`)만 긁는다 (deploy/alloy/config.alloy).

🚨 라벨은 메서드 · 라우트 틀(`/children/{child_id}`) · 응답 코드 묶음(`2xx`)뿐이다.
   경로 원문 · id · IP 를 싣지 않는다 — 다 Grafana Cloud 로 나간다 (루트 CLAUDE.md §2 개인정보).
🚨 라벨 값은 밖에서 정할 수 없게 묶는다 — 인터넷 누구나 요청을 보내 시계열을 늘릴 수 있다
   (#946 M1 · #975 검토).
   - 없는 경로 · 메서드가 안 맞는 경로는 `route="unmatched"` 하나다. 메서드가 안 맞아도 라우터가
     라우트를 채워서, 그대로 두면 있는 경로 × 아무 메서드(uvicorn 이 통과시키는 것만 35개)만큼 는다
   - 라우트 틀에 `/api/v1` 이 안 붙는다 — FastAPI 0.14x 의 include_router 가 라우트를 감싸기만 한다
   - 메서드는 계약서가 쓰는 5개 밖이면 `OTHER`
   - 응답 시간은 구간(bucket) 없이 합계 · 개수만 — 평균으로 본다. Caddy 와 같다
🚨 응답 시간은 응답이 시작될 때까지다 — SSE(`/runs/{rid}/events`)는 run 이 끝날 때까지 열려 있어
   끝까지 재면 평균이 망가진다. Caddy 의 response_duration 과 같은 기준.
🚨 처리 안 된 예외의 500 은 이 미들웨어 바깥(Starlette ServerErrorMiddleware)에서 나간다 — 여기서는
   응답 시작이 안 보이고 예외만 올라온다. 그래서 응답 코드 기본값을 500 으로 두고 finally 에서 센다.
"""

import logging
import threading
import time
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client import CollectorRegistry, Summary, disable_created_metrics, make_wsgi_app
from prometheus_client.exposition import ThreadingWSGIServer
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

# `_created` 시계열(카운터마다 하나 더)을 끈다 — 쓰지 않고 시계열만 두 배가 된다
disable_created_metrics()

REGISTRY = CollectorRegistry()
"""이 모듈의 메트릭만 담는다 — 기본 registry 의 프로세스 · 파이썬 · GC 메트릭은 내보내지 않는다."""

RESPONSE_SECONDS = Summary(
    "api_http_response_seconds",
    "요청을 받아 응답을 시작할 때까지 걸린 시간(초). _count 가 요청 수다",
    ["method", "route", "code"],
    registry=REGISTRY,
)

METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
SKIP_PATHS = frozenset({"/health", "/health/ready"})
"""컨테이너 헬스체크(30초마다) · health-alert.sh 가 부르는 것 — 세면 요청 수 · 평균이 흐려진다."""


class MetricsMiddleware:
    """순수 ASGI — 응답 본문(SSE 포함)을 건드리지 않고 응답 시작만 본다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500
        elapsed: float | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status, elapsed
            if message["type"] == "http.response.start":
                status = message["status"]
                elapsed = time.perf_counter() - started
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # 라우터가 같은 scope 에 고른 라우트를 써 둔다. 🚨 메서드가 안 맞아도 써 둔다
            # (FastAPI 0.141 실측 — PROPFIND · OPTIONS 가 404 인데 route 가 있다).
            # 그래서 그 라우트의 메서드인지 다시 본다
            route = scope.get("route")
            path = getattr(route, "path_format", None)
            if path is None or scope["method"] not in getattr(route, "methods", ()):
                path = "unmatched"
            method = scope["method"] if scope["method"] in METHODS else "OTHER"
            RESPONSE_SECONDS.labels(method, path, f"{status // 100}xx").observe(
                elapsed if elapsed is not None else time.perf_counter() - started
            )


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — 부모 시그니처
        pass  # 1분마다 긁는 요청을 stderr 에 찍지 않는다


def start_metrics_server(port: int, allowed_ip: str) -> WSGIServer | None:
    """`port` 에 메트릭 창구를 연다. `allowed_ip` 가 아닌 곳에서 오면 403.

    🚨 :8000 이 아닌 다른 포트다 — Caddy 는 :8000 만 넘기므로 인터넷에서 닿지 않는다. 그래도
       edge 네트워크의 다른 컨테이너(dev 의 develop 코드 포함)는 닿아서 Alloy 주소만 받는다
       — Caddy :2020 과 같다.
    🚨 실패해도 api 는 뜬다 — 감시 도구가 배포를 막지 않는다 (docs/ops/logging-alerts-v1.md §7).
    `--workers 1` 전제다 (#134) — 워커를 늘리면 두 번째 워커가 같은 포트를 못 잡는다.
    """
    metrics_app = make_wsgi_app(REGISTRY)

    def guarded(environ, start_response):  # type: ignore[no-untyped-def]
        if environ.get("REMOTE_ADDR") != allowed_ip:
            start_response("403 Forbidden", [("Content-Length", "0")])
            return [b""]
        return metrics_app(environ, start_response)

    try:
        # 요청마다 스레드 — 한 연결이 아무것도 안 보내고 붙잡아도 Alloy 수집이 막히지 않는다
        server = make_server(
            "0.0.0.0",  # noqa: S104
            port,
            guarded,
            server_class=ThreadingWSGIServer,
            handler_class=_QuietHandler,
        )
    except OSError as exc:
        log.warning("메트릭 창구를 :%d 에 못 열었다 — 메트릭만 빈다 (%s)", port, type(exc).__name__)
        return None
    threading.Thread(target=server.serve_forever, name="metrics-server", daemon=True).start()
    log.info("메트릭 창구 :%d (받는 곳 %s)", port, allowed_ip)
    return server
