"""Discord 웹훅 전송 — 알림 핸들러(app/core/alerts.py)가 쓰는 보내기 함수.

카카오 클라이언트 테스트처럼 MockTransport 를 물려 Discord 없이 돈다.
"""

import json

import httpx
import pytest

from app.integrations import discord

URL = "https://discord.example/api/webhooks/1/token"


def test_posts_the_text_as_content_json():
    """Discord 웹훅 계약 — POST, JSON 의 content 한 칸. 보낸 글이 그대로 들어간다."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    send = discord.webhook_sender(URL, client=httpx.Client(transport=httpx.MockTransport(respond)))
    send("[prod] ERROR app.x — 무언가")

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == URL
    assert seen[0].headers["content-type"] == "application/json"
    assert json.loads(seen[0].read()) == {"content": "[prod] ERROR app.x — 무언가"}


def test_raises_on_http_failure_so_the_handler_can_count_it():
    """429 · 5xx 는 예외로 올린다 — 조용히 삼키면 "보냈다" 고 믿게 된다. 받는 쪽은 핸들러다."""

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    send = discord.webhook_sender(URL, client=httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(httpx.HTTPStatusError):
        send("x")
