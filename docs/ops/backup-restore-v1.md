# 운영 DB 백업 · 복원 (v1)

> #758 — PR 1(#773) 백업 · PR 2(#812) 삭제 이력 · 처리방침 2026-11-08 판 · PR 3 이 문서.
> 코드가 정본이다. 이 문서는 흐름 · 절차 · 연습 기록만 적는다.
> `deploy/scripts/backup.sh` · `restore-check.sh` · `backup-lib.sh` · `with-deploy-lock.sh` · `.github/workflows/backup-watch.yml` ·
> `deploy/scripts/deploy.sh`(배포 전 백업) · `apps/api/scripts/deletion_log.py`(재적용) · `apps/api/app/domains/deletion_log/`.

처리방침이 약속한 것 — 백업 사본은 운영 DB 와 따로, 접근을 제한한 상태로 최대 30일만 둔다(5장 ③).
복원할 때는 접근을 열기 전에 삭제 이력을 먼저 다시 적용해, 이미 지운 정보와 철회한 동의가 되살아나지 않게
한다(5장 ③). 운영 DB 까지 잃어 이력이 없으면 마지막 백업 뒤에 지운 것이 되살아날 수 있고, 그때는 서비스
안에 알리고 다시 삭제를 요청하도록 안내한다(5장 ③ 단서).

## 1. 무엇이 어디에 언제

| | 언제 | 어디에 | 얼마나 |
| --- | --- | --- | --- |
| 매일 백업 (`daily`) | prod 03:00 · dev 03:05 (서버 cron) | S3 서울 버킷 `<prod\|dev>/daily/<UTC 시각>.dump` | 28일 (버킷 수명 주기) |
| 배포 전 백업 (`predeploy`) | 배포마다 빌드 뒤 · 마이그레이션 앞 (`deploy.sh`) | `<prod\|dev>/predeploy/<UTC 시각>-<sha 앞 12자>.dump` | 28일 |
| 복원 점검 | prod 03:00 매일 백업 바로 뒤 (같은 cron 줄) | 서버의 임시 컨테이너 — 네트워크 없음 · 768MB · 끝나면 지움 | — |
| 서버 밖 감시 | 매일 04:30 KST (`backup-watch.yml`) | 버킷 목록만 본다 | — |
| 삭제 이력 (`deletion_log`) | 지울 때마다 DB 트리거 | 운영 DB 안 | 35일 (정리 배치) |

- 덤프는 EC2 에서 S3 로 바로 간다. GitHub Actions 는 SSM 명령만 보낸다 — 러너를 거치면 국외 이전이다.
- 버킷은 서울 · 퍼블릭 차단 · SSE-S3 · 버전 관리 끔. 버킷 정책이 EC2 역할에만 올리기 · 읽기 · 목록을 주고, 그 밖의
  모두(SSO · `ktc-github-deploy`)에게 객체 읽기 · 쓰기 · 지우기를 거부한다. 지우기는 수명 주기만 한다.
- 수명 주기는 날짜 단위로 비동기로 지운다 — 실제로는 28일에 하루쯤 더해진다. 약속한 30일 안이다.
- 버킷 이름은 저장소에 쓰지 않는다 — 서버 `.env` 의 `BACKUP_S3_BUCKET`, Actions secrets 의 `BACKUP_S3_BUCKET`.
- 각 checkout 은 자기 스택만 백업한다(`.env` 의 `APP_ENV` · `STACK_SUFFIX` 짝). prod 백업을 dev 에 복원하지 않는다.
- 덤프 · 복원 오류의 원문은 밖으로 내지 않는다 — COPY 오류는 행 값을 싣는다. 출력은 키 · 크기 · 테이블 수 ·
  테이블 이름까지다.

## 2. 서버 준비 (한 번)

