"""deploy/scripts/worker-cron.sh — prod 배포가 api worker 의 cron 줄을 건다 (#718).

crontab 을 가짜로 바꿔 돈다 — 가짜 crontab 은 파일 하나를 crontab 으로 쓰고, 받은 인자를 적는다.
FAKE_LIST_ERR 가 있으면 `-l` 이 그 말로 실패한다 (권한 · 사용자 없음 같은 읽기 실패).
FAKE_LIST_WARN 이 있으면 `-l` 이 성공하면서 stderr 에 그 말을 찍는다.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[5] / "deploy" / "scripts" / "worker-cron.sh"

FAKE_CRONTAB = r"""#!/bin/sh
echo "crontab $*" >> "$FAKE_LOG"
[ "$1" = -u ] && shift 2
case "$1" in
  -l)
    [ -z "$FAKE_LIST_ERR" ] || { echo "$FAKE_LIST_ERR" >&2; exit 1; }
    [ -z "$FAKE_LIST_WARN" ] || echo "$FAKE_LIST_WARN" >&2
    [ -f "$FAKE_TAB" ] || { echo "no crontab for ubuntu" >&2; exit 1; }
    cat "$FAKE_TAB" ;;
  -) cat > "$FAKE_TAB" ;;
esac
"""

BACKUP = "0 3 * * * cd /home/ubuntu/ktc4-pusan-3 && bash deploy/scripts/backup.sh daily"
RUN = "docker exec ktc4-api uv run --no-sync python -m app.workers"
WORKERS = ("reminder_dispatch", "cleanup", "profile_recompute")


@pytest.fixture
def box(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "crontab").write_text(FAKE_CRONTAB)
    (bin_dir / "crontab").chmod(0o755)
    tab = tmp_path / "tab"
    log = tmp_path / "calls.log"

    def run(
        list_err: str = "", list_warn: str = "", tz: str = "Asia/Seoul"
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_TAB": str(tab),
            "FAKE_LOG": str(log),
            "FAKE_LIST_ERR": list_err,
            "FAKE_LIST_WARN": list_warn,
            "TZ": tz,
            # 실행하는 사람과 다른 사용자 — SSM(root)이 ubuntu 의 crontab 을 고치는 자리
            "CRON_USER": "ubuntu-test",
        }
        return subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=30
        )

    run.tab = tab  # type: ignore[attr-defined]
    run.calls = lambda: log.read_text().splitlines() if log.exists() else []  # type: ignore[attr-defined]
    return run


def _count(text: str, worker: str) -> int:
    return sum(f"-m app.workers.{worker} " in line for line in text.splitlines())


def test_empty_server_gets_every_worker_once_and_rerun_adds_nothing(box):
    first = box()
    assert first.returncode == 0, first.stdout + first.stderr
    tab = box.tab.read_text()
    assert all(_count(tab, w) == 1 for w in WORKERS)
    assert "docker exec ktc4-api " in tab
    # 다른 사용자의 crontab 을 고른다 — root crontab 에 따로 들어가면 두 번 돈다
    assert all(c.startswith("crontab -u ubuntu-test ") for c in box.calls())

    second = box()
    assert second.returncode == 0, second.stdout + second.stderr
    assert box.tab.read_text() == tab
    assert "더함" not in second.stdout


def test_keeps_other_lines_and_hand_edited_worker_lines(box):
    reminder = f"*/2 * * * * {RUN}.reminder_dispatch >> /tmp/x 2>&1"
    box.tab.write_text(f"{BACKUP}\n{reminder}\n")

    result = box()

    assert result.returncode == 0, result.stdout + result.stderr
    tab = box.tab.read_text()
    assert BACKUP in tab
    assert reminder in tab  # 손으로 바꾼 시각을 덮지 않는다
    assert all(_count(tab, w) == 1 for w in WORKERS)


def test_commented_out_worker_line_does_not_count(box):
    box.tab.write_text(f"# 0 4 * * * {RUN}.cleanup >> /x 2>&1\n")

    assert box().returncode == 0
    assert "\n0 4 * * * docker exec ktc4-api " in box.tab.read_text()


def test_unreadable_crontab_writes_nothing(box):
    """빈 것으로 보고 쓰면 백업 cron 줄이 지워진다."""
    box.tab.write_text(f"{BACKUP}\n")

    result = box(list_err="crontab: user ubuntu-test unknown")

    assert result.returncode != 0
    assert "못 읽었다" in result.stdout + result.stderr
    assert box.tab.read_text() == f"{BACKUP}\n"
    assert not any(c.endswith(" -") for c in box.calls())


def test_duplicate_worker_line_turns_red(box):
    line = f"0 4 * * * {RUN}.cleanup >> /x 2>&1"
    box.tab.write_text(f"{line}\n{line}\n")

    result = box()

    assert result.returncode == 1
    assert "cleanup 줄이 2개다" in result.stdout


@pytest.mark.parametrize(
    "line",
    [
        f"0 4 * * * {RUN}.cleanup",  # 리다이렉트 없음
        "0 4 * * * docker exec -i ktc4-api uv run python -m app.workers.cleanup >> /x 2>&1",
        f"0\t4\t*\t*\t*\t{RUN}.cleanup\t>> /x 2>&1",
        f"0 4 * * * {RUN}.cleanup\r",
    ],
)
def test_hand_written_line_shapes_are_recognized(box, line):
    """못 알아보면 한 줄을 더 넣어 같은 배치가 두 번 돈다."""
    box.tab.write_text(f"{line}\n")

    assert box().returncode == 0
    tab = box.tab.read_text()
    assert line.rstrip("\r") in tab  # read_text 가 CRLF 를 LF 로 읽는다
    assert sum("app.workers.cleanup" in x for x in tab.splitlines()) == 1  # 더하지 않았다


def test_dev_and_lookalike_lines_do_not_count(box):
    box.tab.write_text(
        "0 4 * * * docker exec ktc4-dev-api uv run python -m app.workers.cleanup\n"
        f"0 4 * * * {RUN}.cleanup_x >> /x 2>&1\n"
    )

    assert box().returncode == 0
    assert _count(box.tab.read_text(), "cleanup") == 1


def test_stderr_warning_is_not_written_into_crontab(box):
    box.tab.write_text(f"{BACKUP}\n")

    assert box(list_warn="warning: something").returncode == 0
    assert "warning" not in box.tab.read_text()


def test_non_korean_server_clock_warns_but_still_registers(box):
    """04시 · 05시는 서버 시계 기준이다. UTC 면 한국 13시 · 14시에 돈다 — 알리기만 한다."""
    result = box(tz="UTC")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "서버 시계가 UTC" in result.stdout
    assert all(_count(box.tab.read_text(), w) == 1 for w in WORKERS)


def test_korean_server_clock_does_not_warn(box):
    assert "서버 시계가" not in box().stdout
