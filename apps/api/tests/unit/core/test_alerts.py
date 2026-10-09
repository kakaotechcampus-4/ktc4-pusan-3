"""ERROR 이상 로그를 Discord 웹훅으로 알린다 (멘토 #267 2번).

로그는 서버에 남지만 아무도 보지 않는다. ERROR 가 나면 사람에게 가야 한다 — 팀이 쓰는 Discord 로.

🚨 알림에는 코드에 적힌 로그 글귀 · 위치 · 예외 종류 이름만 간다. 로그에 넣은 값(args) ·
   트레이스백 · 예외 메시지는 서버 로그에만 있다 (루트 CLAUDE.md §2). 한 줄만 잘못 써도 아이
   이야기가 Discord 로 가는 일이 없게 — 값은 애초에 밖으로 안 나간다.

보내는 쪽은 가짜 함수다 — CI 가 Discord 를 부르지 않는다. 진짜 Discord 는 README 절차로 한 번
확인한다.
"""

import logging
import os
import re
import subprocess
import sys
import threading

import pytest

from app.core import alerts

ENV = "prod"
URL = "https://discord.com/api/webhooks/1/token"
FIRST_LINE = re.compile(r"^\[prod\] ERROR app\.alerts_probe:\w+:\d+ — (?P<rest>.*)$")


class _Sent(list[str]):
    """보내기 함수 모양(글, 출처)을 그대로 받아 글만 모은다 — `attach(sent.append)` 로 쓴다."""

    def append(self, text: str, source: str = "api") -> None:  # type: ignore[override]
        super().append(text)


@pytest.fixture
def sent() -> _Sent:
    return _Sent()


@pytest.fixture
def attach(request):
    """핸들러를 만들어 시험용 로거에 달고, 테스트가 끝나면 떼고 닫는다. (로거, 핸들러) 를 돌려준다.

    보내기 사이 간격은 0 — 간격은 자기 테스트에서만 본다.
    """

    def _attach(send, *, logger_name="app.alerts_probe", **kw):
        kw.setdefault("send_gap", 0)
        handler = alerts.WebhookHandler(send, env=ENV, **kw)
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)

        def teardown():
            logger.removeHandler(handler)
            handler.close()

        request.addfinalizer(teardown)
        return logger, handler

    return _attach


def _template(text: str) -> str:
    """알림 글에서 로그 글귀 부분. 머리에 생략 건수가 붙어 있으면 건너뛴다."""
    for line in text.splitlines():
        found = FIRST_LINE.match(line)
        if found:
            return found["rest"]
    raise AssertionError(f"알림 첫 줄 모양이 아니다: {text!r}")


def test_error_log_goes_out_as_its_template_and_location_without_values(attach, sent):
    """ERROR 한 줄이 알림 하나가 된다 — 어느 환경 · 어느 코드 · 코드에 적힌 글귀 그대로.

    🚨 %s 를 채운 값은 나가지 않는다. runner._guarded 가 찍는 모양 그대로 넣는다 — run id 도, 둘째
       줄부터의 코드 위치(트레이스백)도 서버 로그에만 있다. 누가 `log.error("%s", 원문)` 을 써도
       Discord 로 가는 건 "%s" 다.
    """
    log, handler = attach(sent.append)
    log.error(
        "run %s 의 job 이 %s 로 끝났다\n%s", "RUN-1", "ValueError", '  File "app/x.py", line 1'
    )
    handler.flush()

    assert len(sent) == 1
    assert _template(sent[0]) == "run %s 의 job 이 %s 로 끝났다"
    assert "RUN-1" not in sent[0]
    assert "File" not in sent[0]


def test_alert_time_has_the_offset_like_the_server_log(attach, sent):
    """둘째 줄의 시간에 시간대(+0900)가 붙는다 — stderr 로그와 같은 모양이라야 알림을 보고 로그를
    찾는다.
    """
    log, handler = attach(sent.append)
    log.error("x")
    handler.flush()

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}", sent[0].splitlines()[1])


