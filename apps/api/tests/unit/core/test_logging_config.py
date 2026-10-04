"""앱 로그 설정 — app.* 로그가 시간 · 레벨 · 이름과 함께 보인다 (#197 후속)

uvicorn 은 자기 로거(uvicorn*)만 설정한다. 앱이 따로 설정하지 않으면 app.* 로그는 파이썬
기본 처리로 떨어져 WARNING 이상만, 시간도 이름도 없이 찍힌다 — info 는 전부 버려진다.

새 파이썬 프로세스에서 확인한다. pytest 는 자기 로그 수집기를 루트에 달아 두므로 같은
프로세스 안에서는 "설정이 없으면 버려진다" 를 볼 수 없다. 순서는 uvicorn 과 같게 한다 —
uvicorn 은 자기 로그 설정을 먼저 하고 앱(app.main)을 나중에 불러온다.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[3]
"""apps/api — 새 프로세스가 여기서 `import app.main` 을 한다."""

PROBE = """
import logging
import logging.config

from uvicorn.config import LOGGING_CONFIG

logging.config.dictConfig(LOGGING_CONFIG)  # uvicorn 이 먼저 하는 일
import app.main  # 그다음에 앱을 불러온다

logging.getLogger("app.probe").info("app-probe-line")
logging.getLogger("uvicorn.error").warning("uvicorn-probe-line")
logging.getLogger("httpx").info("https://open.example.test/hub?KEY=secret-key")
"""
"""표시 글자는 영문이다 — 파이프 인코딩이 UTF-8 이 아닌 환경에서 엉뚱한 이유로 깨지지 않게."""


@pytest.fixture(scope="module")
def stderr() -> str:
    """서버를 띄울 때와 같은 순서로 불러온 뒤 로그를 남기고, 찍힌 것을 돌려준다."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # 실패하면 새 프로세스의 오류 내용을 그대로 보여 준다 (check=True 는 그걸 버린다).
    assert result.returncode == 0, result.stderr
    return result.stderr


def test_app_info_log_is_shown_with_time_level_and_name(stderr):
    """info 가 버려지지 않고, 언제(시간대까지) · 어느 레벨 · 어느 모듈인지가 한 줄에 찍힌다.

    시간대를 붙이는 이유 — 개발 맥은 한국 시간, 컨테이너는 보통 UTC 라 말없이 9시간 어긋난다.
    """
    lines = [line for line in stderr.splitlines() if "app-probe-line" in line]

    assert len(lines) == 1
    assert "INFO" in lines[0]
    assert "app.probe" in lines[0]
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}", lines[0])


def test_uvicorn_logs_survive_app_logging_setup(stderr):
    """🚨 앱 로그 설정이 uvicorn 의 로거를 끄지 않는다.

    끄면 서버의 시작 줄 · 요청 줄 · "Exception in ASGI application" 오류가 말없이 사라진다.
    dictConfig 의 disable_existing_loggers 기본값이 바로 그렇게 한다.
    """
    assert "uvicorn-probe-line" in stderr


def test_library_info_log_stays_hidden(stderr):
    """🚨 루트 로거는 WARNING 그대로다 — 라이브러리의 info 까지 열지 않는다.

    httpx 는 INFO 로 요청 주소를 찍는데, 주소에 키를 싣는 API 가 있다 (NEIS 의 KEY=).
    """
    assert "secret-key" not in stderr
