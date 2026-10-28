"""FCM(Firebase Cloud Messaging) HTTP v1 — 푸시 한 건을 기기 하나에 보낸다 (#594).

이 모듈만 FCM 을 안다. 서비스 계정으로 OAuth access token 을 받아(JWT bearer grant) 메시지
API 를 부르고, 밖으로는 `SendResult` 만 나간다 — FCM 응답 모양을 다른 곳으로 퍼뜨리지 않는다.
웹(Firebase JS SDK)과 안드로이드 앱이 같은 API · 같은 토큰 종류로 받는다.

🚨 비밀이 셋이다 — 서비스 계정 키, access token, 기기 토큰. 어느 것도 예외 문구 · 로그에
   싣지 않는다. 남기는 것은 상태 코드와 FCM 의 분류값(errorCode)까지다 — 카카오 클라이언트와
   같은 규칙.
🚨 알림 문구도 로그에 남기지 않는다 — 일정 제목 같은 보호자 입력이 들어갈 수 있다.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx
from google.auth import crypt, jwt

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TIMEOUT_SECONDS = 5.0
_TOKEN_MARGIN_SECONDS = 60
"""access token 이 끝나기 이만큼 전에 새로 받는다 — 보내는 중에 끝나지 않게."""
_LOGGABLE_CODE = re.compile(r"[A-Z_]{1,64}")


class SendResult(StrEnum):
    SENT = "sent"
    UNREGISTERED = "unregistered"
    """토큰이 더는 유효하지 않다(앱 삭제 · 브라우저 구독 해지 · 토큰 교체). 기기 행을 지운다."""
    FAILED = "failed"
    """그 밖의 실패 — 다시 보내지 않는다. 기기는 그대로 둔다."""


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    data: dict[str, str]
    """화면이 알림을 눌렀을 때 쓸 값. FCM 은 문자열 값만 받는다."""


class FcmConfigError(Exception):
    """서비스 계정 키 파일을 읽을 수 없다. 문구에 키 내용은 없다."""


class _AuthError(Exception):
    """access token 교환 실패. 문구는 상태 코드뿐이다 — 로그에 그대로 남긴다."""


class FcmClient:
    def __init__(self, service_account: dict, *, client: httpx.AsyncClient | None = None):
        try:
            self._project_id = service_account["project_id"]
            self._email = service_account["client_email"]
            self._signer = crypt.RSASigner.from_service_account_info(service_account)
        except (KeyError, ValueError):
            # 🚨 exc 를 싣지 않는다 — 키 내용이 메시지에 섞일 수 있다
            raise FcmConfigError("FCM 서비스 계정 키의 모양이 아니다") from None
        self._http = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self._token: str | None = None
        self._token_expires_at = 0.0

    @classmethod
    def from_file(cls, path: str, *, client: httpx.AsyncClient | None = None) -> "FcmClient":
        try:
            info = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            raise FcmConfigError("FCM 서비스 계정 키 파일을 읽지 못했다") from None
        return cls(info, client=client)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, device_token: str, message: Message) -> SendResult:
        """기기 하나에 한 건. 실패해도 예외를 던지지 않는다 — 결과만 돌려준다."""
        try:
            access = await self._access_token()
            response = await self._http.post(
                SEND_URL.format(project_id=self._project_id),
                headers={"Authorization": f"Bearer {access}"},
                json={
                    "message": {
                        "token": device_token,
                        "notification": {"title": message.title, "body": message.body},
                        "data": message.data,
                    }
                },
            )
        except _AuthError as exc:
            log.warning("FCM 발송 실패: %s", exc)
            return SendResult.FAILED
        except Exception as exc:  # 타임아웃 · 연결 실패 — 문구에 주소가 섞여 이름만 남긴다
            log.warning("FCM 발송 실패: %s", type(exc).__name__)
            return SendResult.FAILED
        if response.is_success:
            return SendResult.SENT
        code = _error_code(response)
        if response.status_code == 404 and code == "UNREGISTERED":
            return SendResult.UNREGISTERED
        log.warning("FCM 발송 실패: status=%d code=%s", response.status_code, code)
        return SendResult.FAILED

    async def _access_token(self) -> str:
        if self._token is not None and time.time() < self._token_expires_at:
            return self._token
        now = int(time.time())
        assertion = jwt.encode(
            self._signer,
            {
                "iss": self._email,
                "scope": _SCOPE,
                "aud": TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
        )
        response = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        if not response.is_success:
            # 🚨 본문에 assertion 이 되돌아올 수 있어 상태 코드만 남긴다
            raise _AuthError(f"access token 교환 실패 status={response.status_code}")
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = now + int(body["expires_in"]) - _TOKEN_MARGIN_SECONDS
        return self._token


def _error_code(response: httpx.Response) -> str | None:
    """FCM 오류 본문의 errorCode(UNREGISTERED · INVALID_ARGUMENT …). 모양이 다르면 None.

    본문 전체는 남기지 않는다 — 요청 값(토큰)이 메시지에 되돌아올 수 있다.
    """
    try:
        details = response.json()["error"].get("details", [])
    except (ValueError, KeyError, AttributeError, TypeError):
        return None
    for detail in details:
        code = detail.get("errorCode") if isinstance(detail, dict) else None
        if isinstance(code, str) and _LOGGABLE_CODE.fullmatch(code):
            return code
    return None
