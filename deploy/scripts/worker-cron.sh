#!/usr/bin/env bash
# api worker 의 cron 줄을 서버 crontab 에 맞춘다 (#718). deploy.sh 가 prod 배포에 성공한 뒤 부른다.
# 손으로 거는 cron 은 빠뜨려도 아무 오류가 없다 — profile_recompute 가 안 돌면 강등됐어야 할 기억이
# 확정으로 남는다(#715). 그래서 배포가 건다.
#
#   사용: bash deploy/scripts/worker-cron.sh
#     CRON_USER  줄을 넣을 사용자. 기본 ubuntu — 백업 cron(docs/ops/backup-restore-v1.md)이 있는 그 crontab.
#
# 🚨 SSM 은 root 로 돈다. 그냥 crontab 을 부르면 root 의 crontab 에 들어가 ubuntu 쪽 줄과 겹쳐 두 번 돈다 — -u 로 고른다.
# 🚨 같은 worker 줄(주석 아님)이 이미 있으면 그대로 둔다 — 몇 번 돌려도 줄이 늘지 않고, 손으로 바꾼 시각을 덮지 않는다.
#    주석으로 꺼 둔 줄은 없는 것으로 본다 — 다음 배포가 다시 켠다. 끄려면 아래 LINES 에서 빼고 배포한다.
# 🚨 crontab 을 못 읽으면 쓰지 않는다 — 빈 것으로 보고 쓰면 백업 cron 줄이 지워진다.
# 시각은 서버 시계 기준이고, 서버 시계는 Asia/Seoul 이다 (11-08 확인). prod 만 건다 — dev 는 아직 두지 않는다 (#718).

set -euo pipefail

CRON_USER="${CRON_USER:-ubuntu}"
API=ktc4-api
RUN="docker exec $API uv run --no-sync python -m app.workers"
# 일정 알림 1분마다 · 정리 04시(03시 백업 뒤) · 기억 상태 다시 계산 05시 (apps/api/README.md)
LINES=(
  "* * * * * $RUN.reminder_dispatch >> /var/tmp/ktc4-reminder.log 2>&1"
  "0 4 * * * $RUN.cleanup >> /var/tmp/ktc4-cleanup.log 2>&1"
  "0 5 * * * $RUN.profile_recompute >> /var/tmp/ktc4-profile-recompute.log 2>&1"
)

log() { echo "[worker-cron] $*"; }
ct() { if [ "$(id -un)" = "$CRON_USER" ]; then crontab "$@"; else crontab -u "$CRON_USER" "$@"; fi; }
# 없으면 빈 것, 읽다 실패하면 멈춘다
read_tab() {
  local out
  # stderr 는 실패했을 때만 본다 — 섞으면 경고 한 줄이 crontab 첫 줄로 써진다
  if out="$(ct -l 2>/dev/null)"; then
    printf '%s' "$out"
    return
  fi
  out="$(ct -l 2>&1 >/dev/null || true)"
  [[ "$out" == "no crontab for"* ]] && return
  log "🔴 $CRON_USER 의 crontab 을 못 읽었다 — 아무것도 쓰지 않았다: $out" >&2
  return 1
}
# 주석이 아닌 줄 중 이 worker 를 이 컨테이너에서 돌리는 줄 수. 손으로 넣은 줄의 모양(탭 · docker exec -i ·
# 리다이렉트 없음 · CRLF)이 달라도 같은 줄로 센다 — 못 찾으면 한 줄을 더 넣어 두 번 돈다
count() {
  grep -v '^[[:space:]]*#' <<<"$1" |
    grep -cE "[[:space:]]${API}[[:space:]].*-m[[:space:]]+app\.workers\.$2([[:space:]]|\$)" || true
}
module() { local m="${1#*-m app.workers.}"; echo "${m%% *}"; }

current="$(read_tab)"
new="$current"
for line in "${LINES[@]}"; do
  mod="$(module "$line")"
  [ "$(count "$current" "$mod")" = 0 ] || continue
  new+="${new:+$'\n'}$line"
  log "더함 $mod"
done
[ "$new" = "$current" ] || printf '%s\n' "$new" | ct -

# 다시 읽어 worker 마다 정확히 한 줄인지 본다 — 둘이면 같은 배치가 두 번 돈다
after="$(read_tab)"
bad=0
for line in "${LINES[@]}"; do
  mod="$(module "$line")"
  n="$(count "$after" "$mod")"
  [ "$n" = 1 ] || { log "🔴 $mod 줄이 ${n}개다 — 1개여야 한다. crontab -u $CRON_USER -e 로 고친다"; bad=1; }
done
[ "$bad" = 0 ] && log "worker cron ${#LINES[@]}줄 확인 ($CRON_USER)"
exit "$bad"
