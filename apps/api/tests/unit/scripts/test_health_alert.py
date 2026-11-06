"""deploy/scripts/health-alert.sh — 컨테이너 상태가 달라졌을 때만 Discord 로 한 줄 (멘토 #267 2번).

api 안의 알림은 api 가 살아 있을 때만 말할 수 있다. 컨테이너가 못 뜨거나(배포 실패) redis 가 죽은 건
밖에서 봐야 한다 — 서버의 cron 이 1분마다 이 스크립트를 돌린다.

docker 와 curl 을 가짜로 바꿔 돈다 — 가짜 docker 는 환경변수에 적힌 컨테이너 목록 · 상태를 돌려주고,
가짜 curl 은 받은 인자를 파일에 적고 정해진 상태 코드를 찍는다. 그래서 docker 도 Discord 도 없이
돈다.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[5] / "deploy" / "scripts" / "health-alert.sh"

HEALTHY = "/ktc4-redis-deploy running healthy\\n/ktc4-web running healthy\\n"
WEB_DOWN = "/ktc4-redis-deploy running healthy\\n/ktc4-web exited -\\n"


@pytest.fixture
def box(tmp_path: Path):
    """가짜 docker · curl 이 든 PATH, 상태 파일, 웹훅 주소가 든 env 파일."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  compose) case " $* " in\n'
        '    *" exec "*) [ -n "$FAKE_READY" ] && printf "%s\\n" "$FAKE_READY" || exit 1 ;;\n'
        '    *) printf "%s\\n" $FAKE_IDS ;;\n'
        "  esac ;;\n"
        '  inspect) printf "%b" "$FAKE_INSPECT" ;;\n'
        "esac\n"
    )
    (bin_dir / "curl").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" >> "$FAKE_CURL_LOG"\nprintf "%s" "${FAKE_CURL_CODE:-200}"\n'
    )
    for f in (bin_dir / "docker", bin_dir / "curl"):
        f.chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WEB_PORT=3000\nALERT_WEBHOOK_URL=https://discord.com/api/webhooks/1/SECRET-TOKEN\n"
    )

    def run(
        inspect: str,
        *args: str,
        ids: str = "id1 id2",
        curl_code: str = "200",
        alert_env: str | None = "prod",
        ready: str = "",
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_IDS": ids,
            "FAKE_INSPECT": inspect,
            "FAKE_CURL_LOG": str(tmp_path / "curl.log"),
            "FAKE_CURL_CODE": curl_code,
            "FAKE_READY": ready,  # 비우면 api 컨테이너 exec 가 실패한다(unreachable)
            "STATE_FILE": str(tmp_path / "state"),
            "ENV_FILE": str(env_file),
        }
        env.pop("ALERT_ENV", None)
        env.pop("APP_ENV", None)
        if alert_env is not None:
            env["ALERT_ENV"] = alert_env
        return subprocess.run(
            ["bash", str(SCRIPT), *args, str(tmp_path / "compose.yml")],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    run.curl_log = tmp_path / "curl.log"  # type: ignore[attr-defined]
    run.env_file = env_file  # type: ignore[attr-defined]
    run.state = tmp_path / "state"  # type: ignore[attr-defined]
    return run


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_first_run_reports_every_container_as_new(box):
    """처음 돌면 모든 컨테이너가 "없음 → 지금 상태" 로 한 줄씩 — 감시가 시작됐다는 확인이기도
    하다.
    """
    lines = _lines(box(HEALTHY, "--dry-run"))

    assert lines == [
        "🟢 [prod] ktc4-redis-deploy: 없음 → running healthy",
        "🟢 [prod] ktc4-web: 없음 → running healthy",
    ]


def test_same_state_is_silent(box):
    """달라진 게 없으면 아무것도 보내지 않는다 — 1분마다 도는데 매번 울리면 아무도 안 본다."""
    box(HEALTHY, "--dry-run")
    lines = _lines(box(HEALTHY, "--dry-run"))

    assert lines == []


def test_only_the_changed_container_is_reported(box):
    """web 이 죽으면 web 한 줄만 — 멀쩡한 redis 는 말하지 않는다."""
    box(HEALTHY, "--dry-run")
    lines = _lines(box(WEB_DOWN, "--dry-run"))

    assert lines == ["🔴 [prod] ktc4-web: running healthy → exited -"]


def test_recovery_is_reported_too(box):
    """복구도 한 줄 — 그래야 Discord 만 보고도 지금 상태를 안다."""
    box(HEALTHY, "--dry-run")
    box(WEB_DOWN, "--dry-run")
    lines = _lines(box(HEALTHY, "--dry-run"))

    assert lines == ["🟢 [prod] ktc4-web: exited - → running healthy"]


def test_vanished_container_is_reported(box):
    """compose 목록에서 사라지면(지워짐) 그것도 한 줄."""
    box(HEALTHY, "--dry-run")
    lines = _lines(box("/ktc4-redis-deploy running healthy\\n", "--dry-run", ids="id1"))

    assert lines == ["🔴 [prod] ktc4-web: running healthy → 사라짐"]


def test_posts_one_message_to_the_webhook_and_saves_state(box):
    """진짜 모드 — 바뀐 줄들을 한 메시지로 웹훅에 POST 하고, 성공했을 때 상태를 저장한다."""
    result = box(HEALTHY)

    assert result.returncode == 0, result.stderr
    args = box.curl_log.read_text()
    assert "https://discord.com/api/webhooks/1/SECRET-TOKEN?wait=true" in args
    assert '"allowed_mentions":{"parse":[]}' in args
    assert '"username":"infra-alert"' in args  # 채널 하나에서 api-alert · browser-alert 와 구별
    assert "ktc4-web: 없음 → running healthy" in args
    assert "docker compose" in args and "logs --tail" in args  # 다음에 칠 명령 한 줄
    assert box.state.exists()


def test_failed_post_keeps_the_old_state_so_it_retries_next_minute(box):
    """🚨 웹훅이 실패하면 상태를 저장하지 않는다 — 다음 분에 같은 변화를 다시 보낸다. 조용히 잃지
    않는다.
    """
    result = box(HEALTHY, curl_code="500")

    assert result.returncode != 0
    assert not box.state.exists()


def test_env_name_comes_from_app_env_in_the_env_file(box):
    """꼬리표는 env 파일의 APP_ENV 에서 — 시험 서버는 그 파일에 APP_ENV=dev 한 줄만 다르게 쓴다.

    compose 가 web 에 넘기는 값과 같은 줄이라 web 알림 · 감시 알림의 꼬리표가 같이 움직인다.
    """
    box.env_file.write_text(box.env_file.read_text() + "APP_ENV=dev\n")
    lines = _lines(box(HEALTHY, "--dry-run", alert_env=None))

    assert lines[0].startswith("🟢 [dev] ")


def test_env_name_defaults_to_prod_without_app_env(box):
    lines = _lines(box(HEALTHY, "--dry-run", alert_env=None))

    assert lines[0].startswith("🟢 [prod] ")


def test_dry_run_never_calls_curl(box):
    box(HEALTHY, "--dry-run")

    assert not box.curl_log.exists()


# ---------------------------------------------------------------------------
# api 의존성 (#671) — docker healthcheck 는 /health(프로세스)만 봐서 DB · Redis 장애를 모른다
# ---------------------------------------------------------------------------

WITH_API = "/ktc4-api running healthy\\n/ktc4-web running healthy\\n"


def test_api_가_있으면_ready_한_줄을_같이_본다(box):
    lines = _lines(box(WITH_API, "--dry-run", ready="ready"))

    assert lines == [
        "🟢 [prod] ktc4-api: 없음 → running healthy",
        "🟢 [prod] ktc4-api-ready: 없음 → ready",
        "🟢 [prod] ktc4-web: 없음 → running healthy",
    ]


def test_api_는_살아_있는데_Redis_가_죽으면_그것만_알린다(box):
    box(WITH_API, "--dry-run", ready="ready")
    lines = _lines(box(WITH_API, "--dry-run", ready="not-ready:redis"))

    assert lines == ["🔴 [prod] ktc4-api-ready: ready → not-ready:redis"]


def test_api_에_닿지_못하면_unreachable(box):
    box(WITH_API, "--dry-run", ready="ready")
    lines = _lines(box(WITH_API, "--dry-run"))

    assert lines == ["🔴 [prod] ktc4-api-ready: ready → unreachable"]


def test_unhealthy_는_빨강이다(box):
    """*healthy 패턴이 unhealthy 끝에도 맞아 죽은 컨테이너를 🟢 로 알리던 버그 (#671 에서 발견)."""
    box(HEALTHY, "--dry-run")
    lines = _lines(
        box("/ktc4-redis-deploy running healthy\\n/ktc4-web running unhealthy\\n", "--dry-run")
    )

    assert lines == ["🔴 [prod] ktc4-web: running healthy → running unhealthy"]


# ---------------------------------------------------------------------------
# api 컨테이너 안에서 도는 python 한 토막 — 가짜 docker 는 이걸 실행하지 않으므로 따로 돌린다
# ---------------------------------------------------------------------------


def _ready_probe() -> str:
    """스크립트에 박힌 python 을 꺼낸다. 스크립트가 바뀌면 여기서 바로 깨진다."""
    text = SCRIPT.read_text()
    start = text.index("python -c '") + len("python -c '")
    return text[start : text.index("' 2>/dev/null", start)]


@pytest.fixture
def ready_server():
    """/health/ready 흉내 — 정해 둔 상태 코드와 본문을 돌려준다."""
    import http.server
    import json
    import threading

    answer: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(answer["body"]).encode()
            self.send_response(answer["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def probe(status: int, checks: dict) -> str:
        answer.update(status=status, body={"status": "x", "checks": checks})
        code = _ready_probe().replace(
            "http://127.0.0.1:8000", f"http://127.0.0.1:{server.server_port}"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    yield probe
    server.shutdown()


def test_ready_probe_는_200_이면_ready(ready_server):
    assert ready_server(200, {"db": "ok", "redis": "ok"}) == "ready"


def test_ready_probe_는_503_이면_실패한_구성요소만_붙인다(ready_server):
    assert ready_server(503, {"db": "ok", "redis": "fail"}) == "not-ready:redis"
    assert ready_server(503, {"db": "fail", "redis": "fail"}) == "not-ready:db,redis"


def test_ready_probe_는_응답이_없으면_상한에서_끊는다():
    """api 가 멈춰 /health/ready 가 답하지 않아도 cron 이 매달리지 않고 unreachable 로 알린다 (#671 리뷰).

    상한을 짧게 바꿔 돌린다 — 상한이 빠지면 바꿀 것이 없어 끝까지 기다리다 여기서 깨진다.
    """
    import socket

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()  # 연결은 받고 답하지 않는다
    probe = _ready_probe()
    code = probe.replace("timeout=5", "timeout=0.3").replace(
        "http://127.0.0.1:8000", f"http://127.0.0.1:{server.getsockname()[1]}"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=3
        )
    finally:
        server.close()
    assert result.returncode != 0  # 스크립트는 `|| echo unreachable` 로 받는다
