"""deploy/scripts/backup.sh · restore-check.sh — 운영 DB 백업과 복원 점검 (#758).

실제 pg_dump · 복원은 `backup.sh --local` · `restore-check.sh --local` 로 로컬 개발 DB 에서 확인한다
(#758 검증 방법). 여기서는 그걸로 볼 수 없는 것만 본다.

- 배포 전 백업이 실패하면 0 이 아닌 값으로 끝나 배포가 멈춘다. 건너뛰라면 알리기만 한다
- 덤프가 엉뚱한 환경의 키로 가거나 서버 디스크에 남지 않는다
- pg_dump · pg_restore 오류에 실린 행 내용이 출력(cron 로그 · SSM → Actions)에 나가지 않는다
- 배포 전 백업이 있어도 매일 백업이 밀린 것을 잡는다
- 잠금 파일을 다른 사용자도 열 수 있게(0644) 만든다

docker · aws · curl 을 가짜로 바꿔 돈다. 가짜 docker 는 실패할 때 stderr 에 행 내용(SECRET-ROW)을
흉내 낸 줄을 낸다.
"""

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[5] / "deploy" / "scripts"

# restore-check.sh 는 서버(Ubuntu)에서만 돈다 — GNU `date -d` 와 `flock` 을 쓴다. 맥에는 둘 다 없다.
_GNU_DATE = subprocess.run(["date", "-d", "@0"], capture_output=True).returncode == 0
linux_only = pytest.mark.skipif(
    not (_GNU_DATE and shutil.which("flock")), reason="GNU date · flock 이 없다 (맥)"
)

FAKE_DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
case "$*" in
  "inspect "*) echo pgvector/pgvector:0.8.6-pg18 ;;
  "run "* | "rm "*) ;;
  *pg_isready*) ;;
  *"pg_restore --list"*) cat > /dev/null; printf '1; 2615 0 TABLE DATA public child dev\n' ;;
  *"pg_restore -U"*)
    cat > /dev/null
    [ -z "$FAKE_RESTORE_FAIL" ] && exit 0
    echo 'pg_restore: error: COPY failed for table "child": ERROR: bad value "SECRET-ROW"' >&2
    exit 1 ;;
  *"alembic_version"*) echo abc123 ;;
  *to_regclass*) echo t ;;
  "exec -i "*psql*) cat > /dev/null; printf 'child 3\nparent 2\n' ;;
  *pg_dump*)
    if [ -n "$FAKE_DUMP_FAIL" ]; then echo 'pg_dump: error: SECRET-ROW' >&2; exit 1; fi
    printf 'PGDMP SECRET-ROW' ;;
esac
"""

FAKE_AWS = r"""#!/bin/sh
echo "aws $*" >> "$FAKE_LOG"
case "$1 $2" in
  "s3api list-objects-v2") printf '%b' "$FAKE_LIST" ;;
  "s3 cp")
    case "$3" in
      s3://*) printf 'PGDMP' > "$4" ;;
      *) [ -f "$3" ] && echo "aws saw file" >> "$FAKE_LOG" ;;
    esac ;;
