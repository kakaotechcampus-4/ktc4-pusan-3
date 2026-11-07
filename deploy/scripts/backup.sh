#!/usr/bin/env bash
# DB 를 pg_dump 로 떠서 S3 서울 버킷에 올린다 (#758). 서버에 만든 덤프 파일은 올린 뒤 바로 지운다.
#
#   사용: cd <checkout> && bash deploy/scripts/backup.sh <daily|predeploy> [표시]
#     daily      서버 cron 이 매일 03시에 부른다
#     predeploy  배포가 마이그레이션 직전에 부른다. 실패하면 0 이 아닌 값으로 끝나고 배포가 멈춘다
#     표시       키 끝에 붙일 짧은 글자 — 배포 커밋 sha 같은 것. 영문 · 숫자 · - 만, 앞 12자
#     대상 스택 · 환경 이름은 이 checkout 의 deploy/docker/.env 로 정한다 (backup-lib.sh 머리말)
#     S3 키: <APP_ENV>/<daily|predeploy>/<UTC 시각>[-표시].dump
#
#   로컬 시험: bash deploy/scripts/backup.sh --local <폴더>
#     개발 DB(ktc4-postgres)를 떠서 S3 대신 그 폴더에 둔다. 이어서 restore-check.sh --local <폴더>.
#
#   cron (서버, prod · dev checkout 각각 — crontab -e):
#     0 3 * * * cd /home/ubuntu/ktc4-pusan-3 && { bash deploy/scripts/backup.sh daily; bash deploy/scripts/restore-check.sh; } >> /var/tmp/ktc4-backup.log 2>&1
#     5 3 * * * cd /home/ubuntu/ktc4-pusan-3-dev && bash deploy/scripts/backup.sh daily >> /var/tmp/ktc4-backup-dev.log 2>&1
#
# 🚨 덤프는 EC2 에서 S3 로 바로 간다. GitHub Actions 러너를 거치면 미국으로 나가는 국외 이전이다 —
#    Actions 는 SSM 명령만 보낸다. 덤프를 Actions 출력 · 아티팩트로 내보내지 않는다.
# 🚨 출력에는 키 · 크기 · 테이블 수만 찍는다. 덤프 내용은 어디에도 찍지 않는다.
# 🚨 배포 잠금(/var/lock/ktc4-deploy.lock)을 잡지 않는다 — 배포가 잠금을 쥔 채 이걸 부르므로 잡으면 서로 기다린다.
#    pg_dump 는 가볍고(11-06 기준 17MB · 수 초), 도는 중에 마이그레이션이 오면 그쪽이 덤프가 끝날 때까지 기다린다.
# 🚨 S3 에서 지우지 않는다. 버킷의 28일 수명 주기가 지운다 (EC2 역할에는 지우기 권한이 없다).
# 🚨 비밀번호를 다루지 않는다. pg_dump 는 DB 컨테이너 안에서 그 컨테이너의 POSTGRES_USER 로 돈다
#    (컨테이너 안 소켓 접속). 버전도 서버 DB 와 같은 pg_dump 가 된다.
# 🚨 pg_dump · pg_restore 의 stderr 는 화면에 내지 않는다 (backup-lib.sh 의 quiet) — 이 출력은
#    배포 때 SSM 을 거쳐 GitHub Actions 로그에 남는다.
# 복원 시점은 덤프가 시작된 순간의 스냅샷이다. 덤프 뒤 · 배포 down 전 몇 분의 쓰기는 이 백업에 없다.

set -euo pipefail
# shellcheck source=deploy/scripts/backup-lib.sh
. "$(dirname "$0")/backup-lib.sh"
trap 'fail "예상 못 한 실패 (줄 $LINENO)"' ERR

if [ "${1:-}" = --local ]; then
  KIND="local"
  LABEL=""
  pick_target "${2:?사용법: backup.sh --local <폴더>}"
  mkdir -p "$LOCAL_DIR"
else
  KIND="${1:-}"
  case "$KIND" in
    daily | predeploy) ;;
    *) echo "사용법: backup.sh <daily|predeploy> [표시]  |  backup.sh --local <폴더>" >&2; exit 2 ;;
  esac
  LABEL="$(printf '%s' "${2:-}" | tr -cd 'A-Za-z0-9-' | cut -c1-12)"
  pick_target ""
fi

# 서버 디스크의 덤프 — 주인만 읽는 임시 폴더에 두고, 성공이든 실패든 끝나면 지운다.
umask 077
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
DUMP="$WORK/db.dump"

NAME="$(date -u +%Y-%m-%dT%H%M%SZ)${LABEL:+-$LABEL}.dump"
if [ -n "$LOCAL_DIR" ]; then KEY="$NAME"; else KEY="$ENVNAME/$KIND/$NAME"; fi

log "$KEY — $CONTAINER 덤프"
# shellcheck disable=SC2016  # $POSTGRES_* 는 컨테이너 안에서 풀린다
quiet docker exec "$CONTAINER" sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DUMP" ||
  fail "pg_dump 실패 (exit $?) — DB 컨테이너 $CONTAINER 가 떠 있는지 본다"

# 깨진 덤프를 올리고 "백업 있음" 으로 믿는 게 가장 나쁘다. 목차를 끝까지 읽혀 본다.
TOC="$(quiet docker exec -i "$CONTAINER" pg_restore --list < "$DUMP")" ||
  fail "덤프 목차 읽기 실패 (exit $?) — 덤프가 깨졌다"
TABLES="$(printf '%s\n' "$TOC" | grep -c ' TABLE DATA ' || true)"
[ "$TABLES" -gt 0 ] || fail "덤프에 테이블 데이터가 없다"
SIZE="$(wc -c < "$DUMP" | tr -d ' ')"

if [ -n "$LOCAL_DIR" ]; then
  cp "$DUMP" "$LOCAL_DIR/$NAME"
  log "로컬 $LOCAL_DIR/$NAME ($SIZE bytes · 테이블 $TABLES 개)"
  exit 0
fi

aws s3 cp "$DUMP" "s3://$BUCKET/$KEY" --sse AES256 --only-show-errors --no-progress ||
  fail "S3 업로드 실패 (exit $?) — EC2 역할 · 버킷 정책을 본다"
log "올림 $KEY ($SIZE bytes · 테이블 $TABLES 개)"
