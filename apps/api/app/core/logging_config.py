"""앱 로그 설정 — app.* 로그를 시간 · 레벨 · 이름과 함께 stderr 로 (#197 후속)

uvicorn 은 자기 로거(uvicorn · uvicorn.error · uvicorn.access)만 설정한다. 앱이 따로 설정하지
않으면 app.* 로그는 파이썬 기본 처리로 떨어져 WARNING 이상만, 시간도 이름도 없이 찍힌다.

🚨 루트 로거는 건드리지 않는다 (WARNING 그대로). 루트를 INFO 로 열면 라이브러리 로그까지
   열린다 — httpx 는 INFO 로 요청 주소를 찍는데, 주소에 키를 싣는 API 가 있다
   (공공데이터포털의 serviceKey= · 식품안전나라는 경로에 키).
🚨 무엇을 남기는지는 그대로다 — 원문 대신 id (루트 CLAUDE.md §2 개인정보).

app 로그는 루트로도 올라간다(propagate). pytest 의 caplog 가 루트에서 받기 때문이다. 그래서
누가 루트에 처리기를 달면 app 로그가 두 줄씩 찍힌다 — 예: OPENAI_LOG 환경변수를 켜면 openai
SDK 가 basicConfig 를 부른다.

uvicorn 의 접근 로그(요청마다 한 줄)는 쿼리 문자열과 초대 코드를 가린다 — `_RedactAccessLog`.
요청 처리 중 예외가 uvicorn 오류 로그에 다시 찍힐 때는 메시지를 빼고 종류 · 코드 위치만 남긴다 —
`_HideExceptionMessage`. 서버 시작 · 종료(lifespan) 실패는 Starlette 이 traceback 을 글자로 만들어
exc_info 없이 넘겨 이 거름망을 비켜 간다 — lifespan 에 원문이 들어갈 일이 생기면 따로 막는다.
app 코드는 log.exception 대신 `log_error_without_message` 로 같은 모양을 남긴다.

모듈 이름이 logging 이 아닌 이유 — 표준 라이브러리 logging 과 헷갈리지 않게.
"""

import logging
import logging.config
import re
import traceback

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S%z"
"""시간대(+0900 등)까지. 개발 맥은 한국 시간, 컨테이너는 보통 UTC 라 말없이 9시간 어긋난다."""

_INVITE_CODE = re.compile(r"(/invites/+)[^/?]+", re.IGNORECASE)
"""초대 코드는 경로에 들어 있다 — /invites/{code} · /invites/{code}/accept.

대소문자 · 빗금 여러 개도 잡는다(라우트에는 안 맞아 404 지만 원문은 찍힌다). 발행 주소
(/children/{cid}/invites — /invites 로 끝남)는 해당 없다. 나중에 /invites/{id} 모양의 주소가 생기면
그 id 도 가려진다 — 덜 가리는 것보다 낫다.
"""


def _redact(text: str) -> str:
    """쿼리 문자열 전체와 초대 코드를 가린다. 경로는 남긴다."""
    path, has_query, _ = text.partition("?")
    path = _INVITE_CODE.sub(r"\1[redacted]", path)
    return f"{path}?[redacted]" if has_query else path