esac
"""


def _iso(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@pytest.fixture
def box(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "aws").write_text(FAKE_AWS)
    (bin_dir / "curl").write_text('#!/bin/sh\necho "curl $*" >> "$FAKE_LOG"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    work = tmp_path / "tmp"
    work.mkdir()
    log = tmp_path / "calls.log"
    env_file = tmp_path / ".env"

    def run(
        script: str,
        *args: str,
        app_env: str = "prod",
        suffix: str = "",
        dump_fail: bool = False,
        restore_fail: bool = False,
        listing: str = "",
        skip: bool = False,
    ):
        env_file.write_text(
            f"APP_ENV={app_env}\nSTACK_SUFFIX={suffix}\nBACKUP_S3_BUCKET=bk\n"
            "ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/1/SECRET-TOKEN\n"
        )
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ENV_FILE": str(env_file),
            "FAKE_LOG": str(log),
            "FAKE_DUMP_FAIL": "1" if dump_fail else "",
            "FAKE_RESTORE_FAIL": "1" if restore_fail else "",
            "FAKE_LIST": listing,
            "SKIP_PREDEPLOY_BACKUP": "1" if skip else "",
            "TMPDIR": str(work),
            "DEPLOY_LOCK": str(tmp_path / "deploy.lock"),
        }
        result = subprocess.run(
            ["bash", str(SCRIPTS / script), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        result.calls = log.read_text() if log.exists() else ""  # type: ignore[attr-defined]
        result.out = result.stdout + result.stderr  # type: ignore[attr-defined]
        return result

    run.work = work  # type: ignore[attr-defined]
    run.lock = tmp_path / "deploy.lock"  # type: ignore[attr-defined]
    return run


# ── backup.sh ──


@pytest.mark.parametrize(
    ("app_env", "suffix", "container"),
    [("prod", "", "ktc4-postgres-deploy"), ("dev", "-dev", "ktc4-dev-postgres-deploy")],
)
def test_올린_뒤_서버의_덤프는_남지_않는다(box, app_env, suffix, container):
    result = box("backup.sh", "predeploy", "abc1234def5678", app_env=app_env, suffix=suffix)

    assert result.returncode == 0, result.out
    assert f"docker exec {container} " in result.calls
    assert f"s3://bk/{app_env}/predeploy/" in result.calls
    assert "-abc1234def56.dump --sse AES256" in result.calls  # 표시는 앞 12자
    assert "aws saw file" in result.calls  # 올릴 때는 있었고
    assert list(box.work.iterdir()) == []  # 끝나면 없다
    assert "SECRET-ROW" not in result.out


def test_덤프가_실패하면_올리지_않고_실패로_끝난다(box):
    """배포는 이 종료 코드로 멈춘다 — 백업 없이 마이그레이션이 돌면 안 된다."""
    result = box("backup.sh", "predeploy", dump_fail=True)

    assert result.returncode != 0
    assert "aws " not in result.calls
    assert result.calls.count("curl ") == 1
    assert "prod/predeploy/" in result.out  # 알림에 어느 백업인지가 붙는다
    assert "SECRET-ROW" not in result.out + result.calls  # pg_dump 의 오류 원문은 안 나간다
    assert "SECRET-TOKEN" not in result.out
    assert list(box.work.iterdir()) == []


def test_배포_전_백업을_건너뛰면_DB_를_건드리지_않고_알린다(box):
    """DB 컨테이너가 죽어 백업이 안 될 때 그걸 고치는 배포가 막히지 않게 — 건너뛴 것은 알린다."""
    result = box("backup.sh", "predeploy", "abc1234", skip=True)

    assert result.returncode == 0, result.out
    assert "docker " not in result.calls
    assert "aws " not in result.calls
    assert result.calls.count("curl ") == 1


def test_환경_짝이_안_맞으면_DB_를_건드리지_않는다(box):
    """dev 표시인데 prod 스택을 가리키면 운영 덤프가 dev/ 키로 간다."""
    result = box("backup.sh", "daily", app_env="dev", suffix="")

    assert result.returncode != 0
    assert "docker " not in result.calls
    assert "aws " not in result.calls


# ── restore-check.sh ──


@linux_only
def test_복원_오류의_행_내용은_내보내지_않고_테이블만_알린다(box):
    listing = f"{_iso(2)}\tprod/daily/a.dump\n"
    result = box("restore-check.sh", restore_fail=True, listing=listing)

    assert result.returncode != 0
    assert "SECRET-ROW" not in result.out + result.calls
    assert "테이블 child" in result.out
    assert "[prod/daily/a.dump]" in result.out
    assert "docker rm -f -v ktc4-restore-check-" in result.calls  # 실패해도 임시 DB 는 지운다


@linux_only
def test_배포_전_백업이_새것이어도_매일_백업이_밀렸으면_실패다(box):
    listing = f"{_iso(100)}\tprod/daily/old.dump\n{_iso(1)}\tprod/predeploy/new.dump\n"
    result = box("restore-check.sh", listing=listing)

    assert result.returncode != 0
    assert "prod/daily/old.dump" in result.out and "100시간" in result.out
    assert "s3://bk/prod/predeploy/new.dump" in result.calls  # 복원은 가장 새것으로 끝까지 한다
    assert "복원 통과" in result.out


@linux_only
def test_매일_백업이_26시간_안이면_통과한다(box):
    listing = f"{_iso(25.9)}\tprod/daily/a.dump\n"
    result = box("restore-check.sh", listing=listing)

    assert result.returncode == 0, result.out
    assert "curl " not in result.calls


@linux_only
def test_잠금_파일은_다른_사용자도_열_수_있게_만든다(box):
    """배포(root)와 cron(ubuntu)이 같은 파일을 연다. umask 077 아래서 0600 으로 만들면 못 연다."""
    box("restore-check.sh", listing=f"{_iso(1)}\tprod/daily/a.dump\n")

    assert box.lock.stat().st_mode & 0o777 == 0o644
