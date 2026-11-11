"""deploy/scripts/deploy.sh — 새 커밋을 올리고, 헬스가 안 뜨면 직전 이미지로 되돌린다.

docker · git · sleep 을 가짜로 바꿔 돈다. docker · git 은 받은 인자를 파일에 적는다
(docker 는 그때의 api · web 이미지 태그도).
가짜 docker 는 아래 환경변수가 있으면 그 명령을 실패시킨다.

    FAKE_UP_FAIL       up — 그때의 api 태그가 이 목록(공백 구분)에 있으면
    FAKE_RUN_FAIL      compose run (마이그레이션)
    FAKE_PULL_FAIL     compose pull
    FAKE_NO_PREV       image inspect (직전 이미지 없음 · make 의 배포 이미지 없음)
    FAKE_NO_NET        network inspect (ktc4-edge 없음)
    FAKE_CADDY_INVALID docker run … caddy validate
    FAKE_RELOAD_FAIL   docker exec ktc4-caddy caddy reload
    FAKE_BACKUP_FAIL   배포 전 백업(가짜 deploy/scripts/backup.sh)
    FAKE_LOGS_FAIL     compose logs (api 로그 읽기)
    FAKE_CRON_FAIL     worker cron 등록(가짜 deploy/scripts/worker-cron.sh)

`compose logs` 는 FAKE_LOGS 를 그대로 찍는다 (api 컨테이너가 뜰 때 남긴 로그 자리).

`compose config --format json` 은 FAKE_CONFIG 를 그대로 찍는다
(compose 가 .env 를 해석한 결과 자리).
`docker run … caddy validate` 는 물려받은 basic_auth 환경변수도 적는다.

가짜 git 의 `merge-base --is-ancestor` 는 FAKE_OLD 가 있을 때만 참이다
(새 sha 가 지금 떠 있는 것의 조상).
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "deploy" / "scripts" / "deploy.sh"
COMPOSE_FILE = ROOT / "deploy" / "docker" / "docker-compose.deploy.yml"

DEV_ENV = (
    "COMPOSE_PROJECT_NAME=ktc4-dev\n"
    "STACK_SUFFIX=-dev\n"
    "NEXT_PUBLIC_API_BASE_URL=https://api.dev.icatch.ai.kr\n"
    "COMPOSE_PROFILES=\n"
    "APP_ENV=dev\n"
    "API_MEM_LIMIT=768m\n"
    "WEB_MEM_LIMIT=384m\n"
    "DB_MEM_LIMIT=512m\n"
    "REDIS_MEM_LIMIT=128m\n"
    "API_EDGE_IP=172.30.0.21\n"
)
# 가짜 bcrypt 해시 (cost 10 · 53자).
# basic_auth 값은 .env 줄이 아니라 compose 가 해석한 값(FAKE_CONFIG)으로 본다
AUTH_HASH = "$2y$10$" + ("fake" * 14)[:53]


def compose_config(user: str = "team", pw_hash: str = AUTH_HASH, caddy: bool = True) -> str:
    """compose config --format json 의 모양. 실제 출력처럼 $ 를 $$ 로 적는다."""
    env = {"DEV_BASIC_AUTH_USER": user, "DEV_BASIC_AUTH_HASH": pw_hash.replace("$", "$$")}
    return json.dumps({"services": {"caddy": {"environment": env}} if caddy else {"api": {}}})


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
  "run --rm -e"*)
    echo "caddy-env $DEV_BASIC_AUTH_USER $DEV_BASIC_AUTH_HASH" >> "$FAKE_LOG"
    [ -z "$FAKE_CADDY_INVALID" ] ;;
  "exec ktc4-caddy"*) [ -z "$FAKE_RELOAD_FAIL" ] ;;
  "network inspect"*) [ -z "$FAKE_NO_NET" ] ;;
  "image inspect"*) [ -z "$FAKE_NO_PREV" ] ;;
  "image ls ktc4-dev-api"*) printf "old\nprev\nnew\n" ;;
  *" config --format json"*) printf '%s' "$FAKE_CONFIG" ;;
  *" config --images"*)
    printf "redis:7-alpine\ncaddy:2.10.0-alpine\nktc4-dev-api:%s\n" "$API_IMAGE_TAG" ;;
  *" pull "*) [ -z "$FAKE_PULL_FAIL" ] ;;
  *" logs "*) [ -z "$FAKE_LOGS_FAIL" ] || exit 1; printf '%s\n' "$FAKE_LOGS" ;;
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
    (bin_dir / "git").write_text(
        '#!/bin/sh\necho "git $*" >> "$FAKE_LOG"\n'
        'case "$*" in "merge-base"*) [ -n "$FAKE_OLD" ] ;; esac\n'
    )
    (bin_dir / "sleep").write_text("#!/bin/sh\n")  # reload 재시도가 15초를 쓰지 않게
    # make deploy-up 은 Linux 에서 sudo 로 IMDS 방화벽을 건다
    # — CI(Linux) · 맥 어디서든 같은 길을 가짜로 돈다
    (bin_dir / "uname").write_text("#!/bin/sh\necho Linux\n")
    (bin_dir / "sudo").write_text('#!/bin/sh\necho "sudo $*" >> "$FAKE_LOG"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    work = tmp_path / "repo"
    (work / "deploy" / "docker").mkdir(parents=True)
    # 배포 전 백업은 backup.sh 의 시험(test_backup.py)이 본다. 여기서는 불리는 자리와 실패만
    (work / "deploy" / "scripts").mkdir(parents=True)
    (work / "deploy" / "scripts" / "backup.sh").write_text(
        'echo "backup $*" >> "$FAKE_LOG"\n[ -z "$FAKE_BACKUP_FAIL" ]\n'
    )
    # worker cron 등록은 test_worker_cron.py 가 본다. 여기서는 불리는 자리와 실패만
    (work / "deploy" / "scripts" / "worker-cron.sh").write_text(
        'echo "worker-cron" >> "$FAKE_LOG"\n[ -z "$FAKE_CRON_FAIL" ]\n'
    )
    env_file = work / "deploy" / "docker" / ".env"
    env_file.write_text(DEV_ENV)
    tag_file = work / "deploy" / "docker" / ".deployed-tag"
    tag_file.write_text("prev\n")

    def fake_env(**fail: str) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "FAKE_CONFIG": compose_config(),
            **{f"FAKE_{k.upper()}": v for k, v in fail.items()},
        }

    def run(target: str = "dev", allow_old: str = "", **fail: str) -> subprocess.CompletedProcess:
        env = {**fake_env(**fail), "ALLOW_OLD": allow_old, "DEPLOY_LOCK": str(tmp_path / "lock")}
        return subprocess.run(
            ["bash", str(SCRIPT), "new", target],
            cwd=work,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def make(*targets: str, **fail: str) -> subprocess.CompletedProcess:
        """이 저장소의 Makefile 을 가짜 docker 로 돈다. 잠금(flock)은 끈다."""
        (work / "deploy" / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy(SCRIPT, work / "deploy" / "scripts" / "deploy.sh")
        shutil.copy(ROOT / "Makefile", work / "Makefile")
        return subprocess.run(
            ["make", "DEPLOY_LOCK=", *targets],
            cwd=work,
            capture_output=True,
            text=True,
            env=fake_env(**fail),
            timeout=30,
        )

    run.fake_env = fake_env  # type: ignore[attr-defined]
    run.make = make  # type: ignore[attr-defined]
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


API_LOGS = (
    "ktc4-dev-api  | 2026-11-10 15:00:00+0900 INFO app.api.cors CORS 를 켠다\n"
    "ktc4-dev-api  | 2026-11-10 15:00:01+0900 WARNING app.api.startup_report "
    "꺼져 있는 기능 1개 — 사진 받기 — 올리면 503 (PHOTO_UPLOAD_ACCEPT=false)\n"
    "ktc4-dev-api  | 2026-11-10 15:00:02+0900 INFO uvicorn.error Application startup complete."
)


def test_success_shows_which_features_are_off(box):
    """api 가 뜰 때 남긴 "꺼져 있는 기능" 한 줄을 배포 출력에도 보인다 (#912 리뷰).

    서버 로그(journald)에만 있으면 배포한 사람이 못 본다. 배포 출력은 Actions 화면에 그대로 뜬다.
    """
    result = box(logs=API_LOGS)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "꺼져 있는 기능 1개 — 사진 받기" in result.stdout
    # 그 줄만 — 다른 로그 줄까지 쏟지 않는다
    assert "Application startup complete" not in result.stdout
    assert any(" logs " in c and c.split(" tag=")[0].endswith(" api") for c in box.calls())


@pytest.mark.parametrize("fail", [{"logs_fail": "1"}, {"logs": "아무 줄도 없다"}, {}])
def test_unreadable_api_logs_never_fail_the_deploy(box, fail):
    """로그를 못 읽거나 그 줄이 없어도 배포는 성공이다 — 보여 주는 것일 뿐이다."""
    result = box(**fail)

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.tag_file.read_text().strip() == "new"


def test_rolled_back_deploy_does_not_read_api_logs(box):
    # 떠 있는 것은 직전 버전이다 — 새 버전의 줄인 것처럼 보이면 안 된다
    result = box(up_fail="new", logs=API_LOGS)

    assert result.returncode == 1
    assert "꺼져 있는 기능" not in result.stdout
    assert not any(" logs " in c for c in box.calls())


def test_every_up_waits_for_health_and_never_builds_or_pulls(box):
    """--wait 가 빠지면 헬스를 안 보고 성공이라 자동 롤백이 꺼진다. 이미지가 없을 때 up 이
    지금 소스를 구워 그 태그를 붙이면, 롤백이 "실패한 코드 다시 올리기" 가 된다."""
    box(up_fail="new")

    ups = [c for c in box.calls() if " up " in c]
    assert len(ups) == 2
    for c in ups:
        assert " -d --no-build --pull never --wait --wait-timeout 360 " in c


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


def test_predeploy_backup_runs_after_build_and_right_before_migration(box):
    """덤프와 마이그레이션 사이에 쓰기가 덜 끼게 — 빌드(~10분)를 기다린 뒤 뜬다."""
    box()

    calls = box.calls()
    backup = _index(calls, "backup predeploy new")
    assert _index(calls, " build ") < backup < _index(calls, " run --rm api-task")


def test_failed_predeploy_backup_stops_before_migration(box):
    """마이그레이션을 되돌릴 곳이 이 백업뿐이다 — 없으면 DB 도 컨테이너도 건드리지 않는다."""
    result = box(backup_fail="1")

    assert result.returncode != 0
    calls = box.calls()
    assert not any(" run " in c or " up " in c for c in calls)
    assert box.tag_file.read_text().strip() == "prev"


def test_only_a_manual_deploy_can_skip_the_backup():
    """skip_backup 이 deploy.yml 에서 빠지면 아무 테스트도 실패하지 않고 건너뛰기만 조용히 사라진다.
    push 배포에는 inputs 가 없어 늘 빈 값 — 늘 백업한다 (#758)."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy.yml").read_text())
    trigger = workflow[True]  # YAML 1.1 은 `on:` 을 참으로 읽는다
    skip = trigger["workflow_dispatch"]["inputs"]["skip_backup"]
    job = workflow["jobs"]["deploy"]
    ssm = next(step["run"] for step in job["steps"] if step.get("id") == "ssm")

    assert (skip["type"], skip["default"]) == ("boolean", False)
    assert job["env"]["SKIP_BACKUP"] == "${{ inputs.skip_backup && '1' || '' }}"
    assert '--arg skip "$SKIP_BACKUP"' in ssm
    assert "SKIP_PREDEPLOY_BACKUP=\\($skip) bash $F" in ssm


def test_lock_file_made_by_another_user_is_still_locked(box, tmp_path):
    """재부팅 뒤 복원 점검 cron(ubuntu)이 잠금 파일을 먼저 만들면, root 도 그 파일을
    쓰기 · O_CREAT 로는 못 연다(/var/lock 은 sticky · fs.protected_regular=2).
    읽기로 열어야 배포가 잠금을 잡는다 (#758). 여기서는 쓰기 권한이 없는 파일로 흉내 낸다."""
    lock = tmp_path / "lock"
    lock.write_text("")
    lock.chmod(0o444)

    result = box()

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which("flock"), reason="flock 이 없다 (맥)")
def test_make_lock_helper_holds_the_deploy_lock_while_the_command_runs(tmp_path):
    """Makefile 의 deploy-migrate · deploy-load-* 잠금 — 쓰기 권한 없는 파일도 연다 (#758)."""
    lock = tmp_path / "lock"
    lock.write_text("")
    lock.chmod(0o444)
    probe = 'flock -n "$0" true && echo free || echo held'

    result = subprocess.run(
        ["bash", str(SCRIPT.parent / "with-deploy-lock.sh"), "sh", "-c", probe, str(lock)],
        capture_output=True,
        text=True,
        env={**os.environ, "DEPLOY_LOCK_FILE": str(lock)},
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "held"


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
        # ${X:-0} 은 빈 값 · 빠진 줄도 0(상한 없음)으로 바꾼다 — dev 상한이 조용히 빠진다
        ("dev", DEV_ENV.replace("API_MEM_LIMIT=768m", "API_MEM_LIMIT=")),
        ("dev", DEV_ENV.replace("REDIS_MEM_LIMIT=128m\n", "")),
        ("dev", DEV_ENV.replace("DB_MEM_LIMIT=512m", "DB_MEM_LIMIT=0")),
        ("prod", PROD_ENV.replace("https://api.", "https://api.dev.")),  # prod 가 dev API 를 굽는다
        ("prod", PROD_ENV.replace("STACK_SUFFIX=", "STACK_SUFFIX=-dev")),
        # 배포 전 백업의 짝 검사가 빌드 10분 뒤에야 멈춘다 (#758)
        ("prod", PROD_ENV.replace("APP_ENV=prod", "APP_ENV=production")),
        # 첫 전환의 down 이 Caddy · 옛 네트워크를 남겨 운영이 내려간 채 멈춘다
        ("prod", PROD_ENV.replace("COMPOSE_PROFILES=edge", "COMPOSE_PROFILES=")),
        # dev api 가 prod 주소(.20)를 받으면 EC2 메타데이터(서버 역할 열쇠)에 닿는다
        ("dev", DEV_ENV.replace("API_EDGE_IP=172.30.0.21\n", "")),
        ("dev", DEV_ENV.replace("172.30.0.21", "172.30.0.20")),
        ("prod", PROD_ENV + "API_EDGE_IP=172.30.0.21\n"),
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
    # compose 가 Caddy 에 넘기는 값($$ 를 되돌린 것)으로 검사한다.
    # 값은 환경으로 물려줘 ps 에 안 보인다
    assert " -e DEV_BASIC_AUTH_USER -e DEV_BASIC_AUTH_HASH -v " in calls[validate]
    assert AUTH_HASH not in calls[validate]
    assert f"caddy-env team {AUTH_HASH}" in calls
    assert validate < _index(calls, " build ")
    assert any(c.startswith("docker exec ktc4-caddy caddy reload") for c in calls)


def test_prod_registers_worker_cron_after_success(box):
    """worker cron 줄은 prod 배포가 건다 (#718). dev 는 아직 cron 을 두지 않는다."""
    box.env_file.write_text(PROD_ENV)
    assert box(target="prod").returncode == 0
    assert "worker-cron" in box.calls()

    box.env_file.write_text(DEV_ENV)
    assert box().returncode == 0
    assert box.calls().count("worker-cron") == 1


def test_failed_worker_cron_turns_prod_red_without_rollback(box):
    """빠뜨려도 조용한 게 문제라 빨간불로 끝낸다. 새 버전은 떠 있으니 되돌리지 않는다."""
    box.env_file.write_text(PROD_ENV)

    result = box(target="prod", cron_fail="1")

    assert result.returncode == 1
    assert "worker cron 등록" in result.stdout
    assert box.tag_file.read_text().strip() == "new"
    assert "prev" not in [api for api, _ in _ups(box.calls())]


def test_dev_does_not_touch_caddy(box):
    """basic_auth 계정은 prod .env 에만 있다 — dev 는 없어도 된다."""
    box()

    assert not any("caddy" in c for c in box.calls())


@pytest.mark.parametrize(
    "config",
    [
        compose_config(user=""),
        compose_config(pw_hash=""),
        # .env 에서 따옴표 없이 쓰면 compose 가 해시 속 $Ol… 를 변수로 먹는다 → 늘 401
        compose_config(pw_hash=AUTH_HASH[:7] + AUTH_HASH[20:]),
        # 사용자 이름에 공백이 있으면 Caddyfile 에서 칸이 밀린다
        compose_config(user="te am"),
        # COMPOSE_PROFILES 에서 edge 가 빠진 것과 같다 — Caddy 가 config 에 없다
        compose_config(caddy=False),
    ],
)
def test_prod_needs_valid_dev_basic_auth_before_building(box, config):
    """Caddy 는 prod 에만 뜬다. 값이 없거나 깨지면 Caddyfile 검사에서 prod 배포가 멈추거나
    dev 에 아무도 못 들어간다. 배포할 커밋의 compose 파일로 읽어야 해서 reset 뒤에 본다."""
    box.env_file.write_text(PROD_ENV)

    result = box(target="prod", config=config)

    assert result.returncode == 1
    assert "DEV_BASIC_AUTH" in result.stdout
    calls = box.calls()
    assert "git reset --hard new" in calls
    assert not any(" build " in c or " validate " in c for c in calls)
    assert _ups(calls) == []


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


def test_older_commit_than_the_running_one_is_refused(box):
    """옛 run 의 Re-run — 그 run 의 sha 가 그대로 와서,
    막지 않으면 운영이 옛 코드로 조용히 돌아간다."""
    result = box(old="1")

    assert result.returncode == 1
    assert "옛 커밋이다" in result.stdout
    calls = box.calls()
    assert "git merge-base --is-ancestor new prev" in calls
    assert not any(c.startswith("git reset") or c.startswith("docker") for c in calls)
    assert box.tag_file.read_text().strip() == "prev"


def test_allow_old_deploys_an_older_commit_on_purpose(box):
    result = box(old="1", allow_old="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.tag_file.read_text().strip() == "new"


@pytest.mark.parametrize("prev", ["local", "new"])
def test_first_deploy_and_same_sha_redeploy_skip_the_age_check(box, prev):
    """첫 배포(local)는 비교할 커밋이 없고, 같은 sha 는 다시 올려도 된다."""
    box.tag_file.write_text(f"{prev}\n")

    result = box(old="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(c.startswith("git merge-base") for c in box.calls())


GOOD = compose_config()


@pytest.mark.parametrize(
    ("env", "config", "ok"),
    [
        (DEV_ENV, GOOD, True),
        (PROD_ENV, GOOD, True),
        # dev .env 에서 프로젝트 이름만 빠졌다 → prod 로 보고 검사 → 꼬리 · 주소가 안 맞아 막힌다
        (DEV_ENV.replace("COMPOSE_PROJECT_NAME=ktc4-dev", "COMPOSE_PROJECT_NAME="), GOOD, False),
        (DEV_ENV.replace("APP_ENV=dev\n", ""), GOOD, False),
        (PROD_ENV.replace("COMPOSE_PROFILES=edge", "COMPOSE_PROFILES="), GOOD, False),
        (DEV_ENV.replace("WEB_MEM_LIMIT=384m", "WEB_MEM_LIMIT="), GOOD, False),
        # make deploy-up 이 깨진 계정으로 Caddy 를 다시 만들지 않게
        (PROD_ENV, compose_config(user=""), False),
    ],
)
def test_check_env_only_reads_env_and_compose_config(box, env, config, ok):
    """Makefile 배포 타깃이 먼저 부른다. git 은 부르지 않고, docker 는 compose config(읽기)만."""
    box.env_file.write_text(env)

    result = subprocess.run(
        ["bash", str(SCRIPT), "--check-env"],
        cwd=box.env_file.parents[2],
        capture_output=True,
        text=True,
        env=box.fake_env(config=config),
        timeout=30,
    )

    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    assert all(" config --format json" in c for c in box.calls())


# ── Makefile ─────────────────────────────────────────────────────────


def test_make_refuses_to_build_images_on_the_server(box):
    """.deployed-tag 가 있으면 서버다. 손으로 구우면 지금 소스가 배포된 sha 태그를 덮어
    롤백할 곳이 사라진다."""
    for target in ("api-image", "web-image"):
        result = box.make(target)

        assert result.returncode != 0
        assert "굽지 않는다" in result.stdout
    assert box.calls() == []


def test_make_up_never_builds(box):
    """api · web 은 compose 에 build: 가 있다. 이미지가 없을 때 up 이 구우면
    위와 같은 덮어쓰기다."""
    result = box.make("api-up", "web-up", "deploy-up")

    assert result.returncode == 0, result.stdout + result.stderr
    ups = [c for c in box.calls() if " up " in c]
    assert len(ups) == 3
    assert all(" up -d --no-build" in c and c.endswith("tag=prev web=prev") for c in ups)
    assert "sudo -n deploy/scripts/imds-firewall.sh --install" in box.calls()


@pytest.mark.parametrize("target", ["deploy-migrate", "deploy-load-food-dry"])
def test_make_run_targets_stop_when_the_deployed_image_is_missing(box, target):
    """compose run 에는 --no-build 가 없다 — 이미지가 없으면 지금 작업 트리를
    그 sha 이름으로 굽는다."""
    result = box.make(target, no_prev="1")

    assert result.returncode != 0
    assert "ktc4-dev-api:prev 이미지가 없다" in result.stdout
    assert not any(" run " in c for c in box.calls())


def test_make_migrate_reads_the_deployed_tag_when_it_runs(box):
    """make 가 미리 읽은 태그가 아니라, 잠금을 잡은 뒤의 .deployed-tag 를 쓴다."""
    result = box.make("deploy-migrate")

    assert result.returncode == 0, result.stdout + result.stderr
    run = next(c for c in box.calls() if " run --rm api-task " in c)
    assert "alembic upgrade heads" in run and run.endswith("tag=prev web=prev")


# ── compose run (일회용 상자) ─────────────────────────────────────────


def _services() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text())["services"]


def _one_off_targets(path: Path) -> list[str]:
    """`compose … run --rm <서비스>` 로 부르는 서비스 이름.
    `docker run --rm …`(Caddyfile · Alloy 검사)는 compose 가 아니라 빠진다 — `--network none` 의
    값을 서비스 이름으로 읽었다 (#955 CI)."""
    return re.findall(r"(?<!docker )\brun --rm (?:--\S+ )*([a-z][\w-]*)", path.read_text())


def _fixed_ip_networks(service: dict) -> list[str]:
    nets = service.get("networks") or {}
    if not isinstance(nets, dict):  # `- default` 처럼 목록이면 주소를 못 적는다
        return []
    return [name for name, cfg in nets.items() if cfg and "ipv4_address" in cfg]


@pytest.mark.parametrize("path", [SCRIPT, ROOT / "Makefile"], ids=["deploy.sh", "Makefile"])
def test_one_off_runs_never_use_a_service_with_a_fixed_ip(path):
    """compose run 은 서비스의 고정 IP 까지 복사한다. api(ktc4-edge .20 · .21)가 떠 있는 동안
    `run --rm api` 를 하면 같은 주소를 두 번 잡아 Address already in use 로 배포가 멈춘다.
    첫 배포 · 첫 전환은 api 가 없어서 통과하고 두 번째 배포부터 늘 터졌다 (11-09 dev 배포 3회)."""
    targets = _one_off_targets(path)
    services = _services()

    assert targets, f"{path.name} 에서 compose run 을 못 찾았다 — 정규식이 낡았다"
    for name in targets:
        fixed = _fixed_ip_networks(services[name])
        assert not fixed, f"{path.name}: run --rm {name} — {name} 은 {fixed} 에 고정 IP 가 있다"


SHARED_WITH_API = (
    "image",
    "environment",
    "env_file",
    "volumes",
    "mem_limit",
    "memswap_limit",
    "depends_on",
)


def test_api_task_is_the_api_image_with_api_settings_minus_the_edge():
    """일회용 작업(마이그레이션 · 적재)은 api 와 같은 이미지 · 같은 설정으로 돈다.
    다른 것은 셋뿐이다.
    - edge 에 안 붙는다 — 고정 IP 가 없고, Caddy 망 · S3 열쇠(.20)에 닿을 이유도 없다.
    - build 가 없다 — 이미지가 없을 때 compose run 이 지금 소스를 배포된 sha 이름으로
      굽지 못하게 (#808 리뷰).
    - profile 이 있다 — up(deploy.sh · make deploy-up)이 상시 서비스로 띄우지 않게."""
    s = _services()
    api, task = s["api"], s["api-task"]

    for key in SHARED_WITH_API:
        assert task.get(key) == api.get(key), key
    assert "edge" not in (task.get("networks") or ["default"])
    assert "build" not in task
    assert task.get("pull_policy") == "never"
    assert task.get("profiles")
    assert "container_name" not in task


def test_migration_runs_in_api_task_before_up(box):
    box()

    calls = box.calls()
    migrate = _index(calls, "alembic upgrade heads")
    assert " run --rm api-task uv run --no-sync alembic upgrade heads " in calls[migrate]
    assert migrate < _index(calls, " up ")
