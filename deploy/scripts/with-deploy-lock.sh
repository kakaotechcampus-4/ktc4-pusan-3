#!/usr/bin/env bash
# 배포 잠금을 잡은 채 명령 하나를 돈다 — Makefile 의 deploy-migrate · deploy-load-* 가 부른다 (#758).
#
#   사용: bash deploy/scripts/with-deploy-lock.sh <명령...>
#     DEPLOY_LOCK_FILE  잠금 파일. 기본값 /var/lock/ktc4-deploy.lock (deploy.sh · backup-lib.sh 와 같은 파일)
#                       이름이 deploy.sh 의 DEPLOY_LOCK 과 다른 것은 Makefile 의 DEPLOY_LOCK 이 이 명령 자체라서다.
#
# deploy.sh · backup-lib.sh 의 take_deploy_lock 과 같은 방식 — 없으면 0644 로 만들고(실패는 넘김) 읽기로 연다.
# 🚨 `flock <파일> 명령` 으로 되돌리지 않는다. 그 꼴은 O_CREAT 로 열어, 재부팅 뒤 복원 점검 cron(ubuntu)이 먼저
#    만든 잠금 파일을 root 가 못 연다 (/var/lock 은 sticky · fs.protected_regular=2 — take_deploy_lock 머리말).
# 명령은 exec 로 돈다 — 잠금(fd 9)을 물려받아 끝날 때까지 쥔다.

set -euo pipefail

LOCK="${DEPLOY_LOCK_FILE:-/var/lock/ktc4-deploy.lock}"
[ -e "$LOCK" ] || (umask 022 && : > "$LOCK") 2>/dev/null || true
exec 9<"$LOCK" || { echo "잠금 파일 $LOCK 을 열지 못했다 — 소유자 · 권한(0644)을 본다" >&2; exit 1; }
echo "배포 잠금 대기" >&2
flock -w 1200 9 || { echo "20분 동안 배포가 안 끝나 기다리지 못했다" >&2; exit 1; }
exec "$@"
