"""카카오 OAuth HTTP 호출 — 명세 docs/api/auth-kakao-v1.md §2-2 · §3-3 · §7-4

이 모듈만 카카오 서버를 안다. 요청 URL · 타임아웃 · 응답 파싱 · 외부 오류 변환이 여기
모여 있고, 밖으로는 파이썬 문자열만 나간다 — 카카오 응답 모양을 다른 도메인으로
퍼뜨리지 않는다 (apps/api/CLAUDE.md 레이어 경계).

🚨 app.api · app.domains · app.infra 를 import 하지 않는다. 그래서 실패를 ApiError 가
   아니라 KakaoApiError 로 던지고, HTTP 상태로 옮기는 일은 라우터가 한다 — JSON
   엔드포인트는 502 oauth_provider_error, 302 엔드포인트는 복귀 URL 의
   ?error=oauth_provider_error (명세 §8-1 · §8-2).

🚨 인가 코드 · access token · 회원번호를 예외 메시지에도 로그에도 싣지 않는다 (§7-5).
   카카오 응답 본문에는 토큰이 들어 있으므로 본문을 그대로 남기지 않는다. 남기는 것은
   "어느 호출이 몇 번 상태로 실패했는가" 까지다.
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlencode

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
TOKEN_INFO_URL = "https://kapi.kakao.com/v1/user/access_token_info"
LOGOUT_URL = "https://kapi.kakao.com/v1/user/logout"


class KakaoApiError(Exception):
    """카카오 호출이 실패했다 — 5xx · 타임아웃 · 교환 실패 · 응답 모양 불량.

    실패 사유를 나누지 않는다. 라우터에서 전부 같은 곳(502 oauth_provider_error)으로
    가고, 명세 §8-1 이 "기본값으로 대체하지 않는다" 로 못박은 대상이다 — 카카오가
    죽었을 때 로그인을 통과시키는 경로는 존재하지 않는다.
    """


@dataclass(frozen=True)
class KakaoClient:
    """카카오 호출 3종. httpx 클라이언트를 밖에서 받는다.

    테스트는 httpx.MockTransport 를 물린 AsyncClient 를 그대로 넣어 카카오 서버 없이
    돌린다 (apps/api/CLAUDE.md "카카오 서버를 실제 호출하는 테스트는 만들지 않는다").
    운영 경로는 아래 kakao_client() 컨텍스트 매니저를 쓴다.
    """

    rest_api_key: str
    client_secret: str
    callback_url: str
    http: httpx.AsyncClient

    def build_authorize_url(self, *, state: str) -> str:
        """카카오 동의 화면 URL (§3-2).

        redirect_uri 는 콘솔 등록값과 완전히 같아야 한다. 그래서 조립하지 않고
        KAKAO_CALLBACK_URL 을 그대로 싣는다 — 한 글자만 달라도 카카오가 거절한다.
        """
        query = urlencode(
            {
                "client_id": self.rest_api_key,
                "redirect_uri": self.callback_url,
                "response_type": "code",
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    async def exchange_code(self, code: str) -> str:
        """인가 코드를 access token 으로 바꾼다 (§3-3 2번).

        반환은 access token 하나다. refresh token 은 받아도 버린다 — 카카오 API 를 더
        부르지 않으므로 들고 있으면 유출 표면만 는다 (§7-4).
        """
        payload = await self._request(
            "POST",
            TOKEN_URL,
            where="token",
            data={
                "grant_type": "authorization_code",
                "client_id": self.rest_api_key,
                "client_secret": self.client_secret,
                "redirect_uri": self.callback_url,
                "code": code,
            },
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise KakaoApiError("token: 응답에 access_token 이 없다")
        return token

    async def fetch_user_id(self, access_token: str) -> str:
        """회원번호를 조회한다 (§3-3 2번).

        /v2/user/me 가 아니라 access_token_info 다. 전자는 kakao_account(이메일·프로필)
        까지 실어 오는데 NF-04 는 최소 수집을 요구한다 — 받아놓고 안 쓰는 것보다 애초에
        안 받는 것이 낫다 (§2-2).

        회원번호는 Long 이지만 문자열로 돌려준다. Apple 은 문자열 식별자를 주므로
        제공자가 늘 때 auth_identity.provider_user_id 의 타입이 바뀌면 안 된다 (§5-2).
        """
        payload = await self._request(
            "GET",
            TOKEN_INFO_URL,
            where="token_info",
            headers=_bearer(access_token),
        )
        user_id = payload.get("id")
        if not isinstance(user_id, (int, str)) or isinstance(user_id, bool) or user_id == "":
            raise KakaoApiError("token_info: 응답에 id 가 없다")
        return str(user_id)

    async def revoke_token(self, access_token: str) -> None:
        """받은 access token 을 카카오에서 만료시킨다 (§3-3 3번 · §7-4).

        🚨 실패해도 예외를 올리지 않는다. 여기까지 왔으면 회원번호는 이미 얻었고 우리
           쪽 로그인은 성공한 상태다. 정리 한 번 실패했다고 사용자의 로그인을 되돌리면
           더 나쁘고, 우리는 이 토큰을 저장하지 않으므로 남아도 우리 쪽 유출 표면은
           늘지 않는다. 카카오 쪽에서 수명대로 만료된다.
        """
        try:
            await self._request(
                "POST", LOGOUT_URL, where="logout", headers=_bearer(access_token)
            )
        except KakaoApiError:
            log.warning("카카오 토큰 폐기에 실패했다 — 로그인은 계속한다")

    async def _request(
        self,
        method: str,
        url: str,
        *,
        where: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """호출 1건 + 실패를 전부 KakaoApiError 로 모은다.

        where 는 "어느 호출인지" 만 담는다. 인가 코드도 토큰도 응답 본문도 넣지 않는다.
        """
        try:
            response = await self.http.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise KakaoApiError(f"{where}: 타임아웃") from exc
        except httpx.HTTPError as exc:
            raise KakaoApiError(f"{where}: 연결 실패") from exc

        if response.status_code >= 400:
            # 🚨 본문을 싣지 않는다 — 토큰이 들어 있을 수 있다 (§7-5).
            #    4xx 도 여기서 끝낸다. 교환 실패는 사용자가 고칠 수 있는 것이 아니고,
            #    카카오의 사유를 그대로 흘리면 정찰에 쓰인다 (§8-1).
            raise KakaoApiError(f"{where}: 카카오가 {response.status_code} 로 답했다")

        try:
            payload = response.json()
        except ValueError as exc:
            raise KakaoApiError(f"{where}: 응답이 JSON 이 아니다") from exc

        if not isinstance(payload, dict):
            raise KakaoApiError(f"{where}: 응답이 객체가 아니다")
        return payload


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@asynccontextmanager
async def kakao_client() -> AsyncIterator[KakaoClient]:
    """운영 경로에서 쓰는 클라이언트. 블록을 나가면 커넥션을 닫는다.

    설정이 비어 있으면 만들지 않는다 — 라우터는 그 전에 settings.kakao_ready 로 걸러
    ready: false 를 내리거나 시작 자체를 하지 않는다 (§3-1). 여기까지 온 것은 코드가
    순서를 어긴 것이라 RuntimeError 다.
    """
    missing = settings.kakao_missing_keys
    if missing:
        raise RuntimeError(f"카카오 설정이 비어 있다: {', '.join(missing)}")

    # 타임아웃 3초 — NF-06 의 20초는 Agent 파이프라인 예산이고 로그인은 그 밖이다 (§8-1).
    async with httpx.AsyncClient(timeout=settings.KAKAO_API_TIMEOUT) as http:
        yield KakaoClient(
            rest_api_key=str(settings.KAKAO_REST_API_KEY),
            client_secret=str(settings.KAKAO_CLIENT_SECRET),
            callback_url=str(settings.KAKAO_CALLBACK_URL),
            http=http,
        )
