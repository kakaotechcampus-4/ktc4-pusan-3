"""ERROR 이상 로그를 Discord 웹훅으로 알린다 (멘토 #267 2번).

로그는 서버에 남지만 아무도 보지 않는다. ERROR 가 나면 사람에게 가야 한다 — 팀이 쓰는 Discord 로.

🚨 알림 본문에 원문 · 트레이스백 · 예외 메시지를 싣지 않는다 (루트 CLAUDE.md §2).
   서버 로그의 ERROR 는 이미 id · 예외 종류 · 코드 위치만 남기지만, log.exception 의
   예외 메시지에는 입력 원문이 섞일 수 있다 (pydantic 의 input_value 등). 알림은
   메시지 첫 줄과 예외 종류 이름까지만 가져간다.

보내는 쪽은 가짜 함수다 — CI 가 Discord 를 부르지 않는다. 진짜 Discord 는 README 절차로
한 번 확인한다.
"""

import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core import alerts

ENV = "prod"


@pytest.fixture
def sent() -> list[str]:
    return []


@pytest.fixture
def handler(sent: list[str]) -> Iterator[alerts.WebhookHandler]:
    """보내기 함수를 가짜로 바꾼 핸들러. 큐 뒤의 스레드까지 그대로 돈다 — flush 로 기다린다."""
    h = alerts.WebhookHandler(sent.append, env=ENV)
    yield h
    h.close()


@pytest.fixture
def log(handler: alerts.WebhookHandler) -> Iterator[logging.Logger]:
    """app 아래의 시험용 로거. 테스트가 끝나면 핸들러를 뗀다."""
    logger = logging.getLogger("app.alerts_probe")
    logger.addHandler(handler)
    yield logger
    logger.removeHandler(handler)


def test_error_log_is_sent_with_env_level_logger_and_first_line(log, handler, sent):
    """ERROR 한 줄이 알림 하나가 된다 — 어느 환경 · 어느 모듈 · 무슨 일인지가 첫 줄에 있다.

    runner._guarded 가 찍는 모양 그대로 넣는다. 메시지의 둘째 줄부터(코드 위치 트레이스백)는
    싣지 않는다 — Discord 에서 읽을 건 한 줄이고, 자세한 건 서버 로그에 있다.
    """
    log.error(
        "run %s 의 job 이 %s 로 끝났다\n%s",
        "RUN-1",
        "ValueError",
        '  File "app/x.py", line 1, in job\n    raise ValueError()\n',
    )
    handler.flush()

    assert len(sent) == 1
    first_line = sent[0].splitlines()[0]
    assert "[prod]" in first_line
    assert "ERROR" in first_line
    assert "app.alerts_probe" in first_line
    assert "run RUN-1 의 job 이 ValueError 로 끝났다" in first_line
    assert "File" not in sent[0]


def test_exception_type_goes_out_but_not_its_message(log, handler, sent):
    """🚨 log.exception 의 예외 메시지는 싣지 않는다 — 종류 이름만.

    errors.py 의 마지막 그물이 이렇게 찍는다. 예외 메시지에는 입력 원문이 섞일 수 있다.
    """
    try:
        raise ValueError("SECRET-원문-문장")
    except ValueError:
        log.exception("처리되지 않은 예외")
    handler.flush()

    assert len(sent) == 1
    assert "ValueError" in sent[0]
    assert "SECRET" not in sent[0]
    assert "Traceback" not in sent[0]


def test_warning_is_not_sent(log, handler, sent):
    """WARNING 은 서버 로그에만 남는다. 알림은 ERROR 이상이다."""
    log.warning("매핑 없는 HTTPException status=%s", 418)
    handler.flush()

    assert sent == []


def test_sender_failure_stays_inside_the_handler(caplog):
    """🚨 알림을 못 보내도 로그를 찍은 코드(요청 처리)는 아무 영향이 없다.

    실패는 WARNING 한 줄로만 남긴다 — 예외 종류 이름만. httpx 의 오류 메시지에는 웹훅 URL(비밀)이
    들어 있어서 메시지를 찍으면 서버 로그에 비밀이 남는다.
    """

    def broken(_: str) -> None:
        raise RuntimeError("POST https://discord.example/api/webhooks/1/SECRET-TOKEN failed")

    handler = alerts.WebhookHandler(broken, env=ENV)
    logger = logging.getLogger("app.alerts_probe_broken")
    logger.addHandler(handler)
    try:
        with caplog.at_level(logging.WARNING, logger="app.core.alerts"):
            logger.error("무언가 실패")  # 여기서 예외가 올라오면 테스트가 바로 죽는다
            handler.flush()
    finally:
        logger.removeHandler(handler)
        handler.close()

    warnings = [r for r in caplog.records if r.name == "app.core.alerts"]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert "RuntimeError" in warnings[0].getMessage()
    assert "SECRET-TOKEN" not in warnings[0].getMessage()


