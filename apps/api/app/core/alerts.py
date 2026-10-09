"""ERROR 이상 로그를 Discord 웹훅으로 알린다 (멘토 #267 2번 · 10-09 결정).

로그는 서버에 남지만 아무도 보지 않는다. ERROR 가 나면 사람에게 가야 한다 — 팀이 쓰는 Discord 로.
적재(로그를 남겨 두는 것)는 여기서 하지 않는다 — docker 의 로그 드라이버가 한다
(README "로그와 알림"). Discord 로 보내는 HTTP 는 app/integrations/discord.py 다 —
외부 호출은 integrations 가 격리한다 (apps/api/CLAUDE.md). 여기는 보내기 함수를 받기만 한다.

구조 — app 로거에 핸들러 하나.

    log.error(...) ─ emit: 글 만들기 · 분당 상한 ─▶ 큐 ─▶ 스레드 하나 ─▶ send(글)

    요청을 처리하는 이벤트 루프는 큐에 넣고 바로 돌아온다. HTTP 를 기다리는 건 스레드다.
    로그 핸들러 안에서 HTTP 를 직접 기다리면 그동안 서버의 모든 요청이 멈춘다.

🚨 본문은 환경 · 레벨 · 로거 이름 · 메시지 첫 줄 · 예외 종류 이름 · 시간뿐이다
   (루트 CLAUDE.md §2 원문 금지). 트레이스백 · 예외 메시지(str) · 메시지 둘째 줄부터는
   서버 로그에만 있다.
🚨 알림이 실패해도 요청 처리는 영향이 없다. 실패는 WARNING 한 줄, 예외 종류 이름만 —
   httpx 의 오류 메시지에는 웹훅 URL(비밀)이 들어 있다.
🚨 분당 상한 — DB 가 죽으면 요청마다 ERROR 다. Discord 는 웹훅 하나에 분당 30건쯤에서
   429 로 막고, 그때부터 정작 봐야 할 알림이 사라진다. 넘친 건수는 다음 알림 머리에 적는다.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable

from app.core.logging_config import DATEFMT

log = logging.getLogger(__name__)

Sender = Callable[[str], None]
"""알림 글 하나를 보내는 함수. 실패는 예외로 올린다 — 핸들러가 받아 WARNING 으로 남긴다."""

ALERTS_PER_MINUTE = 10
_WINDOW_SECONDS = 60.0
_FIRST_LINE_MAX = 500
"""Discord content 상한은 2000자. 넘치면 400 으로 실패하니 첫 줄을 잘라 둔다."""
_STOP = None
"""큐의 끝 표시. close 가 넣고 스레드가 보면 끝난다."""


def format_alert(record: logging.LogRecord, *, env: str) -> str:
    """알림 글. 첫 줄에 어느 환경 · 어느 레벨 · 어느 모듈 · 무슨 일, 둘째 줄에 시간.

    메시지는 첫 줄만 — runner · errors 는 둘째 줄부터 코드 위치(트레이스백)를 붙이는데, Discord 에서
    읽을 건 한 줄이고 자세한 건 서버 로그에 있다. 예외는 종류 이름만 — 메시지(str)에는 입력 원문이
    섞일 수 있다 (pydantic 의 input_value 등).
    """
    lines = record.getMessage().splitlines() or [""]
    first_line = lines[0][:_FIRST_LINE_MAX]
    exc_type = record.exc_info[0] if record.exc_info else None
    suffix = f" ({exc_type.__name__})" if exc_type is not None else ""
    when = time.strftime(DATEFMT, time.localtime(record.created))
    return f"[{env}] {record.levelname} {record.name} — {first_line}{suffix}\n{when}"


class WebhookHandler(logging.Handler):
    """ERROR 이상을 글로 만들어 큐에 넣는다. 스레드 하나가 꺼내 send 로 보낸다.

    emit 은 Handler.handle 이 잠금을 잡고 부르므로 상한 계산에 잠금이 따로 필요 없다.
    flush 는 큐가 빌 때까지 기다린다 — 테스트와 종료(logging.shutdown)가 쓴다.
    """

    def __init__(
        self,
        send: Sender,
        *,
        env: str,
        per_minute: int = ALERTS_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(level=logging.ERROR)
        self._send = send
        self._env = env
        self._per_minute = per_minute
        self._clock = clock
        self._window_start = clock()
        self._sent_in_window = 0
        self._dropped = 0  # 이번 분에 넘친 건수
        self._dropped_note = 0  # 지난 분에 넘친 건수 — 다음 알림 머리에 적는다
        self._stopped = False  # logging.Handler 의 _closed 와 겹치지 않는 이름
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._drain, name="alert-webhook", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self._stopped:
            return
        try:
            note = self._admit()
            if note is None:
                return
            self._queue.put(note + format_alert(record, env=self._env))
        except Exception:
            self.handleError(record)

    def _admit(self) -> str | None:
        """이번 알림을 보낼지. 보내면 머리에 붙일 글(넘친 건수, 없으면 빈 글), 안 보내면 None."""
        now = self._clock()
        if now - self._window_start >= _WINDOW_SECONDS:
            self._window_start = now
            self._sent_in_window = 0
            self._dropped_note = self._dropped
            self._dropped = 0
        if self._sent_in_window >= self._per_minute:
            self._dropped += 1
            return None
        self._sent_in_window += 1
        note, self._dropped_note = self._dropped_note, 0
        return f"(지난 1분 {note}건 생략)\n" if note else ""

    def _drain(self) -> None:
        while True:
            text = self._queue.get()
            try:
                if text is _STOP:
                    return
                try:
                    self._send(text)
                except Exception as exc:
                    # WARNING 은 이 핸들러(ERROR 이상)로 다시 오지 않는다 — 되돌이가 없다.
                    log.warning("알림 전송 실패 %s", type(exc).__name__)
            finally:
                self._queue.task_done()

    def flush(self) -> None:
        if not self._stopped:
            self._queue.join()

    def close(self) -> None:
        if not self._stopped:
            self._stopped = True
            self._queue.put(_STOP)
            self._thread.join(timeout=5.0)
        super().close()


def configure_alerts(
    url: str, *, env: str, make_sender: Callable[[str], Sender]
) -> WebhookHandler | None:
    """웹훅 주소가 있으면 app 로거에 핸들러를 단다. 없으면 아무것도 하지 않는다 (None).

    app 한 곳에만 — app.* 전부가 올라온다. uvicorn.error 는 대상이 아니다: "Exception in ASGI
    application" 은 errors.py 의 마지막 그물이 이미 ERROR 로 찍은 같은 예외라, 둘 다 보내면 알림이
    두 번 온다. 여러 번 불려도 하나만 단다.
    make_sender 는 주소로 보내기 함수를 만든다 — 운영은 integrations/discord.webhook_sender,
    테스트는 가짜. 주소가 비어 있으면 부르지 않는다.
    """
    url = url.strip()
    if not url:
        return None
    app_logger = logging.getLogger("app")
    for existing in app_logger.handlers:
        if isinstance(existing, WebhookHandler):
            return existing
    handler = WebhookHandler(make_sender(url), env=env)
    app_logger.addHandler(handler)
    return handler
