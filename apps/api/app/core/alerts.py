"""ERROR 이상 로그를 Discord 웹훅으로 알린다 (멘토 #267 2번 · 10-09 결정).

로그는 서버에 남지만 아무도 보지 않는다. ERROR 가 나면 사람에게 가야 한다 — 팀이 쓰는 Discord 로.
적재(로그를 남겨 두는 것)는 여기서 하지 않는다 — docker 의 로그 드라이버가 한다
(docs/ops/logging-alerts-v1.md). Discord 로 보내는 HTTP 는 app/integrations/discord.py 다 —
외부 호출은 integrations 가 격리한다 (apps/api/CLAUDE.md). 여기는 보내기 함수를 받기만 한다.

구조 — app 로거에 핸들러 하나.

    log.error(...) ─ emit: 카드 만들기 · 분당 상한 ─▶ 큐(상한만큼) ─▶ 스레드 하나 ─▶ send(카드)

    요청을 처리하는 이벤트 루프는 큐에 넣고 바로 돌아온다. HTTP 를 기다리는 건 스레드다.
    로그 핸들러 안에서 HTTP 를 직접 기다리면 그동안 서버의 모든 요청이 멈춘다.

🚨 카드(Discord embed)에 무엇이 가는지는 format_alert 가 정한다 — 코드에 적힌 로그 글귀 · 위치 ·
   예외 종류 이름. 로그에 넣은 값(args) · 트레이스백 · 예외 메시지는 밖으로 나가지 않는다
   (루트 CLAUDE.md §2 · §10). 값을 내보내는 길은 `extra={"alert_detail": ...}`(값 줄) 과
   `extra={"alert_heading": ...}`(제목) 둘뿐 — 서버가 모양을 검사한 값만 여기 넣는다
   (화면 오류 보고). 어디서 내보내는지 `grep -rn alert_ app` 한 번으로 다 보인다.
   `extra={"alert_source": "browser"}` 은 출처다 (기본 api · 감시 스크립트는 infra) — Discord 에서
   보내는 이름(api-alert · browser-alert · infra-alert)이 갈려 채널 하나에서도 구별된다.
🚨 알림이 실패해도 요청 처리는 영향이 없다. 실패는 WARNING 한 줄 — SendError 면 그 메시지, 다른
   예외는 종류 이름만 (httpx 의 메시지에는 웹훅 URL(비밀)이 통째로 있다).
🚨 Discord 가 막히거나 느려도 서버 종료를 붙들지 않는다 — flush 는 기한이 있고, 큐는 분당 상한만큼만
   담고, 보내기 사이를 띄운다. 못 보낸 건 다음 알림 머리에 건수로 적는다.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

log = logging.getLogger(__name__)

Sender = Callable[[dict, str], None]
"""알림 embed 하나를 보내는 함수 — (embed dict, 출처). 실패는 예외로 올린다 — 핸들러가 받아
WARNING 으로 남긴다."""

DEFAULT_SOURCE = "api"
_DETAIL_MAX = 200

SEVERITY_MARK = {"high": "🔴", "normal": "🟠", "low": "🟡"}
"""알림 머리의 세기 표시 — 전부 같은 세기로 오면 다 무시하게 된다(현업의 심각도 구분을 가장 작게).
🔴 지금 봐야 함 · 🟠 오늘 안에 · 🟡 내일 봐도 됨. `extra={"alert_severity": "high"}` 로 바꿀 수
있다.
감시 스크립트는 🔴(죽음) · 🟢(복구)를 자기가 붙인다."""
DEFAULT_SEVERITY = {"api": "normal", "browser": "low"}
SEVERITY_COLOR = {"high": 0xFF0000, "normal": 0xFFA500, "low": 0xFFD700}
"""embed 사이드바 색. SEVERITY_MARK 와 같은 키."""
NEXT_STEP = {
    "api": 'journalctl CONTAINER_NAME=ktc4-api --since "10 min ago" | grep ERROR',
    "browser": 'journalctl CONTAINER_NAME=ktc4-api --since "10 min ago" | grep "화면 오류"',
}
"""카드 바닥글의 "다음에 칠 명령" — 알림을 보고 뭘 할지 바로 알게 (런북의 가장 작은 모양).
바닥글은 Discord 가 마크다운을 안 받아 코드 모양이 아니다."""


class SendError(Exception):
    """보내기 함수가 올리는 실패. 메시지에 비밀(URL)이 없어서 로그에 그대로 찍어도 된다.

    다른 예외는 종류 이름만 찍는다 — httpx 의 메시지에는 웹훅 URL 이 통째로 들어 있다.
    """


ALERTS_PER_MINUTE = 10
"""DB 가 죽으면 요청마다 ERROR 다. Discord 는 웹훅 하나에 분당 30건쯤에서 429 로 막고, 그때부터
정작 봐야 할 알림이 사라진다. 큐의 길이도 이 값이다 — Discord 가 느려서 밀린 알림을 끝없이 쌓지
않는다 (복구되는 순간 몇 시간 전 알림이 쏟아지고, 종료도 그만큼 늦어진다)."""
SEND_GAP_SECONDS = 0.5
"""보내기 사이 간격. Discord 는 2초당 5건쯤부터 429 다 — 몰아 보내면 앞 몇 건만 들어간다."""
FLUSH_TIMEOUT_SECONDS = 4.0
"""flush(종료 때 logging.shutdown 이 부른다)가 큐가 비기를 기다리는 상한. docker 는 멈추라고 한 뒤
10초면 SIGKILL 이다 — 그 안에 끝나야 한다. 못 보낸 알림은 버린다."""
_THREAD_JOIN_SECONDS = 1.0
_WINDOW_SECONDS = 60.0


def format_alert(record: logging.LogRecord, *, env: str, source: str = DEFAULT_SOURCE) -> dict:
    """Discord embed dict 를 만든다. `## 🟠 Heading` 마크다운 헤더로 눈에 띄게.

    🚨 값은 나가지 않는다. 로그 글귀는 코드에 적힌 문장(record.msg, "run %s 의 job 이 %s 로 끝났다")
       그대로이고, %s 를 채운 값(args)은 서버 로그에만 있다. 예외도 종류 이름만.
    글귀가 str 이 아니면(`log.error(exc)` 처럼 객체를 바로 찍은 경우) 그 종류 이름만 쓴다.
    """
    template = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
    first_line = (template.splitlines() or [""])[0]
    exc_info = record.exc_info if isinstance(record.exc_info, tuple) else None
    exc_type = exc_info[0] if exc_info else None

    detail = getattr(record, "alert_detail", None)
    detail_str = (
        " ".join(detail.split())[:_DETAIL_MAX] if isinstance(detail, str) and detail else None
    )

    heading_override = getattr(record, "alert_heading", None)
    has_heading = isinstance(heading_override, str) and bool(heading_override)
    if has_heading:
        heading = heading_override
    elif exc_type is not None:
        heading = exc_type.__name__
    else:
        heading = first_line

    severity = getattr(record, "alert_severity", None)
    if severity not in SEVERITY_MARK:
        severity = DEFAULT_SEVERITY.get(source, "normal")
    mark = SEVERITY_MARK[severity]

    parts = [f"## {mark} {heading}"]
    # 제목이 예외 종류면 무슨 일인지는 글귀가 말한다 — detail 이 있어도 지우지 않는다.
    # alert_heading 을 준 쪽(화면 오류)은 제목 · detail 로 스스로 말하니 빈칸 글귀를 숨긴다.
    if not has_heading and heading != first_line:
        parts.append(first_line)
    if detail_str:
        parts.append(detail_str)

    where = f"{record.name}:{record.funcName}:{record.lineno}".removeprefix("app.")
    parts.append(f"\n`{where}` · **{env}**")

    embed: dict = {
        "color": SEVERITY_COLOR[severity],
        "description": "\n".join(parts),
        "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
    }
    footer_text = NEXT_STEP.get(source)
    if footer_text:
        embed["footer"] = {"text": footer_text}
    return embed


def alert_source(record: logging.LogRecord) -> str:
    """출처 — extra={"alert_source": ...} 가 있으면 그것, 없으면 api."""
    source = getattr(record, "alert_source", None)
    return source if isinstance(source, str) and source else DEFAULT_SOURCE


class WebhookHandler(logging.Handler):
    """ERROR 이상을 글로 만들어 큐에 넣는다. 스레드 하나가 꺼내 send 로 보낸다.

    emit 은 Handler.handle 이 잠금을 잡고 부른다. 생략 건수는 스레드도 더하므로 따로 작은 잠금을
    쓴다 — 스레드는 핸들러 잠금을 잡지 않아서, 종료가 flush 로 핸들러 잠금을 쥔 채 기다려도
    스레드가 막히지 않는다.
    flush 는 큐가 빌 때까지 기한 안에서 기다린다 — 테스트와 종료(logging.shutdown)가 쓴다.
    표준 QueueHandler + QueueListener 를 안 쓴 이유 — 그쪽은 기한 없이 기다려서(stop 의 join)
    Discord 가 막히면 종료가 붙들린다.
    """

    def __init__(
        self,
        send: Sender,
        *,
        env: str,
        per_minute: int = ALERTS_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        send_gap: float = SEND_GAP_SECONDS,
        flush_timeout: float = FLUSH_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(level=logging.ERROR)
        self._send = send
        self._env = env
        self._per_minute = per_minute
        self._clock = clock
        self._send_gap = send_gap
        self._flush_timeout = flush_timeout
        self._sleep = sleep
        self._window_start = clock()
        self._sent_in_window = 0
        self._dropped = 0  # 못 보낸 건수 — 다음 알림 머리에 적고 0 으로
        self._count_lock = threading.Lock()
        self._stopped = False  # logging.Handler 의 _closed 와 겹치지 않는 이름
        # 항목은 (이 알림이 들고 가는 생략 건수, embed, 출처). 보내다 실패하면 건수를 되살린다.
        self._queue: queue.Queue[tuple[int, dict, str] | None] = queue.Queue(maxsize=per_minute)
        self._thread = threading.Thread(target=self._drain, name="alert-webhook", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self._stopped or record.name == __name__:
            return  # 자기 WARNING(전송 실패)은 알리지 않는다 — 레벨을 낮춰도 되돌이가 없게
        try:
            source = alert_source(record)
            embed = format_alert(
                record, env=self._env, source=source
            )  # embed 를 먼저 — 실패하면 자리를 안 쓴다
            with self._count_lock:
                if not self._admit():
                    self._dropped += 1
                    return
                try:
                    self._queue.put_nowait((self._dropped, embed, source))
                except queue.Full:
                    self._dropped += 1  # 보내는 쪽이 막혀 있다 — 쌓지 않고 건수로 센다
                    return
                self._dropped = 0
        except Exception:
            self.handleError(record)

    def _admit(self) -> bool:
        """이번 분에 보낼 자리가 있나. 분이 바뀌면 자리를 다시 연다."""
        now = self._clock()
        if now - self._window_start >= _WINDOW_SECONDS:
            self._window_start = now
            self._sent_in_window = 0
        if self._sent_in_window >= self._per_minute:
            return False
        self._sent_in_window += 1
        return True

    def _drain(self) -> None:
        while (item := self._queue.get()) is not None:
            note, embed, source = item
            try:
                if note:
                    embed = {
                        **embed,
                        "description": f"(앞서 {note}건 생략)\n{embed['description']}",
                    }
                self._send(embed, source)
            except Exception as exc:
                with self._count_lock:
                    self._dropped += 1 + note  # 못 보냈다 — 들고 가던 건수까지 다음 알림으로
                detail = str(exc) if isinstance(exc, SendError) else type(exc).__name__
                # WARNING 은 이 핸들러(ERROR 이상 · 자기 이름 제외)로 다시 오지 않는다.
                log.warning("알림 전송 실패 %s", detail)
            finally:
                self._queue.task_done()
            self._sleep(self._send_gap)

    def flush(self) -> None:
        """큐가 빌 때까지 기다리되 기한을 넘기지 않는다. Queue.join 과 같은 방법에 기한만 더했다."""
        if self._stopped:
            return
        deadline = time.monotonic() + self._flush_timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._queue.all_tasks_done.wait(remaining)

    def close(self) -> None:
        if not self._stopped:
            self._stopped = True
            # 아직 큐에 남은 것은 버린다 — 종료를 붙들지 않는다 (flush 가 먼저 기한만큼 기다렸다).
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
                self._queue.task_done()
            try:
                self._queue.put_nowait(None)  # 스레드에게 끝 표시
            except queue.Full:
                pass  # 데몬 스레드라 프로세스와 함께 끝난다
            self._thread.join(timeout=_THREAD_JOIN_SECONDS)
        super().close()


def configure_alerts(send: Sender, *, env: str) -> WebhookHandler:
    """app 로거에 핸들러를 단다. 단 핸들러를 돌려준다 — 테스트가 떼고 닫는 데 쓴다.

    app 한 곳에만 — app.* 전부가 올라온다. uvicorn.error 는 대상이 아니다: "Exception in ASGI
    application" 은 errors.py 의 마지막 그물이 이미 ERROR 로 찍은 같은 예외라, 둘 다 보내면 알림이
    두 번 온다.
    🚨 configure_logging() 뒤에 불러야 한다 — 그쪽 dictConfig 가 app 로거의 처리기를 통째로 갈아
       끼워서, 먼저 달면 사라진다. 주소가 비어 있을 때 부르지 않는 것은 부르는 쪽(main.py)의 일이다.
    """
    handler = WebhookHandler(send, env=env)
    logging.getLogger("app").addHandler(handler)
    return handler
