"""운영 api 메트릭의 라벨 (#975).

🚨 라벨이 밖에서 늘어나면 안 된다 — 아무 경로 · 메서드로 보내도 라우트 틀 · `unmatched` ·
   5개 메서드 · `OTHER` 밖의 값이 생기지 않는지, 경로 원문(id)이 라벨에 안 들어가는지를 건다.
"""

import asyncio
import socket
import urllib.error
import urllib.request

from fastapi import APIRouter, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.api.metrics import REGISTRY, MetricsMiddleware, start_metrics_server
from app.main import app as real_app


def _count(method: str, route: str, code: str) -> float:
    value = REGISTRY.get_sample_value(
        "api_http_response_seconds_count", {"method": method, "route": route, "code": code}
    )
    return value or 0.0


def _seconds(method: str, route: str, code: str) -> float:
    value = REGISTRY.get_sample_value(
        "api_http_response_seconds_sum", {"method": method, "route": route, "code": code}
    )
    return value or 0.0


def _app() -> FastAPI:
    router = APIRouter()

    @router.get("/m975/items/{item_id}")
    async def item(item_id: str) -> dict:
        return {"id": item_id}

    @router.get("/m975/boom")
    async def boom() -> dict:
        raise RuntimeError("x")

    @router.get("/m975/stream")
    async def stream() -> StreamingResponse:
        async def body():
            yield b"a"
            await asyncio.sleep(0.3)
            yield b"b"

        return StreamingResponse(body(), media_type="text/event-stream")

    app = FastAPI()

    @app.get("/health")
    async def health() -> dict:
        return {}

    app.include_router(router, prefix="/api/v1")
    app.add_middleware(MetricsMiddleware)
    return app


def test_라우트_틀로_세고_경로_원문은_라벨에_없다():
    before = _count("GET", "/m975/items/{item_id}", "2xx")
    TestClient(_app()).get("/api/v1/m975/items/secret-id-123")

    assert _count("GET", "/m975/items/{item_id}", "2xx") == before + 1
    labels = [s.labels for m in REGISTRY.collect() for s in m.samples]
    assert not any("secret-id-123" in v for lbl in labels for v in lbl.values())


def test_없는_경로와_안_맞는_메서드는_unmatched_하나다():
    client = TestClient(_app())
    before_get = _count("GET", "unmatched", "4xx")
    before_other = _count("OTHER", "unmatched", "4xx")

    client.get("/api/v1/m975/nope-1")
    client.get("/api/v1/m975/nope-2")
    client.request("PROPFIND", "/api/v1/m975/items/1")
    client.request("PURGE", "/api/v1/m975/items/2")

    assert _count("GET", "unmatched", "4xx") == before_get + 2
    assert _count("OTHER", "unmatched", "4xx") == before_other + 2
    routes = {s.labels["route"] for m in REGISTRY.collect() for s in m.samples}
    assert not any("nope" in r for r in routes)


def test_있는_라우트에_다른_메서드여도_unmatched_다():
    """FastAPI 0.141 은 메서드가 안 맞아도 scope 에 라우트를 써 둔다.

    그대로 세면 경로 × 메서드만큼 는다.
    """
    before = _count("DELETE", "unmatched", "4xx")
    TestClient(_app()).delete("/api/v1/m975/items/1")

    assert _count("DELETE", "unmatched", "4xx") == before + 1
    assert _count("DELETE", "/m975/items/{item_id}", "4xx") == 0


def test_처리_안_된_예외도_5xx_로_센다():
    before = _count("GET", "/m975/boom", "5xx")
    response = TestClient(_app(), raise_server_exceptions=False).get("/api/v1/m975/boom")

    assert response.status_code == 500
    assert _count("GET", "/m975/boom", "5xx") == before + 1


def test_스트리밍은_응답_시작까지만_잰다():
    before = _seconds("GET", "/m975/stream", "2xx")
    TestClient(_app()).get("/api/v1/m975/stream")

    assert _seconds("GET", "/m975/stream", "2xx") - before < 0.25


def test_헬스체크는_세지_않는다():
    before = sum(_count("GET", r, "2xx") for r in ("/health", "unmatched"))
    TestClient(_app()).get("/health")

    assert sum(_count("GET", r, "2xx") for r in ("/health", "unmatched")) == before


def test_실제_앱에_미들웨어가_붙어_있다():
    assert any(m.cls is MetricsMiddleware for m in real_app.user_middleware)


def test_창구는_허용한_주소만_받는다():
    allowed = start_metrics_server(0, "127.0.0.1")
    denied = start_metrics_server(0, "172.30.0.11")
    assert allowed is not None and denied is not None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{allowed.server_port}/metrics") as r:
            assert r.status == 200
            assert b"api_http_response_seconds" in r.read()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{denied.server_port}/metrics")
            raise AssertionError("403 이어야 한다")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        allowed.shutdown()
        denied.shutdown()


def test_포트를_못_잡아도_api_는_뜬다():
    first = start_metrics_server(0, "127.0.0.1")
    assert first is not None
    try:
        assert start_metrics_server(first.server_port, "127.0.0.1") is None
    finally:
        first.shutdown()


def test_연결만_붙잡는_곳이_있어도_수집은_된다():
    """edge 의 다른 컨테이너가 연결만 열고 안 보내도 Alloy 가 긁을 수 있어야 한다 (#980 리뷰).

    한 번에 하나만 받는 서버면 시간 초과로 실패해 `up == 0` 이 거짓으로 운다.
    """
    server = start_metrics_server(0, "127.0.0.1")
    assert server is not None
    idle = socket.create_connection(("127.0.0.1", server.server_port))
    try:
        url = f"http://127.0.0.1:{server.server_port}/metrics"
        with urllib.request.urlopen(url, timeout=2) as r:
            assert r.status == 200
    finally:
        idle.close()
        server.shutdown()
