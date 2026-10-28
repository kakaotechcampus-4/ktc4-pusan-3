"""FCM 클라이언트 — FCM 서버 없이 MockTransport 로 (#594)."""

import json
import logging

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.integrations.fcm import TOKEN_URL, FcmClient, Message, SendResult

DEVICE_TOKEN = "synthetic-device-token"
MESSAGE = Message(title="일정 알림", body="1시간 뒤 일정이 있어요", data={"event_id": "e1"})


@pytest.fixture(scope="module")
def service_account() -> dict:
    """테스트마다 만드는 가짜 서비스 계정. 실제 키가 아니다."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account",
        "project_id": "synthetic-project",
        "private_key_id": "synthetic",
        "private_key": pem,
        "client_email": "fcm@synthetic-project.iam.gserviceaccount.com",
    }


def _client(service_account: dict, send_response: httpx.Response, calls: list) -> FcmClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url) == TOKEN_URL:
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "expires_in": 3600}
            )
        return send_response

    return FcmClient(
        service_account, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_sends_one_message_and_reuses_the_access_token(service_account):
    calls: list[httpx.Request] = []
    fcm = _client(service_account, httpx.Response(200, json={"name": "m1"}), calls)

    assert await fcm.send(DEVICE_TOKEN, MESSAGE) == SendResult.SENT
    assert await fcm.send(DEVICE_TOKEN, MESSAGE) == SendResult.SENT

    token_calls = [c for c in calls if str(c.url) == TOKEN_URL]
    sends = [c for c in calls if str(c.url) != TOKEN_URL]
    assert len(token_calls) == 1  # 끝나기 전까지 다시 받지 않는다
    assert sends[0].url.path == "/v1/projects/synthetic-project/messages:send"
    assert sends[0].headers["Authorization"] == "Bearer synthetic-access"
    assert json.loads(sends[0].content)["message"] == {
        "token": DEVICE_TOKEN,
        "notification": {"title": MESSAGE.title, "body": MESSAGE.body},
        "data": MESSAGE.data,
    }


@pytest.mark.parametrize(
    ("response", "result"),
    [
        (
            httpx.Response(
                404,
                json={
                    "error": {
                        "status": "NOT_FOUND",
                        "details": [{"errorCode": "UNREGISTERED", "token": DEVICE_TOKEN}],
                    }
                },
            ),
            SendResult.UNREGISTERED,
        ),
        (
            httpx.Response(500, json={"error": {"message": f"echo {DEVICE_TOKEN} 일정 알림"}}),
            SendResult.FAILED,
        ),
    ],
)
async def test_failure_is_a_result_and_logs_no_token_or_text(
    service_account, response, result, caplog
):
    caplog.set_level(logging.WARNING, logger="app.integrations.fcm")
    fcm = _client(service_account, response, [])

    assert await fcm.send(DEVICE_TOKEN, MESSAGE) == result
    assert DEVICE_TOKEN not in caplog.text
    assert MESSAGE.body not in caplog.text
