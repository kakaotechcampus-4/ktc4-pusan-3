# shellcheck shell=bash disable=SC2034  # 변수는 이 파일을 source 하는 스크립트가 쓴다
# backup.sh · restore-check.sh 가 같이 쓰는 것 — 대상 스택 고르기, 로그, 실패 알림 (#758).
# 혼자 돌리는 파일이 아니다. 두 스크립트가 `source` 한다.
#
# 대상은 이 checkout 의 deploy/docker/.env 로 정한다 — 한 서버에 prod · dev checkout 이 따로 있고(#753),
# 각 checkout 은 자기 스택만 다룬다. dev checkout(develop 코드)이 운영 DB 를 건드리지 않게 하려는 것이다.
#   STACK_SUFFIX  비면 prod 스택(ktc4-postgres-deploy), -dev 면 dev 스택(ktc4-dev-postgres-deploy)
#   APP_ENV       prod · dev — S3 키의 앞머리이자 알림 꼬리표. 비면 prod
#   두 값의 짝이 안 맞으면 아무것도 하지 않는다 — 운영 덤프가 dev/ 로 가거나 그 반대가 되면 안 된다.
#
# --local 은 로컬 시험용이다. 개발 DB 컨테이너(ktc4-postgres)를 대상으로 하고 S3 · 웹훅 없이 돈다.

# cron 의 PATH 는 /usr/bin:/bin 뿐이라 /usr/local/bin 의 aws 를 못 찾는다. 뒤에 붙인다 — 앞에 두면
# 시험이 가짜 docker · aws 를 끼울 수 없다.
PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/docker/.env}"

LOCAL_DIR=""
ENVNAME="local"
CONTAINER=""
BUCKET=""

log() { echo "[$(basename "$0" .sh) $(date '+%F %T')] $*"; }
envval() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d "\"' " || true; }

# 실패를 Discord 로 한 줄 — "[환경] 스크립트: [키] 단계 — 설명". 웹훅이 없거나(로컬) 보내기가 실패해도
# 스크립트의 실패 코드는 그대로 둔다. 웹훅 URL 은 비밀이라 찍지 않는다.
# KEY 는 백업 하나를 가리키는 이름(S3 키 · 로컬 파일 이름)이다. 정해지면 스크립트가 채운다.
KEY=""
alert() {
  local msg
  msg="🔴 [$ENVNAME] $(basename "$0"): ${KEY:+[$KEY] }$1"
  echo "$msg" >&2
  [ -n "${ALERT_WEBHOOK_URL:-}" ] || return 0
  local body
  body="$(printf '%s' "$msg" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
  curl -sS -o /dev/null --max-time 10 -X POST -H 'Content-Type: application/json' \
    --data "{\"embeds\":[{\"color\":16711680,\"description\":\"$body\"}],\"username\":\"infra-alert\",\"allowed_mentions\":{\"parse\":[]}}" \
    "$ALERT_WEBHOOK_URL" || echo "웹훅 전송도 실패" >&2
}

fail() {
  alert "$1"
  exit 1
}

# 🚨 pg_dump · pg_restore · psql 의 stderr 는 화면에 내지 않는다. COPY 오류는 행의 값을 그대로 싣는다 —
# cron 로그 · SSM 출력(→ GitHub Actions, 국외)으로 아이 정보가 나간다. 원문은 스크립트의 임시 폴더
# ($WORK, 주인만 읽음 · 끝나면 지움)에만 받고, 밖에는 단계 · 종료 코드 · 테이블 이름만 낸다.
#   quiet <명령...>   stderr 를 $WORK/stderr 로. 종료 코드는 그대로 돌려준다
#   err_table         방금 실패의 stderr 에 테이블 이름이 있으면 " · 테이블 이름" (영문 · 숫자 · _ 만)
quiet() { "$@" 2> "$WORK/stderr"; }
err_table() {
  local t
  t="$(grep -o 'table "[A-Za-z0-9_]*"' "$WORK/stderr" 2>/dev/null | head -n 1 | tr -d '"' | cut -d' ' -f2 || true)"
  [ -z "$t" ] || printf ' · 테이블 %s' "$t"
}

# 배포와 같은 잠금을 잡는다 — 4GB 서버에서 이미지 빌드와 임시 DB 가 겹치지 않게. 배포(root)와
# cron(ubuntu) 모두 "없으면 0644 로 만들고, 읽기로 연다" — flock 은 읽기로 연 파일도 배타로 잠근다.
# 🚨 만들면서 열지 않는다 — `exec 9>` 도 `flock <파일> 명령` 도 O_CREAT 로 연다. /var/lock 은 재부팅마다 비는 sticky 폴더이고
#    Ubuntu 기본값 fs.protected_regular=2 는 남이 만든 파일을 O_CREAT 로 여는 것을 root 에게도 막는다 —
#    재부팅 뒤 cron 이 먼저 만들면 그 뒤 배포가 전부 Permission denied 로 실패한다 (#773 리뷰).
#    같은 세 줄이 deploy.sh(reset 전에 임시 파일로 돌아 이 파일을 못 부른다)와 with-deploy-lock.sh(Makefile)에 있다.
# 🚨 파일이 0600 이면 다른 사용자가 못 연다. 스크립트가 umask 077 이어도 여기서는 0644 로 만든다.
take_deploy_lock() {
  command -v flock >/dev/null || return 0
  local lock="${DEPLOY_LOCK:-/var/lock/ktc4-deploy.lock}"
  # 만들기 실패는 넘긴다 — 그 사이 배포가 먼저 만들었으면 남의 파일이라 못 만든다. 여는 쪽이 검사다.
  [ -e "$lock" ] || (umask 022 && : > "$lock") 2>/dev/null || true
  exec 9< "$lock" || fail "잠금 파일 $lock 을 열지 못했다 — 소유자 · 권한을 본다 (0644)"
  log "배포 잠금 대기"
  flock -w 1200 9 || fail "20분 동안 배포가 안 끝나 기다리지 못했다"
}

# 대상 정하기. 인자: 로컬 폴더(없으면 서버 모드)
pick_target() {
  if [ -n "${1:-}" ]; then
    LOCAL_DIR="$1"
    CONTAINER="${LOCAL_CONTAINER:-ktc4-postgres}"
    return
  fi
  [ -f "$ENV_FILE" ] || fail "$ENV_FILE 이 없다"
  local suffix appenv
  suffix="$(envval STACK_SUFFIX)"
  appenv="$(envval APP_ENV)"
  ENVNAME="${appenv:-prod}"
  ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-$(envval ALERT_WEBHOOK_URL)}"
  case "$ENVNAME:$suffix" in
    prod: | dev:-dev) ;;
    *) fail "$ENV_FILE 의 APP_ENV='$ENVNAME' 와 STACK_SUFFIX='$suffix' 가 짝이 안 맞는다 (prod 는 비움, dev 는 -dev). 아무것도 하지 않았다" ;;
  esac
  CONTAINER="ktc4${suffix}-postgres-deploy"
  BUCKET="$(envval BACKUP_S3_BUCKET)"
  [ -n "$BUCKET" ] || fail "$ENV_FILE 에 BACKUP_S3_BUCKET 이 없다"
}
