#!/usr/bin/env bash
# 가장 최근 백업이 실제로 복원되는지 임시 Postgres 에 복원해 본다 (#758). 끝나면 임시 DB 를 통째로 지운다.
#
#   사용: cd <prod checkout> && bash deploy/scripts/restore-check.sh
#     이 checkout 환경(APP_ENV)의 가장 최근 백업 — daily · predeploy 중 가장 새것 — 을 받아
#     ① 복원이 끝까지 되는지 ② alembic_version 이 있는지 ③ 핵심 테이블(parent · child)이 있는지 본다.
#     그리고 가장 최근 **daily** 백업이 26시간보다 오래됐으면 실패다 — 배포 전 백업이 있어도 매일 백업이
#     멈춘 것을 가리지 못하게 daily 만 따로 본다. 복원 점검은 그래도 끝까지 한다.
#     cron 에서 backup.sh daily 다음에 prod 만 돌린다 — 백업이 실패해도 이건 돈다 (backup.sh 머리말).
#     성공은 로그에만, 실패만 알린다.
#     cron 자체가 멈추면 이것도 안 돈다 — 그건 서버 밖의 .github/workflows/backup-watch.yml 이 본다.
#
#   로컬 시험: bash deploy/scripts/restore-check.sh --local <폴더>   그 폴더의 가장 새 .dump 로
#
# 🚨 운영 DB 에는 손대지 않는다. 임시 컨테이너는 네트워크 없이(--network none) 뜨고, 이미지는
#    운영 DB 컨테이너와 같은 것을 쓴다. prod 백업을 dev 스택에 복원하지 않는다 (#758 설계 조건).
# 🚨 4GB 서버라 메모리 상한(768MB)을 걸고, 배포와 같은 잠금을 잡아 이미지 빌드와 겹치지 않게 한다
#    (backup-lib.sh 의 take_deploy_lock). 배포도 같은 잠금을 잡아야 의미가 있다.
# 🚨 멈출 수 있는 단계(S3 · 복원 · psql · 임시 DB 지우기)마다 시간 상한을 건다 — 멈춘 채 배포 잠금을 쥐고
#    서 있지 않게. 스크립트 통째로 timeout 을 씌우면 잠금 fd 가 자식에 남아 풀리지 않는다. 단계가 끊기면
#    fail → cleanup → 종료로 잠금이 풀린다. 끊긴 단계는 exit 124 로 알린다.
# 🚨 출력은 alembic 버전 · 테이블별 행 수까지. pg_restore · psql 의 stderr 는 내지 않는다 —
#    COPY 오류는 행의 값을 싣는다 (backup-lib.sh 의 quiet).

set -euo pipefail
# shellcheck source=deploy/scripts/backup-lib.sh
. "$(dirname "$0")/backup-lib.sh"
trap 'fail "예상 못 한 실패 (줄 $LINENO)"' ERR

# 시간 상한(초). 맥에는 timeout 이 없다 — 로컬 시험에서는 걸지 않는다 (flock 과 같다)
# 끊기면 어느 단계 · 몇 초였는지 한 줄 남긴다. fd 3 으로 쓰는 이유 — $(...) 안에서 불려도 stdout 에
# 섞이지 않고, quiet 가 stderr 를 가져가도 로그 파일로 간다.
exec 3>&1
limit() {
  local s="$1" rc=0 t0=$SECONDS
  shift
  if command -v timeout >/dev/null; then timeout -k 10 "$s" "$@" || rc=$?; else "$@" || rc=$?; fi
  # 124 = TERM 으로 끊김, 137 = TERM 을 무시해 10초 뒤 KILL 로 끊김. 137 은 안에서 KILL(임시 DB 의
  # 768MB 상한 OOM 등)로 죽어도 같은 값이라, 상한까지 갔을 때만 시간 상한으로 적는다.
  # ${*:1:3} — set -u 라 $3 을 바로 쓰면 인자가 적은 명령에서 여기서 죽고 fail(알림)까지 못 간다
  if [ "$rc" = 124 ] || { [ "$rc" = 137 ] && [ $((SECONDS - t0)) -ge "$s" ]; }; then
    log "시간 상한 ${s}초에 걸려 끊었다 (exit $rc) — ${*:1:3}" >&3
  fi
  return "$rc"
}

if [ "${1:-}" = --local ]; then
  pick_target "${2:?사용법: restore-check.sh --local <폴더>}"
else
  pick_target ""
fi

