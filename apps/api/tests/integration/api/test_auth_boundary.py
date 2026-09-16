"""인증 경계와 CORS — 루트 CLAUDE.md §9 · 이슈 #34

여기서 지키는 것 둘.
    ① 무인증으로 열린 경로가 보안 목록 5개를 넘지 않는다
    ② 브라우저가 부를 수 있는 오리진이 환경변수로 정한 목록뿐이다

①이 중요한 이유 — 인증을 라우터마다 손으로 다는 구조에서는 한 번 깜빡한 엔드포인트가
조용히 공개된다. 리뷰어의 눈이 아니라 이 테스트가 막는다.
"""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.cors import register_cors
from app.api.deps.auth import get_current_parent
from app.core.config import Settings, settings
from app.core.constants import API_V1_PREFIX
from app.main import app

PUBLIC_ALLOWLIST = {
    ("GET", "/api/v1/auth/{provider}/status"),
    ("GET", "/api/v1/auth/{provider}"),
    ("GET", "/api/v1/auth/{provider}/callback"),
    ("POST", "/api/v1/auth/{provider}"),
    ("POST", "/api/v1/auth/{provider}/signup"),
}
"""루트 CLAUDE.md §9 의 무인증 5개. 이 집합을 늘리려면 그 목록과 API 계약을 먼저 고친다.

아직 POST 둘은 구현 전이라 실제로 열린 것은 3개다. 그래서 "정확히 같다" 가 아니라
"이 목록을 벗어나지 않는다" 로 건다 — 구현이 늘어도 테스트를 고칠 필요가 없고,
목록 밖이 열리는 순간 실패한다.
"""

ALLOWED_ORIGIN = "https://app.example.test"


def dependency_calls(dependant):
    """그 라우트가 거치는 의존성 함수를 전부 훑는다 (중첩 포함)."""
    for sub in dependant.dependencies:
        yield sub.call
        yield from dependency_calls(sub)


def public_routes() -> set[tuple[str, str]]:
    """/api/v1 아래에서 인증 의존성을 거치지 않는 (메서드, 경로) 집합."""
    found = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(API_V1_PREFIX):
            continue
        if get_current_parent in set(dependency_calls(route.dependant)):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            found.add((method, route.path))
    return found


def test_public_routes_never_exceed_the_security_allowlist():
    """🚨 무인증으로 열린 경로는 로그인용 5개를 넘지 않는다 (루트 CLAUDE.md §9)."""
    assert public_routes() <= PUBLIC_ALLOWLIST


def test_logout_requires_authentication():
    """로그아웃은 무인증 목록에 없다. 세션을 지우는 일이라 누구인지 알아야 한다."""
    assert ("POST", "/api/v1/auth/logout") not in public_routes()


async def test_unauthenticated_request_to_protected_route_is_401(client):
    """보호 라우터는 헤더가 없으면 봉투로 401 을 낸다 (계약서 §01)."""
    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


# ── CORS ─────────────────────────────────────────────────────────────────────


def cors_app(monkeypatch, origins: str) -> FastAPI:
    """같은 등록 함수로 만든 작은 앱. 미들웨어는 앱을 만들 때 한 번 붙으므로,
    설정을 바꿔 가며 보려면 앱을 새로 세우는 편이 정직하다."""
    monkeypatch.setattr(settings, "CORS_ALLOW_ORIGINS", origins)
    fresh = FastAPI()
    register_cors(fresh)

    @fresh.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    return fresh


async def preflight(target: FastAPI, origin: str, header: str = "authorization"):
    transport = ASGITransport(app=target)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.options(
            "/ping",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": header,
            },
        )


async def test_preflight_passes_for_allowed_origin(monkeypatch):
    """허용 오리진은 preflight 를 통과한다. 없으면 프론트의 모든 호출이 막힌다."""
    response = await preflight(cors_app(monkeypatch, ALLOWED_ORIGIN), ALLOWED_ORIGIN)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


async def test_preflight_allows_idempotency_key(monkeypatch):
    """Idempotency-Key 가 빠지면 그 헤더를 싣는 요청만 조용히 막혀 원인을 찾기 어렵다."""
    response = await preflight(
        cors_app(monkeypatch, ALLOWED_ORIGIN), ALLOWED_ORIGIN, header="idempotency-key"
    )

    assert response.status_code == 200
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()


async def test_unknown_origin_is_blocked(monkeypatch):
    """🚨 목록 밖 오리진에는 허용 헤더를 내리지 않는다."""
    response = await preflight(cors_app(monkeypatch, ALLOWED_ORIGIN), "https://evil.example.test")

    assert "access-control-allow-origin" not in response.headers


async def test_cors_stays_off_when_no_origin_is_configured(monkeypatch):
    """비어 있으면 켜지 않는다 — 같은 오리진 배포에서는 필요 없다."""
    response = await preflight(cors_app(monkeypatch, ""), ALLOWED_ORIGIN)

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("blank", ["", "   "])
def test_missing_web_return_url_fails_at_boot(blank):
    """🚨 #45. 웹 복귀 URL 이 없으면 서버가 뜨지 않는다.

    카카오 키와 달리 "없어도 뜨고 ready: false 로 알린다" 를 쓰지 않는다. 이 값이 없으면
    인증이 아예 성립하지 않기 때문이다 — 성공도 실패도 전부 여기로 돌아간다(§2-3).
    빠진 채로 뜨면 사용자가 카카오 인증을 **마친 뒤에** 깨진다.

    키만 두고 값을 비우는 실수가 흔해서 빈 문자열도 함께 막는다.
    """
    with pytest.raises(ValidationError):
        Settings(AUTH_RETURN_URL_WEB=blank)


def test_wildcard_origin_fails_at_boot():
    """🚨 * 는 부팅에서 끊는다.

    이 API 는 Bearer 토큰으로 아이 정보를 내려준다. 런타임에 조용히 넓어지는 것이
    제일 나쁘므로 설정을 읽는 순간 실패시킨다.
    """
    with pytest.raises(ValidationError):
        Settings(CORS_ALLOW_ORIGINS=f"{ALLOWED_ORIGIN},*")
