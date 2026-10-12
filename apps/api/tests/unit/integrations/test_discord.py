"""Discord 웹훅 전송 — 알림 핸들러(app/core/alerts.py)가 쓰는 보내기 함수.

카카오 클라이언트 테스트처럼 MockTransport 를 물려 Discord 없이 돈다.
"""

import json

import httpx
import pytest

from app.core import alerts
from app.integrations import discord

URL = "https://discord.com/api/webhooks/1/SECRET-TOKEN"


def _client(respond) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(respond))


def test_posts_embed_json_and_waits_for_delivery():
    """Discord 웹훅 계약 — POST, JSON 의 embeds 한 칸, 저장까지 기다리고(wait=true), 아무도
    호출하지 않게(allowed_mentions 비움). embed dict 가 그대로 들어간다.
    """
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "1"})

    embed = {"color": 0xFFA500, "description": "## 🟠 test"}
    send = discord.webhook_sender(URL, client=_client(respond))
    send(embed, "api")

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url).startswith(URL)
    assert seen[0].url.params["wait"] == "true"
    assert json.loads(seen[0].read()) == {
        "embeds": [embed],
        "username": "api-alert",
        "allowed_mentions": {"parse": []},
    }


def test_sender_name_follows_the_source_so_one_channel_reads_sorted():
    """채널 하나에 보내는 이름만 갈린다 — api-alert · browser-alert · infra-alert. 웹훅 · 채널을
    늘리지 않는다.
    """
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "1"})

    send = discord.webhook_sender(URL, client=_client(respond))
    send({"description": "화면 쪽"}, "browser")

    assert json.loads(seen[0].read())["username"] == "browser-alert"


def test_http_failure_becomes_a_webhook_error_without_the_url():
    """429 · 5xx 는 예외로 올린다 — 조용히 삼키면 "보냈다" 고 믿게 된다. 메시지엔 상태 코드만."""
    send = discord.webhook_sender(URL, client=_client(lambda _r: httpx.Response(429)))
    with pytest.raises(discord.WebhookError) as info:
        send({"description": "x"}, "api")

    assert isinstance(info.value, alerts.SendError)
    assert "429" in str(info.value)
    assert "SECRET-TOKEN" not in str(info.value)


def test_transport_failure_becomes_a_webhook_error_without_the_url():
    """🚨 httpx 의 연결 · 타임아웃 예외는 메시지에 URL 이 통째로 있다 — 종류 이름만 남기고
    끊는다.
    """

    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    send = discord.webhook_sender(URL, client=_client(respond))
    with pytest.raises(discord.WebhookError) as info:
        send({"description": "x"}, "api")

    assert "ConnectError" in str(info.value)
    assert "SECRET-TOKEN" not in str(info.value)
    assert info.value.__cause__ is None  # 원래 예외(URL 포함)를 달고 다니지 않는다


@pytest.mark.parametrize(
    "bad",
    [
        "https://discord.com/channels/1/2",  # 채널 링크 — 200 을 주면서 아무것도 안 올린다
        "discord.com/api/webhooks/1/SECRET-TOKEN",  # https:// 빠짐
        "http://discord.com/api/webhooks/1/SECRET-TOKEN",
        "https://example.com/api/webhooks/1/SECRET-TOKEN",
    ],
)
def test_rejects_urls_that_are_not_discord_webhooks(bad):
    """🚨 모양이 아니면 부팅에서 끊는다 — 알림이 켜진 줄 알고 영영 못 받는 것이 제일 나쁘다."""
    with pytest.raises(ValueError) as info:
        discord.webhook_sender(bad)

    assert "SECRET-TOKEN" not in str(info.value)