1. 두 checkout 의 `deploy/docker/.env` 에 `BACKUP_S3_BUCKET` · `ALERT_WEBHOOK_URL` (11-07 넣음).
2. ubuntu 사용자의 `crontab -e` — 서버 시계는 `Asia/Seoul` 이다(11-08 확인).
   ```
   0 3 * * * cd /home/ubuntu/ktc4-pusan-3 && { bash deploy/scripts/backup.sh daily; bash deploy/scripts/restore-check.sh; } >> /var/tmp/ktc4-backup.log 2>&1
   5 3 * * * cd /home/ubuntu/ktc4-pusan-3-dev && bash deploy/scripts/backup.sh daily >> /var/tmp/ktc4-backup-dev.log 2>&1
   ```
   - 🚨 삭제 이력의 35일 정리는 정리 배치(`app.workers.cleanup`)가 한다. 그 cron 줄(04시)과 기억 다시 계산(05시)은
     `apps/api/README.md` 에 있다 — 빠져 있으면 이력이 35일 넘게 남아 처리방침 1장 ③ 과 어긋난다.
3. `backup-watch.yml` 의 감시 대상(`ENVS`)에는 매일 백업 cron 을 건 환경만 넣는다 — 안 건 환경을 넣으면 매일
   "하나도 없다" 가 운다. dev 는 11-09 에 cron 을 걸고 손으로 한 번 돌린 뒤 `dev` 로 바꿨다(#895). prod 는
   #773 이 main 으로 배포된 뒤 위 03:00 줄을 걸고 `backup.sh daily` 를 손으로 한 번 돌려 `prod dev` 로 늘린다.
4. 첫 확인 — 머지 뒤 첫 배포의 Actions 출력에 `[backup …] 올림 prod/predeploy/…` 줄, 다음 날
   `/var/tmp/ktc4-backup.log` 의 `복원 통과` 줄, 04:30 감시(손으로 돌리려면 Actions › 백업 감시 › Run workflow).

## 3. 알림이 오면

Discord 한 줄 — `🔴 [환경] 스크립트: [키] 단계 — 설명`. 성공은 로그에만 남는다.

| 설명에 보이는 것 | 뜻 · 볼 것 |
| --- | --- |
| `pg_dump 실패` | DB 컨테이너가 떠 있는지. 배포 전 백업이면 그 배포는 아무것도 바꾸지 않고 멈췄다 — 4장 |
| `배포 전 백업이 10분 안에 안 끝났다` (Actions 출력) | DB 컨테이너는 있는데 postgres 가 응답하지 않는다. 배포는 멈췄다 |
| `덤프 목차 읽기 실패` · `덤프에 테이블 데이터가 없다` (backup) | 뜬 덤프가 깨졌거나 비었다 — 올리지 않았다. 배포 전 백업이면 배포도 멈췄다 |
| `S3 업로드 실패` · `백업 목록 읽기 실패` · `백업 받기 실패` | EC2 역할 · 버킷 정책 · 서버 네트워크 |
| `… 짝이 안 맞는다` · `BACKUP_S3_BUCKET 이 없다` | 그 checkout 의 `.env` |
| `복원 실패 … · 테이블 X` (restore-check) | 그 백업으로는 되살릴 수 없다. 다음 날도 그러면 덤프 쪽(디스크 · 버전)을 본다 |
| `시간 상한 N초에 걸려 끊었다 (exit 124 · 137) — <명령>` (restore-check) | 그 단계가 시간 상한(S3 목록 60초 · 받기 5분 · docker inspect · run 60초 · 임시 DB 준비 60초(확인 한 번 5초) · 복원 10분 · psql 1~2분)에 걸려 끊겼다. 137 은 TERM 을 무시해 10초 뒤 KILL 로 끊긴 것이다. 임시 DB 는 지웠고 배포 잠금도 풀렸다 — 다음 날도 그러면 서버 부하 · 네트워크를 본다 |
| `임시 DB 가 60초 안에 뜨지 않았다` · `임시 DB 컨테이너 띄우기 실패` (restore-check) | 서버 메모리 · docker. 임시 DB 는 지웠다 — 다음 날 결과를 본다 |
| `잠금 파일 … 을 열지 못했다` | `/var/lock/ktc4-deploy.lock` 의 소유자 · 권한(0644) — `backup-lib.sh` 의 take_deploy_lock 머리말 |
| `가장 최근 매일 백업이 N시간 …` · `하나도 없다` | 서버 cron 이 멈췄다(배포 전 백업은 새것이어도 따로 본다). `crontab -l` · 로그 파일 |
| `20분 동안 배포가 안 끝나 기다리지 못했다` | 복원 점검이 잠금을 20분 기다리다 포기했다 — 배포가 그만큼 걸렸거나, 누가 5-2 로 잠금을 쥐고 있었다 |
| `배포 전 백업을 건너뛰었다 (skip_backup …)` | 누군가 손으로 건너뛰었다 — Actions 경고에 실행한 사람이 있다 |
| 감시(`backup-watch`)만 왔다 | 서버는 살아 있는데 cron 이 안 돈다. 서버가 죽었으면 UptimeRobot 이 먼저 운다 |

## 4. 배포 전 백업을 건너뛸 때

배포 전 백업이 실패하면 배포는 멈춘다 — 마이그레이션을 되돌릴 곳이 이 백업뿐이다. DB 컨테이너가 죽어 백업이
안 되는데 **그걸 고치는 배포**거나, **새 스택의 첫 배포**(DB 가 아직 없거나 테이블이 0개)일 때만
Actions › 배포 › Run workflow 에서 `skip_backup` 을 켠다. 건너뛰면 Discord 와 Actions 경고에 남고, 그 배포의
마이그레이션을 되돌릴 곳은 마지막 매일 백업이다. push 배포는 늘 백업한다.

## 5. 복원

### 5-1. 무엇을 할지 고르기

| 상황 | 할 것 |
| --- | --- |
| 운영 DB 는 살아 있는데 **지우지 않은** 데이터가 망가졌다 (잘못된 UPDATE · 마이그레이션이 값 · 스키마를 망가뜨림) | **5-2 전체 복원** — 지금 DB 에서 이력을 빼고, 백업으로 되돌린 뒤 이력을 다시 적용 |
| 잘못 **지워졌다** (삭제 버그 · 행을 지우는 마이그레이션 · 손으로 잘못 지움) | 몇 행이면 먼저 **임시 DB 에서 그 행만 꺼내 되살리기**를 본다(복원 점검처럼 임시 컨테이너에 복원 — 백업 뒤에 쌓인 기록을 잃지 않는다). 넓으면 **5-3**. 🚨 5-2 로 하면 안 된다 — 잘못 지운 것도 이력에 남아 있어 다시 적용이 또 지운다 |
| 운영 DB 자체를 잃었다 (볼륨 · 서버) | **5-4** — 이력이 없다 |

🚨 복원하면 **백업 시각 뒤에 새로 쓴 기록은 사라진다**(삭제 이력은 지운 것만 남긴다 — 동의 · 철회 · 동의 증빙 ·
위치 이용 확인자료만 예외로 되살린다). 복원 전에 PM 과 공지 문구를 정한다. api · web 을 내리면 헬스 알림이
Discord 로 운다 — 팀에도 미리 알린다.

### 5-2. 전체 복원

SSM 세션에서 `sudo -i` 한 뒤. **한 단계가 실패하면 다음 단계로 가지 않는다** — 단계마다 확인 줄을 두고, DB 를
지우는 ③ 은 한 덩어리로 이어 끝까지 가야 `R=ok`, ④ 는 `A=ok` 다. ④ · ⑤ · ⑥ 은 그 값이 없으면 돌지 않는다.
🚨 03:00 ~ 03:30 은 피한다. 매일 백업(`backup.sh daily`)은 잠금을 잡지 않아 복원 중인 DB 를 뜰 수 있다.

```bash
# 0) 세션이 끊겨도 이어지게 tmux 안에서(끊기면 `sudo tmux attach -t restore`), 배포 · 복원 점검과 겹치지 않게
#    배포 잠금을 쥔 셸로 들어간다. 이 셸을 나가면 잠금이 풀린다. 이 셸 안에서는 make deploy-migrate 를 쓰지
#    않는다 — 같은 잠금을 다시 기다려 20분 멈춘다.
tmux new -s restore                  # 없으면 apt install tmux
cd /home/ubuntu/ktc4-pusan-3         # dev 복원이면 /home/ubuntu/ktc4-pusan-3-dev — 아래 값이 이 checkout 의 .env 를 따른다
bash deploy/scripts/with-deploy-lock.sh bash
[ -e /proc/$$/fd/9 ] && echo "잠금 쥠"   # 안 나오면 잠금 없는 셸이다(20분 못 잡음) — exit 하고 다시
umask 077
TAG=$(cat deploy/docker/.deployed-tag); export API_IMAGE_TAG=$TAG WEB_IMAGE_TAG=$TAG
DC="docker compose -f deploy/docker/docker-compose.deploy.yml --env-file deploy/docker/.env"
TASK="$DC run --rm -T api-task uv run --no-sync"   # 일회용 작업은 api-task (docs/ops/deploy-v1.md)
envval() { grep -E "^$1=" deploy/docker/.env | tail -n 1 | cut -d= -f2- | tr -d "\"' "; }
ENV=$(envval APP_ENV); ENV=${ENV:-prod}; BUCKET=$(envval BACKUP_S3_BUCKET); DB=$($DC ps -q db)
echo "환경 $ENV · DB 컨테이너 ${DB:-없음}"   # S3 경로가 이 환경을 따른다 — prod 백업이 dev 에 들어가지 않는다
LOG=/var/tmp/deletion-log.jsonl; APPLY=$LOG; DUMP=/var/tmp/restore.dump; R=; A=   # 5-3 이면 APPLY=$LOG.keep

# ① 서비스를 막는다 — 막기 전에 빼면 그 사이에 지운 것이 파일에도 백업에도 없다
$DC stop api web && [ -z "$($DC ps -q api web)" ] && echo "서비스 내림"

# ② 지금 DB 의 이력을 뺀다 — "이력 N줄" 이 나와야 한다. 비었거나 실패면 멈춘다
$TASK python -m scripts.deletion_log export > "$LOG" && [ -s "$LOG" ] && echo "이력 $(wc -l < "$LOG")줄"
#    지우기 전에 지금 DB 를 떠 둔다 — 백업 뒤에 쓴 기록의 유일한 사본이다. S3 가 안 되면 서버 파일로.
#    되돌리려면 이 덤프로 ③ 의 복원만 하고 ④ 는 건너뛴다
{ bash deploy/scripts/backup.sh predeploy pre-restore ||
  { docker exec "$DB" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /var/tmp/pre-restore.dump &&
    docker exec -i "$DB" pg_restore --list < /var/tmp/pre-restore.dump > /dev/null; }; } && echo "지금 DB 를 떴다"

# ③ 백업을 고른다
aws s3 ls "s3://$BUCKET/$ENV/daily/"; aws s3 ls "s3://$BUCKET/$ENV/predeploy/"
KEY="$ENV/daily/<고른 파일 이름>"     # 배포 전 백업이면 $ENV/predeploy/…
#    받기 → 덤프 읽기 확인 → DB 비우기 → 복원 → "<alembic 버전>|<표 수>" → 이력 순번 비켜 두기(reserve, 늘 $LOG
#    전부로) → 마이그레이션. 한 덩어리로 붙인다 — 한 줄이 실패하면 뒤는 돌지 않고, 끝까지 가야 R=ok 다.
#    DB 는 덤프를 읽을 수 있을 때만 지운다. 오류 원문에는 행 값이 실릴 수 있다 — 화면 밖에 붙이지 않는다
aws s3 cp "s3://$BUCKET/$KEY" "$DUMP" --only-show-errors &&
  docker exec -i "$DB" pg_restore --list < "$DUMP" > /dev/null &&
  docker exec "$DB" sh -c 'dropdb -U "$POSTGRES_USER" --maintenance-db=postgres "$POSTGRES_DB" &&
    createdb -U "$POSTGRES_USER" --maintenance-db=postgres "$POSTGRES_DB"' &&
  docker exec -i "$DB" sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges \
    --exit-on-error --single-transaction' < "$DUMP" &&
  docker exec "$DB" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT
    (SELECT string_agg(version_num, chr(44)) FROM alembic_version),
    (SELECT count(*) FROM pg_tables WHERE schemaname = current_schema())"' &&
  $TASK python -m scripts.deletion_log reserve < "$LOG" &&
  $DC run --rm api-task uv run --no-sync alembic upgrade heads &&
  R=ok; echo "복원: ${R:-실패 — 멈춘다. 서비스는 내린 채 원인을 보고 ③ 을 다시}"

# ④ 이력을 다시 적용한다 — --dry-run 의 건수를 먼저 보고, 그다음 apply. 끝까지 가야 A=ok 다
[ "$R" = ok ] && $TASK python -m scripts.deletion_log apply --dry-run < "$APPLY"
[ "$R" = ok ] && $TASK python -m scripts.deletion_log apply < "$APPLY" &&
  A=ok; echo "적용: ${A:-실패 — 멈춘다. 서비스는 내린 채 원인을 본다}"

# ⑤ 정리 배치 · 기억 다시 계산 → ⑥ 서비스를 연다(--wait 가 헬스체크까지 기다린다)
[ "$A" = ok ] && $TASK python -m app.workers.cleanup && $TASK python -m app.workers.profile_recompute &&
  $DC up -d --no-build --pull never --wait api web && echo "서비스 열림"

# ⑦ 서비스를 확인한 뒤 받은 덤프 · 빼 둔 이력을 지운다 — 이력에는 구분 값과 동의 행 사본이 있다
shred -u "$LOG" "$DUMP"   # 5-3 이면 "$LOG.keep" "$LOG.after", 서버에 떴으면 /var/tmp/pre-restore.dump 도
exit                      # 잠금을 푼다
```

- `dropdb` 는 접속이 남아 있으면 실패한다 — ① 에서 api · web 이 내려갔는지 본다. 매분 도는 일정 알림 cron
  (`docker exec ktc4-api …`)은 api 가 내려간 동안 실패 줄만 남긴다.
- `reserve` 를 빠뜨려도 복원과 apply 사이에 이력이 안 생겼으면 그대로 통과한다. `upgrade heads` 가 이력을
  남겼으면(행을 지우는 마이그레이션) ④ 가 "N번 이력이 복원본의 같은 id 와 다르다" 로 멈춘다 — ③ 의 복원부터 다시.
- ④ 가 출력한 아이 id 는 백업 뒤에 알레르기 · 건강 기록을 지운 아이다 — 복원으로 그 뒤 다시 넣은 기록이 사라졌을
  수 있어, 보호자에게 알레르기 정보를 다시 확인해 달라고 따로 알린다(승인 게이트 ㉡ — 확정은 사람이 한다).
- 로그인 세 표와 알림 기기는 ④ 가 비운다 — **전원이 다시 로그인한다.** 앱의 알림 기기는 로그인하면 다시 등록된다.
- 복원하는 동안 배포가 오면 잠금을 20분 기다리다 실패한다. 끝난 뒤 다시 돌린다.

### 5-3. 사고 복원 — 잘못 지운 것을 살리려고 되돌릴 때

5-2 와 같고, ② 의 파일에서 **사고 시각 뒤의 `delete` · `status` 이력 중 보호자가 지운 것만 남기고** 적용한다.
트리거는 보호자가 지운 것과 버그가 지운 것을 가르지 못한다 — 앱 로그 · 사고 범위로 가른다.
🚨 `insert` 이력(동의 · 철회 · 동의 증빙 · 위치 이용 확인자료)은 빼지 않는다 — 빼면 철회한 동의가 동의 상태로
돌아가고 보관할 증빙이 사라진다.

② 뒤 · ③ 앞에, 같은 잠금 셸에서:

```bash
APPLY=$LOG.keep
# 사고 시각(UTC) 앞의 것과 insert 는 모두 .keep 에, 그 뒤의 delete · status 는 .after 에
python3 - "$LOG" 2026-11-20T03:00:00+00:00 <<'EOF'
import json, sys
from datetime import datetime
path, cut = sys.argv[1], datetime.fromisoformat(sys.argv[2])
keep, later = open(path + ".keep", "w"), open(path + ".after", "w")
for line in open(path):
    e = json.loads(line)
    out = keep if e["action"] == "insert" or datetime.fromisoformat(e["deleted_at"]) < cut else later
    out.write(line)
EOF
# .after 에서 보호자가 지운 줄만 .keep 에 옮긴다 — 줄 순서는 상관없다 (apply 가 id 순으로 정렬한다).
# reserve 는 $LOG 전부로 한다 — 뺀 이력의 id 까지 비켜 두게. apply 만 $APPLY(.keep) 로.
```

### 5-4. 운영 DB 자체를 잃었을 때

이력도 같이 잃었다. 5-2 의 0) · ① 뒤에 아래를 하고 ⑤ · ⑥ 으로 간다 — ② · `reserve` 는 없다. DB 컨테이너가 없으면
`$DC up -d db && DB=$($DC ps -q db)` 로 먼저 띄운다. ④ 대신 빈 입력으로 apply 를 한 번 돌린다 — 적용할 이력은
없어도 로그인 세 표 · 알림 기기는 비운다(로그아웃 · 탈퇴한 세션과 기기가 백업에서 되살아나지 않게).

