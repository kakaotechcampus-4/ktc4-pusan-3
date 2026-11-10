#!/usr/bin/env bash
# 커밋 하나를 이 checkout 의 환경(dev 또는 prod)에 올리고, 헬스가 안 뜨면 직전 이미지로 되돌린다.
# deploy.yml 이 SSM 으로 부른다. 흐름 · 규칙은 docs/ops/deploy-v1.md.
#
#   사용: cd <checkout> && bash deploy/scripts/deploy.sh <커밋 sha> <dev|prod>
#         bash deploy/scripts/deploy.sh --check-env   .env 검사만 한다 (Makefile 배포 타깃이 먼저 부른다).
#           대상은 COMPOSE_PROJECT_NAME 으로 정한다 — ktc4-dev 면 dev, 아니면 prod. 둘 중 하나와 통째로 맞아야 한다.
#     두 번째 인자는 "이 checkout 이 어느 환경이어야 하는가" 다. deploy/docker/.env 가 그와 맞지 않으면
#     아무것도 하지 않고 멈춘다 — dev .env 에서 한 줄만 빠져도 develop 마이그레이션이 운영 DB 에 걸리기 때문이다.
#     DEPLOY_LOCK  두 환경이 동시에 빌드하지 않게 거는 잠금 파일. 기본값 /var/lock/ktc4-deploy.lock
#                  (적재 make 타깃도 같은 잠금을 쓴다 — Makefile)
#     ALLOW_OLD=1  지금 떠 있는 것보다 옛 커밋을 일부러 올릴 때 (아래 "옛 커밋" 검사를 건너뛴다)
#     SKIP_PREDEPLOY_BACKUP=1  배포 전 백업을 건너뛰고 알림만 보낸다 (backup.sh 가 본다). deploy.yml 을 손으로
#                  돌릴 때의 skip_backup 입력이 켠다 — DB 컨테이너가 죽어 백업이 안 되는데 그걸 고치는 배포용
#
# 🚨 down 을 먼저 하지 않는다. 빌드 · 마이그레이션이 실패하면 옛 컨테이너가 그대로 서비스한다.
# 🚨 DB 는 되돌리지 않는다. 이미지만 직전 태그로 되돌린다 — 그래서 마이그레이션은 직전 코드도 돌 수 있게
#    (expand-only) 짠다. 컬럼 삭제 · 이름 바꾸기는 배포를 두 번에 나눈다.
# 🚨 태그는 커밋 sha 다. 직전 태그 이미지는 지우지 않고 남긴다 — 롤백할 곳이 그것뿐이다.
# 🚨 up 은 늘 --no-build 다. 이미지가 없을 때 compose 가 지금 소스를 구워 그 태그를 붙이면, 롤백이
#    "실패한 코드를 다시 올리기" 가 된다.

set -euo pipefail

ENV_FILE="deploy/docker/.env"
COMPOSE=(docker compose -f deploy/docker/docker-compose.deploy.yml --env-file "$ENV_FILE")
TAG_FILE="deploy/docker/.deployed-tag"
EDGE_NET="ktc4-edge"

log() { echo "[deploy $(date '+%H:%M:%S')] $*"; }
envval() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d "\"' " || true; }

if [ "${1:-}" = --check-env ]; then
  CHECK_ONLY=1
  if [ "$(envval COMPOSE_PROJECT_NAME)" = ktc4-dev ]; then TARGET=dev; else TARGET=prod; fi
else
  SHA="${1:?사용법: deploy.sh <커밋 sha> <dev|prod>}"
  TARGET="${2:?사용법: deploy.sh <커밋 sha> <dev|prod>}"
fi

