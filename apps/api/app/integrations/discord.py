"""Discord 웹훅 — 알림 글 하나를 채널에 올린다 (app/core/alerts.py 가 쓴다).

카카오처럼 httpx 클라이언트를 밖에서 받는다 — 테스트는 MockTransport 를 물려 Discord 없이 돈다.
🚨 httpx 의 예외 메시지에는 웹훅 URL(비밀)이 통째로 들어 있다. 그래서 밖으로는 WebhookError 만
   나간다 — 메시지에 상태 코드나 예외 종류 이름만 싣는다. 카카오 클라이언트의 KakaoApiError 와
   같은 이유다. httpx 가 INFO 로 찍는 요청 주소는 core/logging_config 가 막는다.
"""

import httpx

from app.core.alerts import Sender, SendError

TIMEOUT_SECONDS = 5.0
"""연결 · 응답 각각. Discord 는 보통 0.3초 안에 답한다 — 이보다 오래 걸리면 기다리지 않는다
(알림 스레드가 묶이면 다음 알림이 밀린다)."""
_HOSTS = ("discord.com", "discordapp.com")
_WEBHOOK_PATH = "/api/webhooks/"


class WebhookError(SendError):
    """웹훅 호출 실패. 메시지에 URL 은 없다 — 로그에 그대로 찍어도 된다."""


def webhook_sender(url: str, *, client: httpx.Client | None = None) -> Sender:
    """POST, JSON 의 content 한 칸. wait=true 라 Discord 가 저장까지 마치고 200 으로 답한다
    (기본값 wait=false 는 저장에 실패해도 204 를 준다).

    allowed_mentions 를 비워 글에 @everyone 이 들어 있어도 아무도 호출하지 않는다.
    username 은 출처별 — api-alert · browser-alert · infra-alert. 채널 하나에서 보내는 이름으로
    갈린다 (웹훅 · 채널을 늘리지 않는다. 쪼개고 싶으면 그때 URL 을 하나 더 두면 된다).
    2xx 가 아니면 WebhookError — 받는 쪽(알림 핸들러)이 세고 WARNING 으로 남긴다.
    동기 클라이언트다 — 알림 스레드에서 부른다. 이벤트 루프 위가 아니다.
    """
    parsed = httpx.URL(url)
    if (
        parsed.scheme != "https"
        or not parsed.host.endswith(_HOSTS)
        or not parsed.path.startswith(_WEBHOOK_PATH)
    ):
        # 🚨 부팅에서 끊는다. 채널 링크(discord.com/channels/...)를 붙여 넣으면 Discord 가 200 을
        #    주면서 아무 데도 올리지 않아, 알림이 켜진 줄 알고 영영 못 받는다. 메시지에 주소는 없다.
        raise ValueError(
            "ALERT_WEBHOOK_URL 이 Discord 웹훅 주소 모양이 아니다 "
            "(https://discord.com/api/webhooks/<id>/<token>). 채널 링크를 넣은 건 아닌지 확인할 것"
        )
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)

    def send(embed: dict, source: str) -> None:
        try:
            response = http.post(
                url,
                params={"wait": "true"},
                json={
                    "embeds": [embed],
                    "username": f"{source}-alert",
                    "allowed_mentions": {"parse": []},
                },
            )
        except Exception as exc:  # 타임아웃 · 연결 실패 · 잘못된 주소 — 메시지에 URL 이 있다
            raise WebhookError(f"webhook {type(exc).__name__}") from None
        if not response.is_success:
            raise WebhookError(f"webhook {response.status_code}")

    return send