```bash
aws s3 ls "s3://$BUCKET/$ENV/daily/"; KEY="$ENV/daily/<고른 파일 이름>"
aws s3 cp "s3://$BUCKET/$KEY" "$DUMP" --only-show-errors &&
  docker exec -i "$DB" pg_restore --list < "$DUMP" > /dev/null &&
  docker exec "$DB" sh -c 'dropdb -U "$POSTGRES_USER" --maintenance-db=postgres --if-exists "$POSTGRES_DB" &&
    createdb -U "$POSTGRES_USER" --maintenance-db=postgres "$POSTGRES_DB"' &&
  docker exec -i "$DB" sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges \
    --exit-on-error --single-transaction' < "$DUMP" &&
  $DC run --rm api-task uv run --no-sync alembic upgrade heads &&
  $TASK python -m scripts.deletion_log apply < /dev/null &&   # "적용했다. 로그인 세 표도 비웠다"
  A=ok; echo "복원: ${A:-실패 — 멈춘다}"
```

마지막 백업 뒤에 지운 것이 되살아날 수 있으므로 처리방침 5장 ③ 단서대로 **서비스 안에 그 사실을 알리고,
다시 삭제를 요청할 수 있게 안내한다**(문구는 PM). 서버까지 잃었으면 `docs/ops/deploy-v1.md` 의 서버 준비부터 한다.