def test_exception_type_goes_out_but_not_its_message(attach, sent):
    """🚨 log.exception 의 예외 메시지는 싣지 않는다 — 종류 이름만.

    errors.py 의 마지막 그물이 이렇게 찍는다. 예외 메시지에는 입력 원문이 섞일 수 있다.
    """
    log, handler = attach(sent.append)
    try:
        raise ValueError("SECRET-원문-문장")
    except ValueError:
        log.exception("처리되지 않은 예외")
    handler.flush()

    assert _template(sent[0]) == "처리되지 않은 예외 (ValueError)"
    assert "SECRET" not in sent[0]
    assert "Traceback" not in sent[0]


def test_object_logged_as_the_message_sends_only_its_type(attach, sent):
    """`log.error(exc)` 처럼 객체를 바로 찍으면 글귀가 아니라 값이다 — 종류 이름만 간다."""
    log, handler = attach(sent.append)
    log.error(ValueError("SECRET-원문"))
    handler.flush()

    assert _template(sent[0]) == "ValueError"
    assert "SECRET" not in sent[0]


def test_warning_is_not_sent(attach, sent):
    """WARNING 은 서버 로그에만 남는다. 알림은 ERROR 이상이다."""
    log, handler = attach(sent.append)
    log.warning("매핑 없는 HTTPException status=%s", 418)
    handler.flush()

    assert sent == []


def test_the_handlers_own_warnings_are_never_alerted(attach, sent):
    """전송 실패 WARNING 은 app.core.alerts 로거로 나간다 — 그 로거는 레벨과 무관하게 알리지 않는다.

    알리면 실패 → 경고 → 알림 → 실패 … 되돌이가 된다. 레벨을 WARNING 으로 낮춰도 안전해야 한다.
    """
    log, handler = attach(sent.append, logger_name="app.core.alerts")
    log.error("전송 실패를 누가 ERROR 로 올려도")
    handler.flush()

    assert sent == []


def test_sender_failure_stays_inside_the_handler(attach, caplog):
    """🚨 알림을 못 보내도 로그를 찍은 코드(요청 처리)는 아무 영향이 없다.

    실패는 WARNING 한 줄로만 남긴다 — 모르는 예외는 종류 이름만. httpx 의 오류 메시지에는 웹훅
    URL(비밀)이 들어 있어서 메시지를 찍으면 서버 로그에 비밀이 남는다.
    """

    def broken(_text: str, _source: str) -> None:
        raise RuntimeError("POST https://discord.com/api/webhooks/1/SECRET-TOKEN failed")

    log, handler = attach(broken)
    with caplog.at_level(logging.WARNING, logger="app.core.alerts"):
        log.error("무언가 실패")  # 여기서 예외가 올라오면 테스트가 바로 죽는다
        handler.flush()

    warnings = [r for r in caplog.records if r.name == "app.core.alerts"]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert "RuntimeError" in warnings[0].getMessage()
    assert "SECRET-TOKEN" not in warnings[0].getMessage()


def test_send_error_message_is_logged_as_is(attach, caplog):
    """SendError 는 메시지가 안전하다는 약속이다 — 상태 코드가 그대로 로그에 남아 원인을 가를 수
    있다.
    """

    def refused(_text: str, _source: str) -> None:
        raise alerts.SendError("webhook 429")

    log, handler = attach(refused)
    with caplog.at_level(logging.WARNING, logger="app.core.alerts"):
        log.error("무언가 실패")
        handler.flush()

    assert any(
        "webhook 429" in r.getMessage() for r in caplog.records if r.name == "app.core.alerts"
    )


def test_failed_sends_are_counted_in_the_next_alert(attach, sent):
    """보내다 실패한 것도 생략 건수에 들어간다 — 실패한 알림이 들고 가던 건수까지 되살린다."""
    calls: list[str] = []

    def flaky(text: str, _source: str) -> None:
        calls.append(text)
        if len(calls) <= 2:
            raise alerts.SendError("webhook 500")
        sent.append(text)

    log, handler = attach(flaky)
    for template in ("첫 번째", "두 번째", "세 번째"):
        log.error(template)
        handler.flush()

    assert len(sent) == 1
    assert sent[0].startswith("(앞서 2건 생략)\n")
    assert _template(sent[0]) == "세 번째"