umask 077
WORK="$(mktemp -d)"
TMPDB="ktc4-restore-check-$$"
cleanup() {
  limit 60 docker rm -f -v "$TMPDB" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
DUMP="$WORK/db.dump"
STALE=0
MAX_DAILY_AGE=$((26 * 3600))

if [ -z "$LOCAL_DIR" ]; then
  take_deploy_lock
  log "배포 잠금 얻음 (${SECONDS}초 기다림)"
fi
SECONDS=0 # 아래 '복원 통과 … N초' 는 잠금을 기다린 시간을 빼고 센다

# ── 가장 최근 백업 고르기 ──
if [ -n "$LOCAL_DIR" ]; then
  # shellcheck disable=SC2012  # 이름은 backup.sh 가 만든 시각 · 영문뿐이다
  SRC="$(ls -t "$LOCAL_DIR"/*.dump 2>/dev/null | head -n 1 || true)"
  [ -n "$SRC" ] || fail "$LOCAL_DIR 에 .dump 가 없다"
  KEY="$(basename "$SRC")"
  cp "$SRC" "$DUMP"
else
  # 비어 있으면 aws 가 "None" 한 줄을 낸다. 시각(ISO)이 앞이라 글자 순 정렬이 곧 시간 순이다.
  LIST="$(limit 60 aws s3api list-objects-v2 --bucket "$BUCKET" --prefix "$ENVNAME/" \
    --query 'Contents[].[LastModified, Key]' --output text)" ||
    fail "백업 목록 읽기 실패 (exit $?) — EC2 역할 · 버킷 정책을 본다"
  LIST="$(printf '%s\n' "$LIST" | grep -v '^None$' | sort || true)"
  read -r _ LATEST <<< "$(printf '%s\n' "$LIST" | tail -n 1)" || true
  # aws 의 text 출력은 탭으로 나뉜다 — 칸으로 고른다.
  read -r DAILY_AT DAILY <<< "$(printf '%s\n' "$LIST" | awk -v p="$ENVNAME/daily/" 'index($2, p) == 1' | tail -n 1)" || true
  [ -n "${LATEST:-}" ] || fail "$ENVNAME/ 아래에 백업이 하나도 없다"

  if [ -z "${DAILY:-}" ]; then
    alert "매일 백업(daily)이 하나도 없다 — cron 을 본다"
    STALE=1
  else
    AGE=$(( $(date +%s) - $(date -d "$DAILY_AT" +%s) ))
    if [ "$AGE" -gt "$MAX_DAILY_AGE" ]; then
      alert "가장 최근 매일 백업($DAILY)이 $((AGE / 3600))시간 $((AGE % 3600 / 60))분 전이다 — cron 을 본다"
      STALE=1
    fi
  fi

  KEY="$LATEST"
  limit 300 aws s3 cp "s3://$BUCKET/$KEY" "$DUMP" --only-show-errors --no-progress || fail "백업 받기 실패 (exit $?)"
fi
log "$KEY 복원 점검 ($(wc -c < "$DUMP" | tr -d ' ') bytes)"

# ── 임시 DB 에 복원 ──
IMAGE="$(limit 60 docker inspect --format '{{.Config.Image}}' "$CONTAINER")" ||
  fail "운영 DB 컨테이너 $CONTAINER 가 없어 이미지를 정하지 못했다"
limit 60 docker run -d --name "$TMPDB" --network none --memory 768m --memory-swap 768m \
  -e POSTGRES_USER=restore -e POSTGRES_DB=restore -e POSTGRES_HOST_AUTH_METHOD=trust \
  "$IMAGE" >/dev/null || fail "임시 DB 컨테이너 띄우기 실패 (exit $?)"

# 초기화 중에도 소켓으로는 응답하므로 TCP(127.0.0.1)로 본다 — 초기화가 끝나야 TCP 를 연다.
# 횟수가 아니라 마감 시각으로 센다 — 확인 한 번이 멈추면 60번은 몇 분이다. pg_isready 는 자체 연결
# 상한(3초)이 있어 limit 은 docker exec 가 멈출 때만 필요하다.
ready=0
deadline=$((SECONDS + 60))
while [ "$SECONDS" -lt "$deadline" ]; do
  if limit 5 docker exec "$TMPDB" pg_isready -q -h 127.0.0.1 -U restore -d restore; then
    ready=1
    break
  fi
  sleep 1
done
[ "$ready" = 1 ] || fail "임시 DB 가 60초 안에 뜨지 않았다"

quiet limit 600 docker exec -i "$TMPDB" pg_restore -U restore -d restore --no-owner --no-privileges --exit-on-error < "$DUMP" ||
  fail "복원 실패 (exit $?$(err_table)) — 이 백업으로는 되살릴 수 없다"

q() { quiet limit 60 docker exec "$TMPDB" psql -U restore -d restore -v ON_ERROR_STOP=1 -Atc "$1"; }
VERSION="$(q 'SELECT string_agg(version_num, $$,$$ ORDER BY version_num) FROM alembic_version')" ||
  fail "복원한 DB 에서 alembic_version 읽기 실패 (exit $?)"
[ -n "$VERSION" ] || fail "복원한 DB 의 alembic_version 이 비었다"
[ "$(q "SELECT to_regclass('public.parent') IS NOT NULL AND to_regclass('public.child') IS NOT NULL")" = t ] ||
  fail "복원한 DB 에 parent · child 테이블이 없다"

# 테이블별 행 수 — 로그에서 날마다 견줘 볼 수 있게. 숫자만이다.
COUNTS="$(quiet limit 120 docker exec -i "$TMPDB" psql -U restore -d restore -v ON_ERROR_STOP=1 -At <<'SQL'
SELECT format('SELECT %L || $$ $$ || count(*) FROM %I.%I', table_name, table_schema, table_name)
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name
\gexec
SQL
)" || fail "행 수 세기 실패 (exit $?)"
printf '%s\n' "$COUNTS" | sed 's/^/  /'
TOTAL="$(printf '%s\n' "$COUNTS" | awk '{ s += $2 } END { print s + 0 }')"
log "복원 통과 $KEY — alembic $VERSION · 테이블 $(printf '%s\n' "$COUNTS" | grep -c .) 개 · 행 $TOTAL · ${SECONDS}초"

# 복원은 됐어도 매일 백업이 밀렸으면 실패로 끝낸다 (알림은 위에서 보냈다).
[ "$STALE" = 0 ] || exit 1
