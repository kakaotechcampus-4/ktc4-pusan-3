"""deploy/scripts/deploy.sh — 새 커밋을 올리고, 헬스가 안 뜨면 직전 이미지로 되돌린다.

docker · git · sleep 을 가짜로 바꿔 돈다. docker · git 은 받은 인자를 파일에 적는다
(docker 는 그때의 api · web 이미지 태그도).
가짜 docker 는 아래 환경변수가 있으면 그 명령을 실패시킨다.

    FAKE_UP_FAIL       up — 그때의 api 태그가 이 목록(공백 구분)에 있으면
    FAKE_RUN_FAIL      compose run (마이그레이션)
    FAKE_PULL_FAIL     compose pull
    FAKE_NO_PREV       image inspect (직전 이미지 없음)
    FAKE_NO_NET        network inspect (ktc4-edge 없음)
    FAKE_CADDY_INVALID docker run … caddy validate
    FAKE_RELOAD_FAIL   docker exec ktc4-caddy caddy reload
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
    "COMPOSE_PROFILES=\n"
    "APP_ENV=dev\n"
)
PROD_ENV = (
    "COMPOSE_PROJECT_NAME=\n"
    "STACK_SUFFIX=\n"
    "NEXT_PUBLIC_API_BASE_URL=https://api.icatch.ai.kr\n"
    "COMPOSE_PROFILES=edge\n"
    "APP_ENV=prod\n"
)

FAKE_DOCKER = r"""#!/bin/sh
echo "docker $* tag=${API_IMAGE_TAG:-} web=${WEB_IMAGE_TAG:-}" >> "$FAKE_LOG"
case "$*" in
  "run --rm -v"*) [ -z "$FAKE_CADDY_INVALID" ] ;;
  "exec ktc4-caddy"*) [ -z "$FAKE_RELOAD_FAIL" ] ;;
  "network inspect"*) [ -z "$FAKE_NO_NET" ] ;;
  "image inspect"*) [ -z "$FAKE_NO_PREV" ] ;;
  "image ls ktc4-dev-api"*) printf "old\nprev\nnew\n" ;;
  *" config --images"*) printf "redis:7-alpine\ncaddy:2.10.0-alpine\n" ;;
  *" pull "*) [ -z "$FAKE_PULL_FAIL" ] ;;
  *" up "*) case " $FAKE_UP_FAIL " in *" $API_IMAGE_TAG "*) exit 1 ;; esac ;;
  *" run "*) [ -z "$FAKE_RUN_FAIL" ] ;;