## 6. 연습 기록

**11-09 로컬** — 연습용으로 따로 띄운 Postgres 컨테이너(`pgvector/pgvector:0.8.6-pg18`, 운영과 같은 이미지)에 가짜 보호자 ·
아이 두 명 · 기록 · 알레르기 · 로그인 세션을 넣고 `backup.sh --local` → `restore-check.sh --local`(통과, 테이블 48개)
→ 백업 뒤에 아이 하나 삭제(CASCADE) · 기록 삭제 · 알레르기 삭제 · 서비스 동의 철회 · 새 아이 등록 → 이력 5건 export
→ DB 를 지우고 백업으로 복원 → `reserve` → `upgrade heads` → `apply --dry-run` → `apply` → 정리 배치 · 기억 다시 계산.

| | 복원 직후 (백업 시각) | 재적용 뒤 |
| --- | --- | --- |
| 백업 뒤 지운 아이 · 그 아이의 기록 | 1 · 1 (되살아남) | 0 · 0 |
| 백업 뒤 지운 기록 · 알레르기 상태 | active · active | deleted · retracted |
| 백업 뒤 철회한 동의 | 없음 | 있음 |
| 백업 뒤 만든 아이 | 없음 | 없음 (백업 뒤 추가분은 잃는다) |
| 로그인 세션 | 1 | 0 |

