"""앱 로그 설정 — app.* 로그를 시간 · 레벨 · 이름과 함께 stderr 로 (#197 후속)

uvicorn 은 자기 로거(uvicorn · uvicorn.error · uvicorn.access)만 설정한다. 앱이 따로 설정하지
않으면 app.* 로그는 파이썬 기본 처리로 떨어져 WARNING 이상만, 시간도 이름도 없이 찍힌다.

🚨 루트 로거는 건드리지 않는다 (WARNING 그대로). 루트를 INFO 로 열면 라이브러리 로그까지
   열린다 — httpx 는 INFO 로 요청 주소를 찍는데, 주소에 키를 싣는 API 가 있다 (NEIS 의 KEY=).
🚨 무엇을 남기는지는 그대로다 — 원문 대신 id (루트 CLAUDE.md §2 개인정보).

app 로그는 루트로도 올라간다(propagate). pytest 의 caplog 가 루트에서 받기 때문이다. 그래서
누가 루트에 처리기를 달면 app 로그가 두 줄씩 찍힌다 — 예: OPENAI_LOG 환경변수를 켜면 openai
SDK 가 basicConfig 를 부른다.

모듈 이름이 logging 이 아닌 이유 — 표준 라이브러리 logging 과 헷갈리지 않게.
"""

import logging.config

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S%z"
"""시간대(+0900 등)까지. 개발 맥은 한국 시간, 컨테이너는 보통 UTC 라 말없이 9시간 어긋난다."""


def configure_logging() -> None:
    """app 로거에 처리기를 하나 단다. 여러 번 불러도 처리기는 하나다 (dictConfig 가 갈아 끼운다).

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
            "formatters": {"plain": {"format": _FORMAT, "datefmt": _DATEFMT}},
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
