"""무인증 라우터 3개 — 명세 docs/api/auth-kakao-v1.md §3-1 · §3-2 · §3-3

DB 를 보지 않는 경로만 여기 있다. 콜백의 성공 경로는 auth_handoff 를 쓰므로 Postgres 가
필요해 db_client 로 따로 덮는다 (A-01~A-05).

여기서 지키는 것 넷.
    ① 죽은 버튼을 만들지 않는다 — ready 와 start_url (§3-1 · A-20)
    ② 실패도 302 다. 봉투가 새어 나오면 안 된다 (§8-2 · A-06 · A-07 · A-10)
    ③ state 가 어긋나면 인가 코드 교환을 시도하지 않는다 (A-08 · A-09)
    ④ 카카오가 죽으면 기본값으로 넘기지 않는다 (A-16 · A-17)
"""

from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import pytest

from app.api.v1.routers import auth as auth_router
from app.core.config import settings
from app.integrations.kakao.client import KakaoApiError

BIND = "A" * 43
"""base64url 43자 = 256비트 (§3-2)."""

RETURN_WEB = "https://app.example.test/auth/callback"
RETURN_APP = "icatch://auth"
CALLBACK = "https://api.example.test/api/v1/auth/kakao/callback"


@pytest.fixture
def configured(monkeypatch):
    """카카오 설정이 갖춰진 서버. 값은 전부 가짜다."""
    monkeypatch.setattr(settings, "KAKAO_REST_API_KEY", "test-rest-api-key")
    monkeypatch.setattr(settings, "KAKAO_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "KAKAO_CALLBACK_URL", CALLBACK)
    monkeypatch.setattr(settings, "AUTH_RETURN_URL_WEB", RETURN_WEB)
    monkeypatch.setattr(settings, "AUTH_RETURN_URL_APP", RETURN_APP)


@pytest.fixture
def unconfigured(monkeypatch):
    """카카오 키가 없는 서버. 복귀 URL 은 있어야 302 로 거절할 수 있다."""
    monkeypatch.setattr(settings, "KAKAO_REST_API_KEY", None)
    monkeypatch.setattr(settings, "KAKAO_CLIENT_SECRET", None)
    monkeypatch.setattr(settings, "KAKAO_CALLBACK_URL", None)
    monkeypatch.setattr(settings, "AUTH_RETURN_URL_WEB", RETURN_WEB)
    monkeypatch.setattr(settings, "AUTH_RETURN_URL_APP", RETURN_APP)


def redirect_query(response) -> tuple[str, dict]:
    """302 응답의 복귀 대상과 쿼리를 돌려준다."""
    assert response.status_code == 302, response.text
    location = urlparse(response.headers["location"])
    base = location._replace(query="").geturl()
    return base, parse_qs(location.query)


class FakeKakao:
    """카카오 호출을 기록만 하는 대역. 실패를 시키려면 fail 에 예외를 넣는다."""

    def __init__(self, *, user_id: str = "1234567890", fail: Exception | None = None):
        self.user_id = user_id
        self.fail = fail
        self.calls: list[str] = []

    async def exchange_code(self, code: str) -> str:
        self.calls.append("exchange")
        if self.fail is not None:
            raise self.fail
        return "kakao-access-token"

    async def fetch_user_id(self, access_token: str) -> str:
        self.calls.append("user_id")
        return self.user_id

    async def revoke_token(self, access_token: str) -> None:
        self.calls.append("revoke")


def patch_kakao(monkeypatch, fake: FakeKakao) -> FakeKakao:
    @asynccontextmanager
    async def fake_client():
        yield fake

    monkeypatch.setattr(auth_router, "kakao_client", fake_client)
    return fake


async def start_and_take_cookie(client, target: str = "web") -> tuple[str, str]:
    """실제로 시작을 태워 (쿠키 값, state) 를 얻는다. 테스트가 쿠키를 손으로 만들지 않는다."""
    response = await client.get(f"/api/v1/auth/kakao?client={target}&bind={BIND}")
    assert response.status_code == 302
    cookie = response.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    _, query = redirect_query(response)
    return cookie, query["state"][0]


# ── §3-1 상태 조회 ────────────────────────────────────────────────────────────


async def test_status_is_not_ready_without_keys(client, unconfigured):
    """A-20. 설정이 없으면 ready: false — 프론트가 버튼을 비활성화한다."""
    body = (await client.get("/api/v1/auth/kakao/status")).json()

    assert body["ready"] is False
    assert set(body["missing_keys"]) == {
        "KAKAO_REST_API_KEY",
        "KAKAO_CLIENT_SECRET",
        "KAKAO_CALLBACK_URL",
    }


async def test_status_hides_missing_keys_in_production(client, unconfigured, monkeypatch):
    """🚨 A-20. 프로덕션은 ready: false 만 내린다.

    무인증 엔드포인트가 "어떤 설정이 비었는지" 를 알려주면 정찰에 쓰인다 (§3-1).
    """
    monkeypatch.setattr(settings, "APP_ENV", "prod")

    body = (await client.get("/api/v1/auth/kakao/status")).json()

    assert body["ready"] is False
    assert "missing_keys" not in body


async def test_app_return_url_does_not_block_web_login(client, configured, monkeypatch):
    """앱 복귀 URL 이 비어도 웹 로그인은 막지 않는다.

    ready 는 provider 단위 한 값이라, 앱 주소가 없다고 false 를 내리면 웹 사용자의
    버튼까지 꺼진다. 앱은 client=app 으로 시작할 때만 거절한다 (아래 테스트).

    웹 복귀 URL 은 여기서 다루지 않는다 — 없으면 서버가 아예 안 뜬다
    (test_auth_boundary.py 의 부팅 검증, #45).
    """
    monkeypatch.setattr(settings, "AUTH_RETURN_URL_APP", None)

    body = (await client.get("/api/v1/auth/kakao/status")).json()

    assert body["ready"] is True


async def test_start_rejects_app_client_without_app_return_url(client, configured, monkeypatch):
    """앱으로 돌려보낼 주소가 없으면 카카오까지 걷게 하지 않는다."""
    monkeypatch.setattr(settings, "AUTH_RETURN_URL_APP", None)

    response = await client.get(f"/api/v1/auth/kakao?client=app&bind={BIND}")

    base, params = redirect_query(response)
    # 앱으로는 못 보내니 웹으로 알린다.
    assert base == RETURN_WEB
    assert params["error"] == ["oauth_provider_error"]


async def test_status_derives_start_url_from_callback_origin(client, configured):
    """start_url 은 등록된 콜백 URL 의 오리진에서 나온다 (§3-1).

    프론트가 API_BASE_URL 로 조립하면 두 값이 어긋난 배포에서 state 쿠키가 조용히 깨진다.
    """
    body = (await client.get("/api/v1/auth/kakao/status")).json()

    assert body["ready"] is True
    assert body["start_url"] == "https://api.example.test/api/v1/auth/kakao"


async def test_status_rejects_provider_outside_enum(client):
    """provider 가 enum 밖이면 422 validation_failed (§8-1).

    이쪽은 JSON 엔드포인트라 봉투가 정상이다 — 302 둘과 다르다.
    """
    response = await client.get("/api/v1/auth/microsoft/status")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# ── §3-2 로그인 시작 ──────────────────────────────────────────────────────────


async def test_start_redirects_to_kakao_with_state_cookie(client, configured):
    """동의 화면으로 보내고, state 를 쿠키에 심는다 (§3-2 · §5-5)."""
    response = await client.get(f"/api/v1/auth/kakao?bind={BIND}")

    base, query = redirect_query(response)
    assert base == "https://kauth.kakao.com/oauth/authorize"
    assert query["redirect_uri"] == [CALLBACK]
    assert query["response_type"] == ["code"]

    cookie = response.headers["set-cookie"]
    assert "oauth_state=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie.replace("samesite", "SameSite")
    assert "Path=/api/v1/auth" in cookie
    # 🚨 bind 원문이 쿠키에 실리면 §7-2 가 무의미해진다. 해시만 담는다.
    assert BIND not in cookie


@pytest.mark.parametrize("query", ["", f"?bind={'A' * 42}", "?bind=1"])
async def test_start_rejects_bad_bind_as_redirect(client, configured, query):
    """A-06 · A-07. "보냈다" 만 보면 bind=1 로도 통과해 §7-2 가 무의미해진다.

    🚨 400 JSON 이 아니라 302 여야 한다. 봉투가 새어 나오면 §8-2 위반이고, 사용자는
       카카오도 우리 화면도 아닌 JSON 에 남는다.
    """
    response = await client.get(f"/api/v1/auth/kakao{query}")

    base, params = redirect_query(response)
    assert base == RETURN_WEB
    assert params["error"] == ["invalid_bind"]


async def test_start_rejects_unknown_client_to_web(client, configured):
    """A-10. 잘못된 값으로는 앱 복귀를 신뢰할 수 없으므로 웹으로 돌려보낸다 (§2-3)."""
    response = await client.get(f"/api/v1/auth/kakao?client=desktop&bind={BIND}")

    base, params = redirect_query(response)
    assert base == RETURN_WEB
    assert params["error"] == ["invalid_client"]


async def test_start_without_keys_does_not_walk_user_to_kakao(client, unconfigured):
    """설정이 없으면 카카오까지 걷게 하지 않는다."""
    response = await client.get(f"/api/v1/auth/kakao?bind={BIND}")

    base, params = redirect_query(response)
    assert base == RETURN_WEB
    assert params["error"] == ["oauth_provider_error"]


# ── §3-3 콜백 ────────────────────────────────────────────────────────────────


async def test_callback_without_cookie_is_invalid_state(client, configured, monkeypatch):
    """A-09. 쿠키 없이 콜백. client 를 모르니 웹으로 돌려보낸다."""
    fake = patch_kakao(monkeypatch, FakeKakao())

    response = await client.get("/api/v1/auth/kakao/callback?code=x&state=y")

    base, params = redirect_query(response)
    assert base == RETURN_WEB
    assert params["error"] == ["invalid_state"]
    assert fake.calls == []


async def test_callback_with_mismatched_state_never_exchanges(client, configured, monkeypatch):
    """🚨 A-08. state 불일치면 인가 코드 교환을 시도하지 않는다.

    여기가 뚫리면 로그인 CSRF 가 열린다. 교환을 한 번이라도 부르면 실패다.
    """
    fake = patch_kakao(monkeypatch, FakeKakao())
    cookie, _ = await start_and_take_cookie(client)

    response = await client.get(
        "/api/v1/auth/kakao/callback?code=x&state=attacker-state",
        headers={"Cookie": f"oauth_state={cookie}"},
    )

    base, params = redirect_query(response)
    assert base == RETURN_WEB
    assert params["error"] == ["invalid_state"]
    assert fake.calls == []


async def test_callback_passes_user_cancel_through_as_denied(client, configured, monkeypatch):
    """A-17. 사용자가 동의 화면에서 취소한 것은 장애와 구분해 내려준다."""
    fake = patch_kakao(monkeypatch, FakeKakao())
    cookie, state = await start_and_take_cookie(client)

    response = await client.get(
        f"/api/v1/auth/kakao/callback?error=access_denied&state={state}",
        headers={"Cookie": f"oauth_state={cookie}"},
    )

    base, params = redirect_query(response)
    assert base == RETURN_WEB
    assert params["error"] == ["oauth_denied"]
    assert fake.calls == []


async def test_callback_returns_to_app_scheme(client, configured, monkeypatch):
    """A-11. client=app 으로 시작했으면 앱 스킴으로 돌아간다 (§2-3).

    복귀 대상은 쿠키가 들고 온다 — 콜백 쿼리로 받으면 오픈 리다이렉트가 된다.
    """
    patch_kakao(monkeypatch, FakeKakao())
    cookie, state = await start_and_take_cookie(client, target="app")

    response = await client.get(
        f"/api/v1/auth/kakao/callback?error=access_denied&state={state}",
        headers={"Cookie": f"oauth_state={cookie}"},
    )

    base, params = redirect_query(response)
    assert base == RETURN_APP
    assert params["error"] == ["oauth_denied"]


async def test_callback_does_not_fall_back_when_kakao_fails(client, configured, monkeypatch):
    """🚨 A-16. 카카오가 죽었을 때 로그인을 통과시키는 경로는 존재하지 않는다 (§8-1)."""
    patch_kakao(monkeypatch, FakeKakao(fail=KakaoApiError("token: 카카오가 500 으로 답했다")))
    cookie, state = await start_and_take_cookie(client)

    response = await client.get(
        f"/api/v1/auth/kakao/callback?code=authorization-code&state={state}",
        headers={"Cookie": f"oauth_state={cookie}"},
    )

    base, params = redirect_query(response)
    assert base == RETURN_WEB
    assert params["error"] == ["oauth_provider_error"]
    assert "code" not in params


async def test_callback_error_never_carries_a_message(client, configured, monkeypatch):
    """🚨 복귀 URL 에 문구를 싣지 않는다 (§8-2).

    서버 메시지를 URL 에 실으면 공격자가 프론트 화면에 임의 문구를 띄우는 통로가 된다.
    """
    patch_kakao(monkeypatch, FakeKakao(fail=KakaoApiError("비밀스러운 내부 사유")))
    cookie, state = await start_and_take_cookie(client)

    response = await client.get(
        f"/api/v1/auth/kakao/callback?code=c&state={state}",
        headers={"Cookie": f"oauth_state={cookie}"},
    )

    _, params = redirect_query(response)
    assert set(params) == {"error"}
    assert "비밀스러운" not in response.headers["location"]