def test_at_most_n_per_minute_then_the_next_alert_counts_the_dropped(attach, sent):
    """같은 오류가 쏟아져도 Discord 를 도배하지 않는다 — 분당 상한, 넘친 건 다음 알림에 건수로.

    DB 가 죽으면 요청마다 ERROR 다. 상한이 없으면 Discord 가 429 로 막고, 정작 봐야 할
    알림이 사라진다.
    """
    now = [1000.0]
    log, handler = attach(sent.append, per_minute=3, clock=lambda: now[0])
    for i in range(5):
        log.error("오류 %d", i)
    handler.flush()
    assert len(sent) == 3

    now[0] += 61
    log.error("오류 다음 분")
    handler.flush()

    assert len(sent) == 4
    assert sent[3].startswith("(앞서 2건 생략)\n")


class _StuckSender:
    """부르면 entered 를 켜고 release 가 켜질 때까지 멈춘다 — Discord 가 응답하지 않는 상황."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.sent: list[str] = []

    def __call__(self, text: str, _source: str) -> None:
        self.entered.set()
        self.release.wait(5)
        self.sent.append(text)


def test_flush_gives_up_after_its_deadline_when_the_sender_is_stuck(attach):
    """🚨 flush 는 기한을 넘기지 않는다 — 종료(logging.shutdown)가 flush 를 부른다.

    Discord 가 응답하지 않을 때 큐가 빌 때까지 기다리면 프로세스가 안 끝난다. docker 는 10초 뒤
    SIGKILL 이고, README 의 확인 명령도 잘못된 주소에서 멈춘다. 기한 안에 못 보낸 건 버린다.
    """
    stuck = _StuckSender()
    log, handler = attach(stuck, flush_timeout=0.2)
    log.error("응답 없는 웹훅")
    assert stuck.entered.wait(1)

    started = time_monotonic()
    handler.flush()
    waited = time_monotonic() - started
    stuck.release.set()

    assert waited < 1.0


def time_monotonic() -> float:
    import time

    return time.monotonic()


def test_overflow_while_the_sender_is_stuck_is_counted_not_queued(attach):
    """큐는 분당 상한만큼만 담는다 — Discord 가 느리면 밀린 알림을 끝없이 쌓지 않고 생략으로 센다.

    쌓아 두면 메모리도 늘지만, 복구 순간 몇 시간 전 알림이 한꺼번에 쏟아지고 종료도 그만큼
    늦어진다.
    """
    now = [1000.0]
    stuck = _StuckSender()
    log, handler = attach(stuck, per_minute=2, clock=lambda: now[0])
    log.error("1")  # 스레드가 집어 가 send 안에서 멈춘다
    assert stuck.entered.wait(1)
    log.error("2")  # 큐 [2]
    now[0] += 61
    log.error("3")  # 큐 [2, 3] — 상한 2 가득
    log.error("4")  # 자리가 없다 → 생략으로 센다
    now[0] += 61
    stuck.release.set()
    handler.flush()
    log.error("5")  # 다음 알림 머리에 생략 건수
    handler.flush()

    assert [_template(t) for t in stuck.sent] == ["1", "2", "3", "5"]
    assert stuck.sent[3].startswith("(앞서 1건 생략)\n")


def test_sends_are_spaced_out(attach):
    """보내기 사이에 쉰다 — Discord 는 웹훅 하나에 2초당 5건쯤에서 429 를 내고, 몰아 보내면 앞 몇
    건만 들어간다. 상한 10건/분이면 0.5초 간격으로도 5초 안에 다 나간다.
    """
    sleeps: list[float] = []
    log, handler = attach(lambda _t, _s: None, send_gap=0.5, sleep=sleeps.append)
    for i in range(3):
        log.error("오류 %d", i)
    handler.flush()

    assert sleeps == [0.5, 0.5, 0.5]


def test_alert_detail_marked_safe_goes_out_after_the_template(attach, sent):
    """값을 내보내는 유일한 길 — extra={"alert_detail": ...}. 서버가 모양을 검사한 값만 여기 넣는다.

    화면 오류 보고(client_errors)가 쓴다. 일반 %s 값은 여전히 안 나간다 — 내보낼 값은 코드가
    명시적으로 표시해야 하고, 어디서 내보내는지 grep 한 번으로 다 보인다.
    """
    log, handler = attach(sent.append)
    detail = "TypeError /records android 14 app"
    log.error("화면 오류 name=%s", "TypeError", extra={"alert_detail": detail})
    handler.flush()

    assert _template(sent[0]) == "화면 오류 name=%s · TypeError /records android 14 app"


def test_alert_detail_is_one_short_line(attach, sent):
    """detail 은 한 줄 · 200자까지 — 줄바꿈이나 긴 글이 들어와도 알림 모양이 깨지지 않는다."""
    log, handler = attach(sent.append)
    log.error("x", extra={"alert_detail": "첫 줄\n둘째 줄 " + "a" * 300})
    handler.flush()

    detail = _template(sent[0]).split(" · ", 1)[1]
    assert "\n" not in detail
    assert len(detail) <= 200
    assert detail.startswith("첫 줄 둘째 줄")


def test_alert_source_reaches_the_sender(attach):
    """출처(api · web · infra)를 보내기 함수에 넘긴다 — Discord 에서 보내는 이름이 갈린다.

    기본은 api 다. 화면 오류 보고는 extra={"alert_source": "browser"} 으로 찍는다.
    """
    sources: list[str] = []
    log, handler = attach(lambda _text, source: sources.append(source))
    log.error("api 쪽")
    log.error("화면 쪽", extra={"alert_source": "browser"})
    handler.flush()

    assert sources == ["api", "browser"]


def test_configure_attaches_to_the_app_logger_only(request, sent):
    """app 로거 한 곳에만 단다 — app.* 전부가 올라오고, uvicorn 은 대상이 아니다.

    uvicorn.error 의 "Exception in ASGI application" 은 errors.py 의 마지막 그물이 이미
    ERROR 로 찍은 같은 예외다. 둘 다 보내면 알림이 두 번 온다.
    """
    handler = alerts.configure_alerts(sent.append, env=ENV)

    def teardown():
        logging.getLogger("app").removeHandler(handler)
        handler.close()

    request.addfinalizer(teardown)
    logging.getLogger("app.alerts_probe_wired").error("app 쪽 오류")
    logging.getLogger("uvicorn.error").error("Exception in ASGI application")
    handler.flush()

    assert len(sent) == 1
    assert "app 쪽 오류" in sent[0]


MAIN_PROBE = """
import logging

