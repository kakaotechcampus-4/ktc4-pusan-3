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

access = logging.getLogger("uvicorn.access")
line = '%s - "%s %s HTTP/%s" %d'  # uvicorn 이 접근 로그를 찍는 모양 그대로
access.info(line, "127.0.0.1:5000", "GET",
            "/api/v1/auth/kakao/callback?code=SECRET-AUTH-CODE&state=SECRET-STATE", "1.1", 302)
access.info(line, "127.0.0.1:5000", "GET",
            "/api/v1/auth/kakao?client=web&bind=SECRET-BIND", "1.1", 302)
access.info(line, "127.0.0.1:5000", "POST", "/api/v1/invites/SECRETCD/accept", "1.1", 200)
access.info(line, "127.0.0.1:5000", "GET", "/api/v1/invites/SECRETPV", "1.1", 200)
access.info(line, "127.0.0.1:5000", "GET", "/api/v1/Invites/SECRETUP", "1.1", 404)
access.info(line, "127.0.0.1:5000", "GET", "/api/v1/invites//SECRETDS", "1.1", 404)
# uvicorn 이 값 모양(개수 · 순서)을 바꿔도 가려야 한다 — 모양이 다르면 그냥 통과시키지 않는다
access.info("%s %s", "GET", "/api/v1/auth/kakao?bind=SECRET-SHAPE")
access.info(line, "127.0.0.1:5000", "POST",
            "/api/v1/children/0b6f5c1e-0000-4000-8000-000000000001/inputs", "1.1", 202)
"""
"""표시 글자는 영문이다 — 파이프 인코딩이 UTF-8 이 아닌 환경에서 엉뚱한 이유로 깨지지 않게."""


@pytest.fixture(scope="module")
def output() -> str:
    """서버를 띄울 때와 같은 순서로 불러온 뒤 로그를 남기고, 찍힌 것을 돌려준다.

    stdout 과 stderr 를 합친다 — uvicorn 기본 설정은 접근 로그를 stdout 으로, 나머지를 stderr 로
    보낸다. 한쪽만 보면 접근 로그의 "없다" 단언이 빈 화면을 보고 통과한다.
    """
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # 실패하면 새 프로세스의 오류 내용을 그대로 보여 준다 (check=True 는 그걸 버린다).
    assert result.returncode == 0, result.stderr
    return result.stdout + result.stderr


def test_app_info_log_is_shown_with_time_level_and_name(output):
    """info 가 버려지지 않고, 언제(시간대까지) · 어느 레벨 · 어느 모듈인지가 한 줄에 찍힌다.

    시간대를 붙이는 이유 — 개발 맥은 한국 시간, 컨테이너는 보통 UTC 라 말없이 9시간 어긋난다.
    """
    lines = [line for line in output.splitlines() if "app-probe-line" in line]

    assert len(lines) == 1
    assert "INFO" in lines[0]
    assert "app.probe" in lines[0]
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}", lines[0])


def test_uvicorn_logs_survive_app_logging_setup(output):
    """🚨 앱 로그 설정이 uvicorn 의 로거를 끄지 않는다.

    끄면 서버의 시작 줄 · 요청 줄 · "Exception in ASGI application" 오류가 말없이 사라진다.
    dictConfig 의 disable_existing_loggers 기본값이 바로 그렇게 한다.
    """
    assert "uvicorn-probe-line" in output


def test_library_info_log_stays_hidden(output):
    """🚨 루트 로거는 WARNING 그대로다 — 라이브러리의 info 까지 열지 않는다.

    httpx 는 INFO 로 요청 주소를 찍는데, 주소에 키를 싣는 API 가 있다
    (공공데이터포털의 serviceKey= · 식품안전나라는 경로에 키).
    """
    assert "secret-key" not in output
    # Discord 웹훅은 토큰이 주소에 있다 — 루트가 INFO 로 열려도 httpx 는 WARNING 에 못 박혀
    # 있어야 한다
    assert "SECRET-WEBHOOK-TOKEN" not in output


def test_access_log_hides_query_strings_and_invite_codes(output):
    """🚨 uvicorn 접근 로그는 주소를 쿼리째 찍는다 — 인가 코드 · state · bind 원문이 쿼리에 있다.

    초대 코드는 경로에 있다 (/invites/{code}). 둘 다 가리고 경로 · 상태 코드는 남긴다
    (auth-kakao-v1 §7-5 "콜백 URL 전체를 로깅하지 않는다", #214 리뷰).
    """
    secrets = (
        "SECRET-AUTH-CODE",
        "SECRET-STATE",
        "SECRET-BIND",
        "SECRETCD",
        "SECRETPV",
        "SECRETUP",
        "SECRETDS",
        "SECRET-SHAPE",
    )
    for secret in secrets:
        assert secret not in output
    assert "/api/v1/auth/kakao/callback?[redacted]" in output
    assert "/api/v1/invites/[redacted]/accept" in output
    assert "/api/v1/invites/[redacted] HTTP" in output  # 수락 전 확인 — 코드가 경로 끝


def test_access_log_keeps_ids_in_paths(output):
    """아이 id · run id 는 남긴다 — 원문이 아니라 id 다 (루트 CLAUDE.md §2)."""
    assert "/api/v1/children/0b6f5c1e-0000-4000-8000-000000000001/inputs" in output
