"""앱 로그 설정 — app.* 로그가 시간 · 레벨 · 이름과 함께 보인다 (#197 후속)

uvicorn 은 자기 로거(uvicorn*)만 설정한다. 앱이 따로 설정하지 않으면 app.* 로그는 파이썬
기본 처리로 떨어져 WARNING 이상만, 시간도 이름도 없이 찍힌다 — info 는 전부 버려진다.

새 파이썬 프로세스에서 확인한다. pytest 는 자기 로그 수집기를 루트에 달아 두므로 같은
프로세스 안에서는 "설정이 없으면 버려진다" 를 볼 수 없다.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[3]
"""apps/api — 새 프로세스가 여기서 `import app.main` 을 한다."""

PROBE = """
import app.main
import logging
logging.getLogger("app.probe").info("앱 로그가 보이는가")
logging.getLogger("httpx").info("https://open.example.test/hub?KEY=secret-key")
"""


@pytest.fixture(scope="module")
def stderr() -> str:
    """서버를 띄울 때처럼 app.main 을 불러온 뒤 로그를 남기고, 찍힌 것을 돌려준다."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return result.stderr


def test_app_info_log_is_shown_with_time_level_and_name(stderr):
    """info 가 버려지지 않고, 언제 · 어느 레벨 · 어느 모듈인지가 한 줄에 같이 찍힌다."""
    lines = [line for line in stderr.splitlines() if "앱 로그가 보이는가" in line]

    assert len(lines) == 1
    assert "INFO" in lines[0]
    assert "app.probe" in lines[0]
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", lines[0])


def test_library_info_log_stays_hidden(stderr):
    """🚨 루트 로거는 WARNING 그대로다 — 라이브러리의 info 까지 열지 않는다.

    httpx 는 INFO 로 요청 주소를 찍는데, 주소에 키를 싣는 API 가 있다 (NEIS 의 KEY=).
    """
    assert "secret-key" not in stderr