def test_at_most_n_per_minute_then_the_next_alert_counts_the_dropped(sent):
    """같은 오류가 쏟아져도 Discord 를 도배하지 않는다 — 분당 상한, 넘친 건 다음 알림에 건수로.

    DB 가 죽으면 요청마다 ERROR 다. 상한이 없으면 Discord 가 429 로 막고, 정작 봐야 할
    알림이 사라진다.
    """
    now = [1000.0]
    handler = alerts.WebhookHandler(sent.append, env=ENV, per_minute=3, clock=lambda: now[0])
    logger = logging.getLogger("app.alerts_probe_storm")
    logger.addHandler(handler)
    try:
        for i in range(5):
            logger.error("오류 %d", i)
        handler.flush()
        assert len(sent) == 3

        now[0] += 61
        logger.error("오류 다음 분")
        handler.flush()
    finally:
        logger.removeHandler(handler)
        handler.close()

    assert len(sent) == 4
    assert "2건" in sent[3]
    assert "생략" in sent[3]


@pytest.mark.parametrize("url", ["", "   "])
def test_configure_without_url_attaches_nothing(url):
    """웹훅 주소가 없으면 알림만 꺼지고 서버는 그대로 뜬다 — 로컬 · 테스트의 기본 상태."""
    app_logger = logging.getLogger("app")
    before = list(app_logger.handlers)

    never = lambda _url: pytest.fail("주소가 없는데 보내기 함수를 만들었다")  # noqa: E731
    assert alerts.configure_alerts(url, env="local", make_sender=never) is None
    assert app_logger.handlers == before


def test_configure_sends_app_errors_but_not_uvicorn_errors(sent):
    """app 로거 한 곳에만 단다 — app.* 전부가 올라오고, uvicorn 은 대상이 아니다.

    uvicorn.error 의 "Exception in ASGI application" 은 errors.py 의 마지막 그물이 이미
    ERROR 로 찍은 같은 예외다. 둘 다 보내면 알림이 두 번 온다.
    """
    handler = alerts.configure_alerts(
        "https://discord.example/webhook", env=ENV, make_sender=lambda _url: sent.append
    )
    assert handler is not None
    try:
        logging.getLogger("app.alerts_probe_wired").error("app 쪽 오류")
        logging.getLogger("uvicorn.error").error("Exception in ASGI application")
        handler.flush()
    finally:
        logging.getLogger("app").removeHandler(handler)
        handler.close()

    assert len(sent) == 1
    assert "app 쪽 오류" in sent[0]


API_ROOT = Path(__file__).resolve().parents[3]
"""apps/api — 새 프로세스가 여기서 `import app.main` 을 한다 (test_logging_config 와 같다)."""

MAIN_PROBE = """
import logging

import app.main  # 서버가 뜰 때처럼 — 설정을 읽고 핸들러를 단다

from app.core.alerts import WebhookHandler

attached = any(isinstance(h, WebhookHandler) for h in logging.getLogger("app").handlers)
print("attached" if attached else "none")
"""


@pytest.mark.parametrize(
    ("url", "expected"),
    [("", "none"), ("https://discord.example/api/webhooks/1/token", "attached")],
)
def test_main_attaches_the_handler_only_when_the_url_is_set(url, expected):
    """app.main 이 설정의 ALERT_WEBHOOK_URL 로 핸들러를 단다. 비어 있으면 안 단다.

    새 프로세스에서 본다 — 설정은 import 때 한 번 읽히므로 이 프로세스 안에서는 바꿀 수 없다.
    환경변수는 .env 보다 먼저라, 내 .env 에 무엇이 있든 이 값이 이긴다.
    """
    result = subprocess.run(
        [sys.executable, "-c", MAIN_PROBE],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "ALERT_WEBHOOK_URL": url},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
