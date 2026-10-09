"""deploy/scripts/health-alert.sh — 컨테이너 상태가 달라졌을 때만 Discord 로 한 줄 (멘토 #267 2번).

api 안의 알림은 api 가 살아 있을 때만 말할 수 있다. 컨테이너가 못 뜨거나(배포 실패) redis 가 죽은 건
밖에서 봐야 한다 — 서버의 cron 이 1분마다 이 스크립트를 돌린다.

docker 와 curl 을 가짜로 바꿔 돈다 — 가짜 docker 는 환경변수에 적힌 컨테이너 목록 · 상태를 돌려주고,
가짜 curl 은 받은 인자를 파일에 적고 정해진 상태 코드를 찍는다. 그래서 docker 도 Discord 도 없이
돈다.
"""

import os
import subprocess
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
        '  compose) printf "%s\\n" $FAKE_IDS ;;\n'
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
        inspect: str, *args: str, ids: str = "id1 id2", curl_code: str = "200"
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_IDS": ids,
            "FAKE_INSPECT": inspect,
            "FAKE_CURL_LOG": str(tmp_path / "curl.log"),
            "FAKE_CURL_CODE": curl_code,
            "STATE_FILE": str(tmp_path / "state"),
            "ENV_FILE": str(env_file),
            "ALERT_ENV": "prod",
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args, str(tmp_path / "compose.yml")],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    run.curl_log = tmp_path / "curl.log"  # type: ignore[attr-defined]
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


def test_dry_run_never_calls_curl(box):
    box(HEALTHY, "--dry-run")

    assert not box.curl_log.exists()
