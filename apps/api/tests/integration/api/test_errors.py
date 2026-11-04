"""에러 봉투와 라우터 등록 순서 — 계약서 §01 · 명세 §3-6

ASGI 앱을 직접 호출해 상태 코드와 JSON 경로를 검증한다. DB를 보지 않으므로
Postgres 없이 돈다.

이 파일이 지키는 것 둘.
    ① 어떤 에러든 {"error": {"code", ...}} 봉투로 나간다 (프론트가 이 모양만 뜯는다)
    ② POST /auth/logout 이 /auth/{provider} 에 먹히지 않는다 (등록 순서)
"""

import logging

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.errors import register_error_handlers
from app.core.alerts import format_alert


async def test_unknown_path_returns_envelope(client):
    """FastAPI 기본 {"detail": "Not Found"} 가 아니라 계약 봉투로 나와야 한다."""
    response = await client.get("/api/v1/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    # detail 이 없으면 키 자체가 빠진다 (envelope 의 exclude_none).
    assert "detail" not in body["error"]


async def test_logout_is_not_swallowed_by_provider_route(client):
    """헤더 없이 부르면 401 unauthenticated.

    422 가 나오면 provider="logout" 으로 잡힌 것이고, 등록 순서가 뒤집혔다는 뜻이다.
    404 가 나오면 라우터가 마운트되지 않았다는 뜻이다.
    """
    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_method_not_allowed_is_hidden_as_not_found(client):
    """🚨 405 는 상태와 코드 모두 404 로 내린다.

    "메서드가 다르다" 는 곧 "그 경로는 있다" 는 뜻이다. 코드만 not_found 로 적고 상태를
    405 로 두면 숨긴 것이 아니다 — 공격자는 코드가 아니라 상태를 본다. Allow 헤더도
    같은 이유로 지운다.
    """
    response = await client.post("/health")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "allow" not in {name.lower() for name in response.headers}


async def test_health_stays_outside_v1(client):
    """마운트가 기존 운영 엔드포인트를 깨지 않았는지 (계약서 §01)."""
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/api/v1/health")).status_code == 404


# ---------------------------------------------------------------------------
# 처리되지 않은 예외 (#670) — 로그에도 메시지를 싣지 않는다. 메시지에 입력 원문이 섞일 수 있다
# ---------------------------------------------------------------------------

SECRET = "우리아이-원문-문장"


def _boom_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        try:
            raise KeyError(f"원인 {SECRET}")
        except KeyError as cause:
            raise ValueError(f"입력 {SECRET} 를 처리하지 못했다") from cause

    return app


async def test_처리되지_않은_예외는_종류와_위치만_로그에_남긴다(caplog):
    transport = ASGITransport(app=_boom_app(), raise_app_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert SECRET not in response.text
    assert "ValueError" in caplog.text
    assert "in boom" in caplog.text  # 어디서 났는지는 남는다
    assert SECRET not in caplog.text  # 연결된 예외(KeyError)의 메시지도


async def test_처리되지_않은_예외의_Discord_카드_제목은_예외_종류다(caplog):
    """exc_info 를 싣지 않아 제목을 직접 준다 — 아니면 제목이 "처리되지 않은 예외 %s" 다."""
    transport = ASGITransport(app=_boom_app(), raise_app_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/boom")

    [record] = [r for r in caplog.records if r.name == "app.api.errors"]
    card = str(format_alert(record, env="test"))
    assert "처리되지 않은 예외 ValueError" in card
    assert "%s" not in card
    assert SECRET not in card