class _RedactAccessLog(logging.Filter):
    """uvicorn 접근 로그에서 쿼리 문자열과 초대 코드를 가린다 (#214 리뷰 · #239).

    🚨 uvicorn 은 주소를 쿼리째 찍는다. 카카오 콜백의 인가 코드 · state, 로그인 시작의 bind 원문이
       쿼리에 있고, 초대 코드는 경로에 있다 (auth-kakao-v1 §7-5 "콜백 URL 전체를 로깅하지 않는다").
       경로 · 상태 코드 · id(아이 · run)는 남긴다 — 어느 주소가 어떻게 끝났는지는 운영에 필요하다.

    uvicorn 은 `'%s - "%s %s HTTP/%s" %d'` 에 값 5개(주소 · 메서드 · 경로 · 버전 · 상태)를 넘기지만,
    그 모양에 기대지 않고 **글자 값 전부**에 같은 가리기를 건다. uvicorn 을 올려 모양이 바뀌어도
    원문이 조용히 새지 않게(안전한 쪽으로 실패하게) — 주소 · 메서드 · 버전에는 `?` 도 `/invites/` 도
    없어서 엉뚱한 값이 가려지지 않는다. 값 개수는 그대로라 uvicorn 의 AccessFormatter 도
    그대로 푼다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def _kinds(exc: BaseException, seen: set[int]) -> str:
    """종류 이름만 — 묶음 안의 예외는 [ ] 로, 원인(__cause__ · __context__)은 ← 로 잇는다."""
    seen.add(id(exc))
    text = type(exc).__name__
    if isinstance(exc, BaseExceptionGroup):
        text += f"[{', '.join(_kinds(e, seen) for e in exc.exceptions if id(e) not in seen)}]"
    cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    if cause is not None and id(cause) not in seen:
        text += f" ← {_kinds(cause, seen)}"
    return text


def describe_exception(exc: BaseException) -> str:
    """예외를 메시지 없이 — 종류(연결된 · 묶인 예외의 종류까지)와 코드 위치 (#670).

    🚨 메시지에는 입력 원문이 섞일 수 있다 — ValueError(f"... {text}") · DB 오류의 DETAIL.
    코드 위치(format_tb)는 소스 줄이지 그때의 값이 아니다 — 코드는 공개 저장소에 있다.
    """
    return f"{_kinds(exc, set())}\n{''.join(traceback.format_tb(exc.__traceback__))}"


def log_error_without_message(log: logging.Logger, msg: str, exc: BaseException) -> None:
    """log.exception 대신 — 예외 메시지 없이 ERROR 한 줄 (#670 · 루트 §2).

    msg 는 코드에 적힌 고정 글귀다(값을 넣지 않는다). Discord 카드 제목은 "msg 예외종류" —
    exc_info 를 싣지 않아 제목을 직접 준다(core/alerts.py). 위치는 부른 쪽 줄로 남긴다.
    """
    log.error(
        "%s %s",
        msg,
        describe_exception(exc),
        extra={"alert_heading": f"{msg} {type(exc).__name__}"},
        stacklevel=2,
    )


class _HideExceptionMessage(logging.Filter):
    """uvicorn 오류 로그의 예외에서 메시지를 빼고 종류와 코드 위치만 남긴다 (#670).

    🚨 처리되지 않은 예외는 API 의 마지막 그물(api/errors.py)이 메시지 없이 찍은 뒤, Starlette 이
       다시 던져 uvicorn 이 "Exception in ASGI application" 으로 traceback 전체를 한 번 더 찍는다.
       그 마지막 줄 "ValueError: <메시지>" 에 입력 원문이 섞일 수 있다 (루트 §2 개인정보).
       연결된 예외(__cause__ · __context__)의 메시지도 같은 이유로 남기지 않는다 — 종류만.
    🚨 지역 변수를 찍는 traceback(rich 의 show_locals 같은)은 쓰지 않는다. 그때부터 코드가 아니라
       값이 찍힌다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            # 다 만든 글로 바꾼다 — args 를 남기면 uvicorn 포매터가 한 번 더 끼워 넣다 깨진다
            record.msg = f"{record.getMessage().rstrip()} {describe_exception(exc)}"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True


def configure_logging() -> None:
    """app 로거에 처리기를 하나 단다. 여러 번 불러도 처리기는 하나다 (dictConfig 가 갈아 끼운다).

    🚨 갈아 끼운다는 건 app 로거에 먼저 달린 다른 처리기도 닫고 뗀다는 뜻이다 — 알림 핸들러
       (core/alerts.py)가 그렇다. 그래서 알림은 이 함수 뒤에 달고, 이 함수를 다시 부르지 않는다.

    🚨 disable_existing_loggers 를 끄는 이유 — 켜 두면(dictConfig 의 기본값) 이 함수보다 먼저
       만들어진 app 밖의 로거가 전부 꺼진다. 거기에 uvicorn 의 로거가 들어 있다 — uvicorn 은
       자기 로그를 먼저 설정하고 앱을 나중에 불러오므로, 꺼지면 시작 줄 · 요청 줄 ·
       "Exception in ASGI application" 오류가 말없이 사라진다. sqlalchemy · httpx 경고도 같다.
       (app 아래 로거는 이 값과 상관없이 꺼지지 않고 초기화만 된다.)
    """
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"plain": {"format": _FORMAT, "datefmt": DATEFMT}},
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "formatter": "plain",
                    "stream": "ext://sys.stderr",
                },
            },
            "loggers": {"app": {"level": "INFO", "handlers": ["stderr"]}},
        }
    )
    # uvicorn 이 자기 로그를 먼저 설정한 뒤라, 그 로거에 거름망만 더한다(설정을 다시 하지 않는다).
    # 여러 번 불려도 하나만 단다.
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactAccessLog) for f in access.filters):
        access.addFilter(_RedactAccessLog())
    errors = logging.getLogger("uvicorn.error")
    if not any(isinstance(f, _HideExceptionMessage) for f in errors.filters):
        errors.addFilter(_HideExceptionMessage())
    # 🚨 httpx 는 INFO 로 요청 주소 전체를 찍는다 — NEIS 의 KEY=, Discord 웹훅의 토큰이 주소에 있다.
    #    루트가 INFO 로 열리는 일(OPENAI_LOG · basicConfig)이 있어도 새지 않게 WARNING 으로 못
    #    박는다.
    logging.getLogger("httpx").setLevel(logging.WARNING)