# ── 이 checkout 이 정말 그 환경인가 — 잠금 · git 보다 먼저, 아무것도 바꾸기 전에 본다 ──
# .env 는 git 밖이라 reset 으로 바뀌지 않는다.
[ -f "$ENV_FILE" ] || { log "🔴 $ENV_FILE 이 없다"; exit 1; }
PROJECT="$(envval COMPOSE_PROJECT_NAME)"
SUFFIX="$(envval STACK_SUFFIX)"
API_URL="$(envval NEXT_PUBLIC_API_BASE_URL)"
PROFILES="$(envval COMPOSE_PROFILES)"
APPENV="$(envval APP_ENV)"
# dev 메모리 상한 네 개 (#780). compose 의 ${X:-0} 은 빈 값도 0(= 상한 없음)으로 바꿔서, 비워 두면 오류 없이 상한이 빠진다.
mem_limits_set() { for s in API WEB DB REDIS; do case "$(envval "${s}_MEM_LIMIT")" in "" | 0) return 1 ;; esac; done; }
EDGE_IP="$(envval API_EDGE_IP)"
case "$TARGET" in
  # 🚨 프로젝트 이름이 비면 compose 파일의 name(ktc4-deploy) = 운영 DB 볼륨으로 떨어진다.
  # profile 이 남으면 dev 가 운영 Caddy(ktc4-caddy) 이름으로 부딪힌다. APP_ENV 는 compose 가 api · web 에
  # 직접 넣어(${APP_ENV:-prod}) apps/api/.env 를 덮는다 — 비면 dev 알림이 [prod] 로 온다.
  # API_EDGE_IP — 비면 prod 주소(.20)를 받아 dev 가 EC2 메타데이터(서버 역할 열쇠)에 닿고, prod api 와 주소가 부딪힌다.
  dev) [ "$PROJECT" = ktc4-dev ] && [ "$SUFFIX" = -dev ] && [[ "$API_URL" == *//api.dev.* ]] &&
    [ -z "$PROFILES" ] && [ "$APPENV" = dev ] && mem_limits_set && [ "$EDGE_IP" = 172.30.0.21 ] ;;
  # web 이미지는 API 주소를 번들에 굽는다 — prod 가 dev API 를 굽거나 그 반대면 막는 건 CORS 하나뿐이다 (#747).
  # edge 가 빠지면 Caddy 를 compose 가 못 본다 — 첫 전환의 down 이 Caddy · 옛 네트워크를 남겨
  # ktc4-edge 를 못 만들고, 운영이 내려간 채 멈춘다.
  # APP_ENV 는 배포 전 백업(backup.sh)의 짝 검사와 같은 규칙 — 다르면 빌드 10분 뒤 백업에서야 멈춘다 (#758).
  prod) { [ -z "$PROJECT" ] || [ "$PROJECT" = ktc4-deploy ]; } && [ -z "$SUFFIX" ] && [[ "$API_URL" != *//api.dev.* ]] &&
    [ "$PROFILES" = edge ] && { [ -z "$EDGE_IP" ] || [ "$EDGE_IP" = 172.30.0.20 ]; } &&
    { [ -z "$APPENV" ] || [ "$APPENV" = prod ]; } ;;
  *) false ;;
esac || {
  log "🔴 $TARGET 배포인데 $ENV_FILE 이 맞지 않는다 — COMPOSE_PROJECT_NAME='$PROJECT' STACK_SUFFIX='$SUFFIX' NEXT_PUBLIC_API_BASE_URL='$API_URL' COMPOSE_PROFILES='$PROFILES' APP_ENV='$APPENV' API_EDGE_IP='$EDGE_IP'"
  log "   dev: ktc4-dev · -dev · https://api.dev.… · profile 비움 · APP_ENV=dev · API_EDGE_IP=172.30.0.21 · API · WEB · DB · REDIS_MEM_LIMIT 네 개(빈 값 · 0 아님)"
  log "   prod: 비움(또는 ktc4-deploy) · 비움 · api.dev 아님 · COMPOSE_PROFILES=edge · APP_ENV 비움 또는 prod · API_EDGE_IP 비움 (docs/ops/deploy-v1.md)"
  exit 1
}
# dev 사이트 basic_auth 계정 (#780) — 두 환경을 다 받는 Caddy 가 prod 에 있어서 prod .env 에만 둔다.
# .env 줄이 아니라 compose 가 해석해 Caddy 에 실제로 넘기는 값을 본다 — 따옴표 · 줄 끝 공백 · 주석 · \r 을
# compose 와 똑같이 푼 값이다 (#808 리뷰). 해시를 따옴표 없이 쓰면 compose 가 해시 속 $… 를 변수로 먹어
# 모양이 깨지고, 비거나 하나만 있으면 Caddyfile 검사가 실패하거나 계정 없는 basic_auth 가 된다 — 여기서 막는다.
# config 출력은 $ 를 $$ 로 적어서 되돌린다. 성공하면 두 줄(사용자 · 해시)을 찍는다.
dev_auth() {
  # shellcheck disable=SC2016 # 파이썬 코드라 셸이 펼치지 않는 게 맞다
  "${COMPOSE[@]}" config --format json | python3 -c '
import json, re, sys
env = json.load(sys.stdin)["services"].get("caddy", {}).get("environment") or {}
user, pw_hash = (str(env.get(k) or "").replace("$$", "$") for k in ("DEV_BASIC_AUTH_USER", "DEV_BASIC_AUTH_HASH"))
if not (re.fullmatch(r"[^\s:]+", user) and re.fullmatch(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", pw_hash)):
    sys.exit(1)
print(user, pw_hash, sep="\n")
'
}
dev_auth_bad() {
  log "🔴 prod 인데 Caddy 가 받을 DEV_BASIC_AUTH_USER · DEV_BASIC_AUTH_HASH 가 없거나 깨졌다 ($ENV_FILE) — 아무것도 올리지 않았다"
  log "   사용자는 공백 · : 없이, 해시는 작은따옴표로 감싼 bcrypt — DEV_BASIC_AUTH_HASH='\$2y\$10\$…' (docs/ops/deploy-v1.md)"
  exit 1
}
if [ -n "${CHECK_ONLY:-}" ]; then
  if [ "$TARGET" = prod ]; then dev_auth >/dev/null || dev_auth_bad; fi
  log "$ENV_FILE = $TARGET 설정과 맞다"
  exit 0
fi

# 4GB 서버에서 dev · prod 가 동시에 이미지를 구우면 메모리가 넘친다. 다른 환경 배포가 끝날 때까지 기다린다.
# 20분 = 다른 쪽 최악(빌드 ~10분 + up 4분 + 롤백 4분). (맥에는 flock 이 없다 — 로컬 시험에서는 잠그지 않는다)
if command -v flock >/dev/null; then
  # 없으면 0644 로 만들고(실패는 넘김) 읽기로 연다. `exec 9>` 로 열면 재부팅 뒤 복원 점검 cron(ubuntu)이 먼저
  # 만든 파일을 root 가 못 연다 — /var/lock 은 sticky 이고 fs.protected_regular=2 다 (backup-lib.sh 의
  # take_deploy_lock · #758). 이 스크립트는 reset 전에 임시 파일로 돌아 그 함수를 불러 쓸 수 없어 같은 줄을 둔다.
  LOCK="${DEPLOY_LOCK:-/var/lock/ktc4-deploy.lock}"
  [ -e "$LOCK" ] || (umask 022 && : > "$LOCK") 2>/dev/null || true
  exec 9<"$LOCK" || { log "🔴 잠금 파일 $LOCK 을 열지 못했다 — 소유자 · 권한(0644)을 본다"; exit 1; }
  log "잠금 대기"
  flock -w 1200 9 || { log "20분 동안 다른 배포가 안 끝났다 — 포기"; exit 1; }
fi

git fetch --quiet origin

PREV="$(cat "$TAG_FILE" 2>/dev/null || echo local)"
# 옛 run 을 Actions 에서 Re-run 하면 그 run 의 sha 가 그대로 온다 — 더 새 버전이 나간 뒤면 운영이 옛 코드로
# 조용히 돌아간다(스키마는 새 것 그대로). 지금 떠 있는 커밋의 조상이면 거부한다. 같은 sha 재배포 · 첫 배포(local)는 통과.
# (브랜치 끝 커밋만 받으면 더 단순하지만, 뒤 커밋의 CI 가 깨졌을 때 멀쩡한 앞 커밋까지 막힌다.)
if [ "$PREV" != local ] && [ "$PREV" != "$SHA" ] && [ "${ALLOW_OLD:-}" != 1 ] &&
  git merge-base --is-ancestor "$SHA" "$PREV" 2>/dev/null; then
  log "🔴 $SHA 는 지금 떠 있는 $PREV 보다 옛 커밋이다 — 옛 run 의 Re-run? 아무것도 바꾸지 않았다"
  log "   정말 옛 커밋으로 돌릴 거면 서버에서 ALLOW_OLD=1 bash deploy/scripts/deploy.sh $SHA $TARGET"
  exit 1
fi

git reset --hard "$SHA"

# Caddy 가 두 환경의 web · api 에 닿는 공용 네트워크. Caddy 고정 IP(172.30.0.10) 가 여기 있다 —
# api 의 FORWARDED_ALLOW_IPS 가 그 주소만 믿는다 (docker-compose.deploy.yml).
# 다른 컨테이너는 .128 위에서만 주소를 받는다 — .10 을 누가 먼저 가져가 Caddy 행세를 할 수 없게.
if ! docker network inspect "$EDGE_NET" >/dev/null 2>&1; then
  log "네트워크 $EDGE_NET 생성"
  docker network create --subnet 172.30.0.0/24 --ip-range 172.30.0.128/25 "$EDGE_NET" >/dev/null
fi

log "$TARGET: 직전 $PREV → 새 $SHA"

export API_IMAGE_TAG="$SHA" WEB_IMAGE_TAG="$SHA"
# 굽지 않는 이미지(caddy · db · redis)가 이 커밋에서 바뀌었으면 여기서 받는다. up 은 --pull never 라,
# 없으면 up 이 새 api · web 을 만든 뒤 멈추고 롤백도 같은 자리에서 멈춘다 (#753 리뷰).
# missing — 이미 있는 태그(redis:7-alpine 같은)는 다시 받지 않는다. 받으면 운영 redis 가 바뀐다.
log "빠진 이미지 받기"
"${COMPOSE[@]}" pull --quiet --ignore-buildable --policy missing
if [ "$TARGET" = prod ]; then
  # 깨진 Caddyfile 은 reload 가 거부해도 다음 Caddy 재시작에 dev · 운영을 같이 내린다. 굽기 전에 막는다.
  CADDY_IMAGE="$("${COMPOSE[@]}" config --images | grep '^caddy:' | head -n 1)"
  log "Caddyfile 검사 ($CADDY_IMAGE)"
  # 배포할 커밋의 compose 파일로 다시 읽는다 — 이 변경이 처음 나가는 배포는 reset 전 파일에 Caddy environment 가 없다.
  AUTH="$(dev_auth)" || dev_auth_bad
  # basic_auth 값을 같이 넘긴다 — 없이 검사하면 실제와 다른 설정(계정 없는 basic_auth)을 검사하게 된다.
  # 값은 -e NAME 으로 환경에서 물려준다 — -e NAME=값 이면 검사가 도는 동안 ps 에 해시가 보인다.
  DEV_BASIC_AUTH_USER="${AUTH%%$'\n'*}" DEV_BASIC_AUTH_HASH="${AUTH#*$'\n'}" \
    docker run --rm -e DEV_BASIC_AUTH_USER -e DEV_BASIC_AUTH_HASH \
    -v "$PWD/deploy/caddy:/etc/caddy:ro" "$CADDY_IMAGE" \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile ||
    { log "🔴 Caddyfile 이 깨졌다 — 아무것도 올리지 않았다"; exit 1; }
fi
log "이미지 빌드"
# 진행 로그를 끈다 — SSM 은 출력 앞부분만 돌려줘서, 빌드 로그가 alembic · up 의 에러를 밀어낸다.
"${COMPOSE[@]}" build --quiet api web
# 배포 전 백업 (#758) — 빌드가 끝난 뒤 · 마이그레이션 직전. 덤프와 마이그레이션 사이에 쓰기가 덜 끼게 여기서 뜬다.
# 실패하면 set -e 로 여기서 멈춘다 — 옛 컨테이너가 그대로 서비스하고 DB 는 건드리지 않았다. 이 마이그레이션을
# 되돌릴 곳이 이 백업뿐이라 멈춘다. backup.sh 는 이 checkout 의 .env 로 대상(dev · prod)을 정하고, 배포가
# 쥔 잠금을 다시 잡지 않는다. DB 가 죽어 백업이 안 되는 복구 배포는 SKIP_PREDEPLOY_BACKUP(머리말)로 넘긴다.
# 10분 상한 — DB 컨테이너는 떠 있는데 postgres 가 멈췄으면 pg_dump 가 SSM 상한(40분)까지 잠금을 쥐어 다른 환경
# 배포까지 막는다(11-06 덤프는 수 초). 잠금(fd 9)은 넘기지 않는다 — 상한에 끊겨도 남은 docker exec 가 쥐지 않게.
# (맥에는 timeout 이 없다 — 로컬 시험에서는 상한 없이 돈다)
log "배포 전 백업"
BACKUP=(bash deploy/scripts/backup.sh predeploy "$SHA")
command -v timeout >/dev/null && BACKUP=(timeout 600 "${BACKUP[@]}")
"${BACKUP[@]}" 9<&- || {
  rc=$?
  [ "$rc" != 124 ] || log "🔴 배포 전 백업이 10분 안에 안 끝났다 — DB 컨테이너를 본다. 아무것도 바꾸지 않았다"
  exit "$rc"
}
log "마이그레이션"
# api 가 아니라 api-task 다 — api 는 ktc4-edge 고정 IP 라, 떠 있는 api 옆에서 run 하면 같은 주소를 두 번 잡아
# "Address already in use" 로 멈춘다 (docker-compose.deploy.yml 의 api-task · 11-09).
"${COMPOSE[@]}" run --rm api-task uv run --no-sync alembic upgrade heads

log "기동 — 헬스체크 통과까지 대기"
# 360 = 기동 240 + 옛 api drain 유예 120 (stop_grace_period · docs/ops/deploy-v1.md §drain, #897 리뷰)
if "${COMPOSE[@]}" up -d --no-build --pull never --wait --wait-timeout 360; then
  echo "$SHA" > "$TAG_FILE"
  log "배포 성공 $SHA"
  # api 가 뜰 때 남기는 "꺼져 있는 기능" 한 줄(app/api/startup_report.py)을 배포 출력에도 보인다 —
  # 서버 로그(journald)에만 있으면 배포한 사람이 못 본다 (#912 리뷰). 설정 이름만 있고 값은 없는 줄이다.
  # 못 읽어도, 그 줄이 없어도 배포는 성공이다.
  "${COMPOSE[@]}" logs --no-color --since 10m api 2>/dev/null | grep "꺼져 있는 기능" | tail -n 1 || true
  if [ "$TARGET" = prod ]; then
    # 서버 메트릭 수집(Alloy · #946). 🚨 감시 도구라 무엇이 실패해도 배포는 성공이다 — 경고만 남기고 넘어간다.
    # 위 `up --wait` 에 안 들어간다(profile monitor) — 들어가면 깨진 config.alloy 가 운영 배포를 롤백시키고,
    # 롤백도 같은 config 를 읽어 같이 실패한다. 이름을 불러 따로 띄우고(--wait 없음) 이미지는 없으면 받는다.
    # --force-recreate — compose 정의가 그대로면 up 은 컨테이너를 두고 가고, Alloy 는 바뀐 config.alloy 를
    # 스스로 다시 읽지 않는다. 그러면 검사만 새 설정으로 하고 옛 설정으로 돈다 (#946 보안 검토 L3).
    # Caddy reload 보다 먼저 — reload 가 실패해 exit 1 로 끝나도 메트릭은 돈다.
    # 5분 상한 — Docker Hub 가 대답 없이 멈추면 실패가 아니라 대기라서, 배포 잠금을 쥔 채 여기서 선다.
    # 첫 배포는 이미지(~640MB)를 받는다. (맥에는 timeout 이 없다 — 로컬 시험에서는 상한 없이 돈다)
    LIMIT=()
    command -v timeout >/dev/null && LIMIT=(timeout 300)
    if out="$(
      img="$("${COMPOSE[@]}" --profile monitor config --images | grep '^grafana/alloy:' | head -n 1)" &&
        ${LIMIT[@]+"${LIMIT[@]}"} docker run --rm -v "$PWD/deploy/alloy:/etc/alloy:ro" "$img" validate /etc/alloy/config.alloy 2>&1 &&
        ${LIMIT[@]+"${LIMIT[@]}"} "${COMPOSE[@]}" up -d --no-build --pull missing --force-recreate alloy 2>&1
    )"; then
      log "Alloy(메트릭 수집) 기동"
    else
      # 검사에서 멈췄으면 떠 있던 Alloy 는 옛 설정으로 계속 돈다 — 다만 재시작되는 순간 깨진 설정을 읽는다.
      log "🟠 Alloy 검사 · 기동 실패 — 배포는 성공이다. 떠 있던 Alloy 는 옛 설정으로 돌고(없으면 메트릭이 빈다), 재시작되면 깨진 설정을 읽는다. deploy/alloy/config.alloy 를 고쳐 다시 배포한다"
      echo "$out" | tail -n 20
    fi
    # Caddyfile 은 up 만으로 다시 읽히지 않는다 (Caddy 설정이 그대로면 컨테이너를 다시 안 만든다).
    # 이번 up 이 Caddy 를 새로 만들었으면 관리 포트(2019)가 몇 초 늦게 열린다 — 리허설에서 바로 부르면
    # connection refused 였다. 몇 번 다시 해 본다.
    reloaded=0
    for _ in 1 2 3 4 5; do
      if out="$(docker exec ktc4-caddy caddy reload --config /etc/caddy/Caddyfile 2>&1)"; then
        reloaded=1
        break
      fi
      sleep 3
    done
    # 초록불로 끝내면 아무도 모른다 — Caddy 는 옛 설정으로 돌지만 다음 재시작에 새 파일을 읽는다.
    [ "$reloaded" = 1 ] || {
      log "🔴 Caddy reload 실패 — 새 버전은 떠 있고 Caddy 는 옛 설정이다. 서버에서 docker logs ktc4-caddy 로 본다"
      echo "$out"
      exit 1
    }
  fi
  # 지금 것과 직전 것만 남기고 이 환경의 옛 태그 이미지를 지운다. 정리가 실패해도 배포는 성공이다.
  for repo in "ktc4${SUFFIX}-api" "ktc4${SUFFIX}-web"; do
    docker image ls "$repo" --format '{{.Tag}}' | while read -r tag; do
      [ "$tag" = "$SHA" ] || [ "$tag" = "$PREV" ] || docker image rm "$repo:$tag" >/dev/null || true
    done || true
  done
  docker image prune -f >/dev/null || true
  exit 0
fi

log "🔴 새 버전 헬스 실패"
"${COMPOSE[@]}" ps -a || true
if ! docker image inspect "ktc4${SUFFIX}-api:$PREV" "ktc4${SUFFIX}-web:$PREV" >/dev/null 2>&1; then
  log "🔴🔴 롤백할 직전 이미지($PREV)가 없다 (첫 배포?) — 새 버전이 실패한 채로 남아 있다. 서버에서 직접 본다"
  exit 1
fi
log "직전 $PREV 로 롤백"
export API_IMAGE_TAG="$PREV" WEB_IMAGE_TAG="$PREV"
if "${COMPOSE[@]}" up -d --no-build --pull never --wait --wait-timeout 360; then
  log "롤백 성공 — $PREV 가 서비스 중. $SHA 는 올라가지 않았다"
else
  log "🔴🔴 롤백도 실패 — 서버에서 직접 본다: docker compose -f deploy/docker/docker-compose.deploy.yml --env-file $ENV_FILE logs --tail 100"
fi
exit 1
