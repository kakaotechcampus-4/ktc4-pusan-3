"""카카오 integration — 명세 docs/api/auth-kakao-v1.md §2-2 · §3-3 · §7-4

카카오 서버를 부르지 않는다. httpx.MockTransport 로 응답을 바꿔 끼우고 **호출 여부와
입력값을 함께** 검증한다 (apps/api/CLAUDE.md 자동 검증).

여기서 지키는 것 셋.
    ① 교환 요청이 명세대로 나간다 — redirect_uri 는 콘솔 등록값 그대로
    ② 카카오가 어떻게 실패하든 KakaoApiError 하나로 모인다 (§8-1 의 502 로 갈 자리)
    ③ 토큰 폐기는 실패해도 로그인을 되돌리지 않는다 (§7-4)
    ④ 실패 사유는 카카오의 분류값까지만 남긴다 — 설명 글 · 본문은 버린다 (#197 · §7-5)
"""

import logging
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.integrations.kakao.client import (
    LOGOUT_URL,
    TOKEN_INFO_URL,
    TOKEN_URL,
    KakaoApiError,
    KakaoClient,
    build_authorize_url,
)

CALLBACK_URL = "http://localhost:8000/api/v1/auth/kakao/callback"


def build_client(handler) -> KakaoClient:
    """MockTransport 를 문 클라이언트. 값은 전부 가짜다 — 실제 키를 픽스처에 넣지 않는다."""
    return KakaoClient(
        rest_api_key="test-rest-api-key",
        client_secret="test-client-secret",
        callback_url=CALLBACK_URL,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def json_handler(payload: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def test_authorize_url_carries_registered_callback_verbatim():
    """redirect_uri 는 조립하지 않고 KAKAO_CALLBACK_URL 을 그대로 싣는다 (§3-2).

    모듈 함수라 httpx 클라이언트가 필요 없다 — 시작 라우터는 카카오를 부르지 않는다.
    """
    url = build_authorize_url(
        rest_api_key="test-rest-api-key", callback_url=CALLBACK_URL, state="state-abc"
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    assert base == "https://kauth.kakao.com/oauth/authorize"
    assert query["redirect_uri"] == [CALLBACK_URL]
    assert query["client_id"] == ["test-rest-api-key"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["state-abc"]


async def test_exchange_code_sends_spec_form_and_returns_token():
    """교환 요청의 URL 과 폼 필드를 함께 본다 — 하나만 틀려도 카카오가 거절한다."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(
            200, json={"access_token": "kakao-access-token", "refresh_token": "r"}
        )

    token = await build_client(handler).exchange_code("authorization-code")

    assert token == "kakao-access-token"
    assert seen["url"] == TOKEN_URL
    assert seen["form"] == {
        "grant_type": ["authorization_code"],
        "client_id": ["test-rest-api-key"],
        "client_secret": ["test-client-secret"],
        "redirect_uri": [CALLBACK_URL],
        "code": ["authorization-code"],
    }


@pytest.mark.parametrize("status", [400, 401, 500, 503])
async def test_exchange_code_turns_any_kakao_failure_into_one_error(status):
    """4xx(교환 실패)도 5xx 도 같은 예외다. 사유를 구분해 흘리지 않는다 (§8-1)."""
    with pytest.raises(KakaoApiError):
        await build_client(json_handler({"error": "invalid_grant"}, status)).exchange_code("c")


async def test_exchange_code_raises_on_timeout():
    """타임아웃도 같은 곳으로 간다 — 카카오 3초 (§8-1)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(KakaoApiError):
        await build_client(handler).exchange_code("c")


async def test_exchange_code_raises_when_token_missing():
    """200 이어도 모양이 다르면 실패다. 기본값으로 넘기지 않는다 (§8-1)."""
    with pytest.raises(KakaoApiError):
        await build_client(json_handler({"token_type": "bearer"})).exchange_code("c")


async def test_error_message_does_not_leak_response_body():
    """🚨 예외 문구에 카카오 본문이 실리면 토큰이 로그로 새어 나간다 (§7-5)."""
    body = {"access_token": "leaked-token", "error_description": "secret reason"}

    with pytest.raises(KakaoApiError) as caught:
        await build_client(json_handler(body, 401)).exchange_code("authorization-code")

    message = str(caught.value)
    assert "leaked-token" not in message
    assert "secret reason" not in message
    assert "authorization-code" not in message


async def test_error_message_carries_kakao_error_code_but_not_description():
    """#197. 토큰 받기(kauth) 실패는 분류값을 싣고 설명은 버린다.

    error_description 에는 인가 코드가 그대로 들어간다 — 카카오 예시
    "authorization code not found for code=..." (§7-5).
    """
    body = {
        "error": "invalid_grant",
        "error_description": "authorization code not found for code=SECRET-AUTH-CODE",
        "error_code": "KOE320",
    }

    with pytest.raises(KakaoApiError) as caught:
        await build_client(json_handler(body, 400)).exchange_code("SECRET-AUTH-CODE")

    message = str(caught.value)
    assert "token" in message
    assert "400" in message
    assert "invalid_grant" in message
    assert "KOE320" in message
    assert "SECRET-AUTH-CODE" not in message
    assert "not found" not in message


async def test_error_message_carries_kakao_api_code_but_not_msg():
    """#197. kapi(토큰 정보 · 로그아웃) 는 {code, msg} 로 답한다. code 만 싣는다.

    msg 에는 앱 키가 들어갈 수 있다 — 카카오 예시 "...by using app_key(${APP_KEY})."
    """
    body = {"code": -401, "msg": "This api is not allowed by using app_key(test-rest-api-key)."}

    with pytest.raises(KakaoApiError) as caught:
        await build_client(json_handler(body, 401)).fetch_user_id("kakao-access-token")

    message = str(caught.value)
    assert "token_info" in message
    assert "-401" in message
    assert "test-rest-api-key" not in message
    assert "kakao-access-token" not in message


async def test_error_message_drops_values_that_are_not_short_codes():
    """분류값 자리에 줄바꿈 · 긴 글이 오면 버린다.

    그대로 실으면 가짜 로그 줄이나 설명 글이 들어오는 통로가 된다.
    """
    body = {"error": "invalid_grant", "error_code": "KOE320\nFORGED LOG LINE", "code": "x" * 65}

    with pytest.raises(KakaoApiError) as caught:
        await build_client(json_handler(body, 400)).exchange_code("c")

    message = str(caught.value)
    assert "invalid_grant" in message
    assert "FORGED LOG LINE" not in message
    assert "x" * 65 not in message


async def test_error_without_json_body_is_still_one_error():
    """프록시 장애처럼 본문이 JSON 이 아니어도 같은 예외다 — 분류값을 꺼내다 터지면 안 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    with pytest.raises(KakaoApiError) as caught:
        await build_client(handler).exchange_code("c")

    assert "502" in str(caught.value)


async def test_fetch_user_id_uses_token_info_and_returns_string():
    """/v2/user/me 가 아니라 access_token_info 다 — 회원번호만 받는다 (§2-2).

    회원번호는 Long 으로 오지만 문자열로 나간다. Apple 이 문자열 식별자를 주므로
    provider_user_id 의 타입이 제공자마다 바뀌면 안 된다 (§5-2).
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": 1234567890, "expires_in": 21599})

    user_id = await build_client(handler).fetch_user_id("kakao-access-token")

    assert user_id == "1234567890"
    assert seen["url"] == TOKEN_INFO_URL
    assert seen["auth"] == "Bearer kakao-access-token"


async def test_fetch_user_id_raises_when_id_missing():
    with pytest.raises(KakaoApiError):
        await build_client(json_handler({"expires_in": 21599})).fetch_user_id("t")


async def test_revoke_token_calls_logout():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(200, json={"id": 1234567890})

    await build_client(handler).revoke_token("kakao-access-token")

    assert seen == {"url": LOGOUT_URL, "method": "POST"}


async def test_revoke_token_swallows_failure():
    """🚨 폐기가 실패해도 예외를 올리지 않는다.

    이 시점에는 회원번호를 이미 얻었고 로그인은 성공한 상태다. 정리 한 번 실패했다고
    사용자의 로그인을 되돌리면 더 나쁘다 (§7-4).
    """
    await build_client(json_handler({"msg": "down"}, 500)).revoke_token("kakao-access-token")


async def test_revoke_failure_log_says_which_call_and_why(caplog):
    """#197. 폐기 실패 로그에도 어느 호출 · 상태 · 분류값이 남는다. 토큰은 남지 않는다."""
    body = {"code": -401, "msg": "InvalidTokenException"}

    with caplog.at_level(logging.WARNING, logger="app.integrations.kakao.client"):
        await build_client(json_handler(body, 401)).revoke_token("kakao-access-token")

    assert "logout" in caplog.text
    assert "-401" in caplog.text
    assert "kakao-access-token" not in caplog.text
