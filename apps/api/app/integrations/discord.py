"""Discord 웹훅 — 알림 글 하나를 채널에 올린다 (app/core/alerts.py 가 쓴다).

웹훅은 Discord 채널마다 만들 수 있는 "이 주소로 POST 하면 메시지가 올라오는" 비밀 URL 이다.
🚨 URL 을 아는 사람은 누구나 그 채널에 글을 올릴 수 있다 — .env 에만 둔다 (루트 CLAUDE.md §9).
카카오처럼 httpx 클라이언트를 밖에서 받는다 — 테스트는 MockTransport 를 물려 Discord 없이 돈다.
"""

import httpx

from app.core.alerts import Sender


def webhook_sender(url: str, *, client: httpx.Client | None = None) -> Sender:
    """POST, JSON 의 content 한 칸. 204 가 정상.

    2xx 가 아니면 예외 — 받는 쪽(알림 핸들러)이 세고 WARNING 으로 남긴다. 🚨 그 예외의 메시지에
    url 이 들어 있으니 거기서 메시지를 찍지 않는다.
    동기 클라이언트다 — 알림 스레드에서 부른다. 이벤트 루프 위가 아니다.
    """
    http = client or httpx.Client(timeout=5.0)

    def send(text: str) -> None:
        http.post(url, json={"content": text}).raise_for_status()

    return send