import app.main  # 서버가 뜰 때처럼 — 설정을 읽고 핸들러를 단다

from app.core.alerts import WebhookHandler

attached = any(isinstance(h, WebhookHandler) for h in logging.getLogger("app").handlers)
print("attached" if attached else "none")
"""
"""새 프로세스에서 본다 — 설정은 import 때 한 번 읽히므로 이 프로세스 안에서는 바꿀 수 없다.
환경변수는 .env 보다 먼저라, 내 .env 에 무엇이 있든 아래 값이 이긴다."""


def _main_probe(**env: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", MAIN_PROBE],
        cwd=alerts.__file__.rsplit("/app/", 1)[0],  # apps/api
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **env},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_main_attaches_the_handler_when_the_url_is_set():
    """app.main 이 설정의 ALERT_WEBHOOK_URL 로 핸들러를 단다 (dev · prod)."""
    assert _main_probe(ALERT_WEBHOOK_URL=URL, APP_ENV="dev") == "attached"


def test_main_keeps_alerts_off_in_local_even_with_a_url():
    """🚨 local 에서는 주소가 있어도 안 단다 — 노트북의 오류가 팀 채널로 가지 않게."""
    assert _main_probe(ALERT_WEBHOOK_URL=URL, APP_ENV="local") == "none"


def test_app_logger_has_no_alert_handler_under_tests():
    """비어 있으면 안 단다 — 그리고 테스트가 도는 이 프로세스가 바로 그 상태다.

    conftest 가 app.main 을 불러왔고, tests/__init__.py 가 그 전에 주소를 비웠다. 여기서 핸들러가
    보이면 main 이 주소 없이도 달았거나(버그) 안전장치가 conftest 보다 늦게 돈 것이다.
    """
    assert "app.main" in sys.modules
    assert not any(isinstance(h, alerts.WebhookHandler) for h in logging.getLogger("app").handlers)
