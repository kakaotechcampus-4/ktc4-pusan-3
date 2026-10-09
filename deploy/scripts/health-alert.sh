#!/usr/bin/env bash
# 배포 compose 의 컨테이너 상태가 달라졌을 때만 Discord 웹훅으로 한 줄 보낸다 (멘토 #267 2번).
#
# api 안의 알림(apps/api/app/core/alerts.py)은 api 가 살아 있을 때만 말할 수 있다. 컨테이너가 못 뜨거나
# (배포 실패) redis 가 죽거나 재시작을 반복하는 건 밖에서 봐야 한다 — 서버의 cron 이 1분마다 이걸 돌린다.
# docker 의 healthcheck 는 판정만 하고 아무에게도 말하지 않는다. 이 스크립트가 그 판정을 읽어 통보한다.
#
#   사용: deploy/scripts/health-alert.sh [--dry-run] [compose 파일]
#     compose 파일  기본값: 이 파일 옆의 ../docker/docker-compose.deploy.yml
#     ENV_FILE      웹훅 주소(ALERT_WEBHOOK_URL)를 읽을 파일. 기본값: compose 파일 옆의 .env
#     STATE_FILE    지난번 상태를 적어 두는 파일. 기본값: /tmp/ktc4-health-alert.state
#     ALERT_ENV     메시지 머리의 환경 이름. 기본값: prod
#     --dry-run     웹훅 대신 화면에 찍는다 (상태 파일은 그대로 갱신 — 다음 번엔 달라진 것만 보인다)
#
#   cron (서버, 1분마다 — crontab -e):
#     * * * * * cd /home/ubuntu/ktc4-pusan-3 && deploy/scripts/health-alert.sh >> /var/tmp/ktc4-health-alert.log 2>&1
#
#   로컬 시험 (맥, 개발 DB compose 로): deploy/scripts/health-alert.sh --dry-run deploy/docker/docker-compose.yml
#     → ktc4-postgres 를 멈췄다 켜면서 다시 돌리면 달라진 줄만 찍힌다.
#
# 🚨 지난번과 달라진 컨테이너만 말한다 — 죽어 있는 동안 매분 울리면 아무도 안 본다. 복구도 한 줄.
# 🚨 웹훅 전송이 실패하면 상태를 저장하지 않는다 — 다음 분에 같은 변화를 다시 보낸다. 조용히 잃지 않는다.
# 🚨 메시지엔 컨테이너 이름과 상태뿐이다. 웹훅 URL 은 비밀이라 어디에도 찍지 않는다.
# bash 3.2(맥 기본)에서도 돌게 연관 배열을 쓰지 않는다 — 로컬에서 --dry-run 으로 시험하기 위해서다.

set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
  shift
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${1:-$HERE/../docker/docker-compose.deploy.yml}"
ENV_FILE="${ENV_FILE:-$(dirname "$COMPOSE")/.env}"
STATE_FILE="${STATE_FILE:-/tmp/ktc4-health-alert.state}"
ALERT_ENV="${ALERT_ENV:-prod}"

# 웹훅 주소 — 환경변수가 없으면 env 파일에서. 값은 변수에만 두고 절대 echo 하지 않는다.
if [ -z "${ALERT_WEBHOOK_URL:-}" ] && [ -f "$ENV_FILE" ]; then
  # 줄이 없으면 grep 이 1 을 내는데, set -e 때문에 여기서 조용히 죽지 않게 || true
  ALERT_WEBHOOK_URL="$(grep -E '^ALERT_WEBHOOK_URL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d "\"'" || true)"
fi
if [ "$DRY_RUN" -eq 0 ] && [ -z "${ALERT_WEBHOOK_URL:-}" ]; then
  echo "ALERT_WEBHOOK_URL 이 비어 있다 ($ENV_FILE). 보낼 곳이 없다" >&2
  exit 1
fi

# 지금 상태 — "이름 실행상태 헬스" 한 줄씩, 이름순. 헬스체크가 없는 컨테이너는 "-".
# docker 자체가 안 되면 "컨테이너가 사라졌다" 로 오해하지 않게 그냥 실패한다.
if ! ids="$(docker compose -f "$COMPOSE" --env-file "$ENV_FILE" ps -aq)"; then
  echo "docker compose ps 실패 — docker 가 안 뜬 건지 compose 파일이 틀린 건지 본다" >&2
  exit 1
fi
current=""
if [ -n "$ids" ]; then
  # shellcheck disable=SC2086
  current="$(docker inspect --format \
    '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' \
    $ids | sed 's#^/##' | sort)"
fi
previous=""
if [ -f "$STATE_FILE" ]; then
  previous="$(cat "$STATE_FILE")"
fi

# 달라진 것만 — 이름으로 짝을 지어 "전 → 후". 지난번엔 있었는데 지금 없는 건 "사라짐".
changes=""
while IFS= read -r line; do
  [ -z "$line" ] && continue
  name="${line%% *}"
  now="${line#* }"
  before="$(printf '%s\n' "$previous" | awk -v n="$name" '$1 == n { $1 = ""; sub(/^ /, ""); print; exit }')"
  [ "$before" = "$now" ] && continue
  changes="${changes}[$ALERT_ENV] $name: ${before:-없음} → $now"$'\n'
done <<< "$current"
while IFS= read -r line; do
  [ -z "$line" ] && continue
  name="${line%% *}"
  before="${line#* }"
  if ! printf '%s\n' "$current" | awk -v n="$name" '$1 == n { found = 1 } END { exit !found }'; then
    changes="${changes}[$ALERT_ENV] $name: $before → 사라짐"$'\n'
  fi
done <<< "$previous"

if [ -z "$changes" ]; then
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  printf '%s' "$changes"
else
  # JSON 글자 처리 — 백슬래시 · 따옴표 · 줄바꿈. 내용은 컨테이너 이름과 상태뿐이라 그 셋이면 된다.
  content="$(printf '%s' "$changes" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | awk 'BEGIN { ORS = "\\n" } { print }' | sed 's/\\n$//')"
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
    --data "{\"content\":\"$content\",\"allowed_mentions\":{\"parse\":[]}}" \
    "${ALERT_WEBHOOK_URL}?wait=true" || true)"
  case "$code" in
    2*) ;;
    *)
      echo "웹훅 전송 실패 HTTP ${code:-없음} — 상태를 저장하지 않는다, 다음 번에 다시 보낸다" >&2
      exit 1
      ;;
  esac
fi

printf '%s\n' "$current" > "$STATE_FILE"