esac
"""


@pytest.fixture
def box(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "git").write_text('#!/bin/sh\necho "git $*" >> "$FAKE_LOG"\n')
    (bin_dir / "sleep").write_text("#!/bin/sh\n")  # reload 재시도가 15초를 쓰지 않게
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    work = tmp_path / "repo"
    (work / "deploy" / "docker").mkdir(parents=True)
    env_file = work / "deploy" / "docker" / ".env"
    env_file.write_text(DEV_ENV)
    tag_file = work / "deploy" / "docker" / ".deployed-tag"
    tag_file.write_text("prev\n")

    def run(target: str = "dev", **fail: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "DEPLOY_LOCK": str(tmp_path / "lock"),
            **{f"FAKE_{k.upper()}": v for k, v in fail.items()},
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


def _ups(calls: list[str]) -> list[tuple[str, str]]:
    """up 마다 (api 태그, web 태그)."""
    out = []
    for c in calls:
        if " up " in c:
            api, web = c.rsplit(" tag=", 1)[1].split(" web=")
            out.append((api, web))
    return out


def _index(calls: list[str], needle: str) -> int:
    return next(i for i, c in enumerate(calls) if needle in c)


def test_success_records_tag_and_keeps_only_current_and_previous(box):
    result = box()

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.tag_file.read_text().strip() == "new"
    calls = box.calls()
    assert "git reset --hard new" in calls
    assert _ups(calls) == [("new", "new")]
    assert any(" build --quiet api web " in c for c in calls)
    removed = [c.split()[3] for c in calls if c.startswith("docker image rm")]
    assert removed == ["ktc4-dev-api:old"]


def test_every_up_waits_for_health_and_never_builds_or_pulls(box):
    """--wait 가 빠지면 헬스를 안 보고 성공이라 자동 롤백이 꺼진다. 이미지가 없을 때 up 이
    지금 소스를 구워 그 태그를 붙이면, 롤백이 "실패한 코드 다시 올리기" 가 된다."""
    box(up_fail="new")

    ups = [c for c in box.calls() if " up " in c]
    assert len(ups) == 2
    for c in ups:
        assert " -d --no-build --pull never --wait --wait-timeout 240 " in c


def test_missing_non_built_images_are_pulled_before_build(box):
    """caddy · db · redis 태그가 바뀐 커밋 — up 은 --pull never 라
    여기서 받지 않으면 up 이 중간에 멈춘다."""
    box()

    calls = box.calls()
    pull = _index(calls, " pull ")
    assert "--ignore-buildable --policy missing" in calls[pull]
    assert pull < _index(calls, " build ")


def test_pull_failure_stops_before_touching_anything(box):
    result = box(pull_fail="1")

    assert result.returncode != 0
    calls = box.calls()
    assert not any(" build " in c or "alembic" in c for c in calls)
    assert _ups(calls) == []


def test_unhealthy_new_version_rolls_back_both_images_to_previous_tag(box):
    result = box(up_fail="new")

    assert result.returncode == 1
    assert _ups(box.calls()) == [("new", "new"), ("prev", "prev")]
    assert "롤백 성공" in result.stdout
    # 직전 태그를 그대로 둔다 — 다음 배포가 실패해도 같은 곳으로 돌아간다
    assert box.tag_file.read_text().strip() == "prev"


def test_failed_rollback_still_fails_the_deploy(box):
    result = box(up_fail="new prev")

    assert result.returncode == 1
    assert "롤백도 실패" in result.stdout
    assert box.tag_file.read_text().strip() == "prev"


def test_no_previous_image_does_not_attempt_rollback(box):
    """첫 배포(.deployed-tag 없음 → local) 처럼 직전 이미지가 없으면,
    되돌릴 곳이 없다고 말하고 끝낸다. api · web 둘 다 있어야 되돌린다."""
    box.tag_file.unlink()

    result = box(up_fail="new", no_prev="1")

    assert result.returncode == 1
    calls = box.calls()
    assert _ups(calls) == [("new", "new")]
    inspect = next(c for c in calls if c.startswith("docker image inspect"))
    assert "ktc4-dev-api:local" in inspect and "ktc4-dev-web:local" in inspect
    assert "롤백할 직전 이미지(local)가 없다" in result.stdout


def test_failed_migration_never_touches_running_containers(box):
    result = box(run_fail="1")

    assert result.returncode != 0
    assert _ups(box.calls()) == []
    assert box.tag_file.read_text().strip() == "prev"


def test_creates_edge_network_with_caddy_address_reserved(box):
    """.10 은 Caddy 자리다 — 다른 컨테이너는 .128/25 에서만 주소를 받아야 Caddy 행세를 못 한다."""
    box(no_net="1")

    create = [c for c in box.calls() if c.startswith("docker network create")]
    assert len(create) == 1
    assert "--subnet 172.30.0.0/24 --ip-range 172.30.0.128/25 ktc4-edge" in create[0]


@pytest.mark.parametrize(
    ("target", "env"),
    [
        # 🚨 프로젝트 이름이 빠지면 compose name(ktc4-deploy) = 운영 DB 로 떨어진다
        ("dev", DEV_ENV.replace("COMPOSE_PROJECT_NAME=ktc4-dev", "COMPOSE_PROJECT_NAME=")),
        (
            "dev",
            DEV_ENV.replace("https://api.dev.", "https://api."),
        ),  # prod .env 를 복사하고 안 고쳤다
        ("dev", DEV_ENV.replace("COMPOSE_PROFILES=", "COMPOSE_PROFILES=edge")),  # Caddy 이름 충돌
        ("dev", DEV_ENV.replace("APP_ENV=dev\n", "")),  # dev 알림이 [prod] 로 온다
        ("prod", PROD_ENV.replace("https://api.", "https://api.dev.")),  # prod 가 dev API 를 굽는다
        ("prod", PROD_ENV.replace("STACK_SUFFIX=", "STACK_SUFFIX=-dev")),
        # 첫 전환의 down 이 Caddy · 옛 네트워크를 남겨 운영이 내려간 채 멈춘다
        ("prod", PROD_ENV.replace("COMPOSE_PROFILES=edge", "COMPOSE_PROFILES=")),
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


def test_prod_validates_caddyfile_and_reloads_caddy(box):
    box.env_file.write_text(PROD_ENV)

    result = box(target="prod")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = box.calls()
    validate = _index(calls, "caddy validate")
    assert "caddy:2.10.0-alpine" in calls[validate]
    assert validate < _index(calls, " build ")
    assert any(c.startswith("docker exec ktc4-caddy caddy reload") for c in calls)


def test_dev_does_not_touch_caddy(box):
    box()

    assert not any("caddy" in c for c in box.calls())


def test_broken_caddyfile_stops_prod_before_anything_changes(box):
    """깨진 Caddyfile 은 다음 Caddy 재시작에 dev · 운영을 같이 내린다."""
    box.env_file.write_text(PROD_ENV)

    result = box(target="prod", caddy_invalid="1")

    assert result.returncode == 1
    assert "Caddyfile 이 깨졌다" in result.stdout
    calls = box.calls()
    assert not any(" build " in c or "alembic" in c for c in calls)
    assert _ups(calls) == []


def test_failed_caddy_reload_turns_the_deploy_red(box):
    """초록불로 끝내면 아무도 모른다. 새 버전은 떠 있으니 태그는 새 것으로 적는다."""
    box.env_file.write_text(PROD_ENV)

    result = box(target="prod", reload_fail="1")

    assert result.returncode == 1
    assert "Caddy reload 실패" in result.stdout
    assert box.tag_file.read_text().strip() == "new"
    assert sum(c.startswith("docker exec ktc4-caddy caddy reload") for c in box.calls()) == 5
