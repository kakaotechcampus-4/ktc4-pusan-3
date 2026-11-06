"""deploy/scripts/deploy.sh — 새 커밋을 올리고, 헬스가 안 뜨면 직전 이미지로 되돌린다.

docker 와 git 을 가짜로 바꿔 돈다. 둘 다 받은 인자를 파일에 적는다(docker 는 그때의 이미지 태그도).
가짜 docker 의 `up` 은 태그가 FAKE_UP_FAIL 과 같으면, `run`(마이그레이션)은 FAKE_RUN_FAIL 이 있으면,
`image inspect` 는 FAKE_NO_PREV 가 있으면 실패한다.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[5] / "deploy" / "scripts" / "deploy.sh"

DEV_ENV = (
    "COMPOSE_PROJECT_NAME=ktc4-dev\n"
    "STACK_SUFFIX=-dev\n"
    "NEXT_PUBLIC_API_BASE_URL=https://api.dev.icatch.ai.kr\n"
)
PROD_ENV = (
    "COMPOSE_PROJECT_NAME=\nSTACK_SUFFIX=\nNEXT_PUBLIC_API_BASE_URL=https://api.icatch.ai.kr\n"
)


@pytest.fixture
def box(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        'echo "docker $* tag=${API_IMAGE_TAG:-}" >> "$FAKE_LOG"\n'
        'case "$*" in\n'
        '  *" up "*) [ "$API_IMAGE_TAG" != "$FAKE_UP_FAIL" ] ;;\n'
        '  *" run "*) [ -z "$FAKE_RUN_FAIL" ] ;;\n'
        '  "image inspect"*) [ -z "$FAKE_NO_PREV" ] ;;\n'
        '  "image ls ktc4-dev-api"*) printf "old\\nprev\\nnew\\n" ;;\n'
        "esac\n"
    )
    (bin_dir / "git").write_text('#!/bin/sh\necho "git $*" >> "$FAKE_LOG"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    work = tmp_path / "repo"
    (work / "deploy" / "docker").mkdir(parents=True)
    env_file = work / "deploy" / "docker" / ".env"
    env_file.write_text(DEV_ENV)
    tag_file = work / "deploy" / "docker" / ".deployed-tag"
    tag_file.write_text("prev\n")

    def run(
        target: str = "dev", up_fail: str = "", run_fail: str = "", no_prev: str = ""
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "FAKE_UP_FAIL": up_fail,
            "FAKE_RUN_FAIL": run_fail,
            "FAKE_NO_PREV": no_prev,
            "DEPLOY_LOCK": str(tmp_path / "lock"),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), "new", target],
            cwd=work,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    run.tag_file = tag_file  # type: ignore[attr-defined]
    run.env_file = env_file  # type: ignore[attr-defined]
    run.calls = lambda: log.read_text().splitlines() if log.exists() else []  # type: ignore[attr-defined]
    return run


def _ups(calls: list[str]) -> list[str]:
    return [c.rsplit("tag=", 1)[1] for c in calls if " up " in c]


def test_success_records_tag_and_keeps_only_current_and_previous(box):
    result = box()

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.tag_file.read_text().strip() == "new"
    calls = box.calls()
    assert "git reset --hard new" in calls
    assert _ups(calls) == ["new"]
    removed = [c.split()[3] for c in calls if c.startswith("docker image rm")]
    assert removed == ["ktc4-dev-api:old"]


def test_up_never_builds(box):
    """이미지가 없을 때 up 이 지금 소스를 구워 그 태그를 붙이면,
    롤백이 "실패한 코드 다시 올리기" 가 된다."""
    box(up_fail="new")

    ups = [c for c in box.calls() if " up " in c]
    assert ups and all("--no-build" in c and "--pull never" in c for c in ups)


def test_unhealthy_new_version_rolls_back_to_previous_tag(box):
    result = box(up_fail="new")

    assert result.returncode == 1
    assert _ups(box.calls()) == ["new", "prev"]
    assert "롤백 성공" in result.stdout
    # 직전 태그를 그대로 둔다 — 다음 배포가 실패해도 같은 곳으로 돌아간다
    assert box.tag_file.read_text().strip() == "prev"


def test_no_previous_image_does_not_attempt_rollback(box):
    """첫 배포(.deployed-tag 없음 → local) 처럼 직전 이미지가 없으면,
    되돌릴 곳이 없다고 말하고 끝낸다."""
    box.tag_file.unlink()

    result = box(up_fail="new", no_prev="1")

    assert result.returncode == 1
    assert _ups(box.calls()) == ["new"]
    assert "롤백할 직전 이미지(local)가 없다" in result.stdout


def test_failed_migration_never_touches_running_containers(box):
    result = box(run_fail="1")

    assert result.returncode != 0
    assert _ups(box.calls()) == []
    assert box.tag_file.read_text().strip() == "prev"


@pytest.mark.parametrize(
    ("target", "env"),
    [
        # 🚨 프로젝트 이름이 빠지면 compose name(ktc4-deploy) = 운영 DB 로 떨어진다
        ("dev", DEV_ENV.replace("COMPOSE_PROJECT_NAME=ktc4-dev", "COMPOSE_PROJECT_NAME=")),
        (
            "dev",
            DEV_ENV.replace("https://api.dev.", "https://api."),
        ),  # prod .env 를 복사하고 안 고쳤다
        ("prod", PROD_ENV.replace("https://api.", "https://api.dev.")),  # prod 가 dev API 를 굽는다
        ("prod", DEV_ENV),  # DEPLOY_DIR 이 dev checkout 을 가리킨다
        ("staging", DEV_ENV),
    ],
)
def test_env_file_must_match_target_before_touching_anything(box, target, env):
    box.env_file.write_text(env)

    result = box(target=target)

    assert result.returncode == 1
    assert "맞지 않는다" in result.stdout
    # git 도 docker 도 부르지 않는다
    assert box.calls() == []


def test_prod_env_passes_and_reloads_caddy(box):
    box.env_file.write_text(PROD_ENV)

    result = box(target="prod")

    assert result.returncode == 0, result.stdout + result.stderr
    assert any(c.startswith("docker exec ktc4-caddy caddy reload") for c in box.calls())