- `--dry-run` 은 아무것도 바꾸지 않았고, 두 번째 `apply` 는 0건이었다. 알레르기를 다시 회수한 아이 id 1개가 출력됐다.
- 덤프 214KB · 0.3초, 복원 0.3초.
- 처음에 `dropdb -d postgres` 로 잘못 불러 복원 없이 넘어갔는데, 뒤 단계가 그대로 돌았다 — 그래서 5-2 는 단계를
  `&&` 한 덩어리로 잇고(`R=ok` · `A=ok`) ③ 에서 `<alembic 버전>|<표 수>` 를 보게 했다. 5-2 의 복원 명령(`--list` 확인 · `--single-transaction` ·
  확인 쿼리)과 5-4 의 `apply < /dev/null`(로그인 표를 비운다)은 같은 컨테이너에서 그대로 돌려 봤다. 앞 몇 KB 만 남긴
  깨진 덤프는 `--list` 에서 멈춘다.

**아직 하지 않은 것** — 서버 · 실제 S3 백업으로 5-2 를 처음부터 끝까지 dev 에서 한 번. 백업이 운영에서 돌기
시작한 뒤(#773 머지 · cron 등록) 하고 여기에 적는다.

## 7. 한계 · 다음에 볼 것

- 백업 뒤에 새로 쓴 기록은 복원하면 잃는다(매일 백업이면 최대 하루). 더 줄이려면 시점 복원(WAL 아카이빙)이 필요하다.
- 삭제 이력이 생기기 전(#812 의 마이그레이션 `de2051c89680` 이 그 환경에 배포되기 전)에 뜬 백업은 5-2 로 복원하지
  않는다 — 그 백업에는 이력 표가 없어 절차가 그대로 맞지 않고, 그 전에 지운 것은 이력에도 없어 되살아난다.
  ③ 에서 백업 날짜로 가린다. 그 배포 뒤 28일이 지나면 그런 백업은 수명 주기로 사라진다.
- 복원 점검은 스크립트 통째가 아니라 멈출 수 있는 단계마다 시간 상한을 건다. 잠금 fd 가 자식 프로세스에 넘어가서
  통째로 `timeout` 을 씌우면 풀리지 않는다 — 단계가 끊기면 fail → cleanup → 종료로 풀린다(Linux 에서 끊긴 뒤
  1초 안에 다른 쪽이 잠금을 얻는 것을 확인했다). 상한을 다 더하면 20분을 넘을 수 있어 그날 배포 하나가 기다리다
  실패할 수는 있지만, 끝없이 서 있지는 않는다.
- dev 는 복원 점검을 하지 않는다(4GB 서버 — 임시 DB 를 하루 두 번 띄우지 않는다).
- `.env` 짝 검사가 `backup-lib.sh` 와 `deploy.sh` 두 곳에 있다(cron 은 `deploy.sh` 없이 돈다). 규칙은 같게 맞춰 두었다.
