# 배포 v1 — CI 게이트 · 자동 롤백 · dev/prod 분리

담당: 김명성 · 작성일: 2026-11-06

## 한눈에

```
develop push ─┐                                    ┌─ dev  (dev.icatch.ai.kr · api.dev.icatch.ai.kr)
              ├─ API CI + Web CI ─ 통과 ─ deploy.sh ┤
main push ────┘        │                           └─ prod (icatch.ai.kr · api.icatch.ai.kr)
                    빨간불 → 배포 안 함
deploy.sh: 빌드 → 마이그레이션 → up --wait(헬스) ─ 실패 → 직전 이미지로 up → 워크플로 빨간불
```

- 서버는 **EC2 한 대**(t3.medium · 4GB)다. 그 위에 compose 프로젝트 두 개가 따로 뜬다.
  DB · Redis · api · web 이 환경마다 하나씩 있고 데이터는 섞이지 않는다.
- Caddy(80/443)는 서버에 하나뿐이라 prod 프로젝트에만 뜨고, 두 환경을 함께 받는다.
  두 환경의 web · api 는 공용 네트워크 `ktc4-edge` 로 Caddy 에 닿는다.

| | prod | dev |
| --- | --- | --- |
| 브랜치 | `main` | `develop` |
| checkout | `/home/ubuntu/ktc4-pusan-3` | `/home/ubuntu/ktc4-pusan-3-dev` |
| compose 프로젝트 | `ktc4-deploy` (이름 유지 — DB 볼륨) | `ktc4-dev` |
| 컨테이너 | `ktc4-api` · `ktc4-web` · … | `ktc4-dev-api` · `ktc4-dev-web` · … |
| `deploy/docker/.env` | `COMPOSE_PROFILES=edge` | `COMPOSE_PROJECT_NAME=ktc4-dev` · `STACK_SUFFIX=-dev` · `APP_ENV=dev` |
| GitHub Environment | `prod` | `dev` |

## 롤백 — 무엇이 되돌아가고 무엇이 안 되돌아가나

`deploy/scripts/deploy.sh` 가 한다. 판정은 compose 의 `up --wait` 다. 각 서비스의 healthcheck 가
healthy 가 될 때까지 기다리고, 안 되면(최대 240초) 실패로 본다.

| 실패 지점 | 결과 |
| --- | --- |
| CI | 배포하지 않는다 |
| 굽지 않는 이미지(caddy · db · redis) 받기 · Caddyfile 검사(prod) | 여기서 멈춘다. 아무것도 바꾸지 않았다 |
| 이미지 빌드 · 마이그레이션 | 여기서 멈춘다. `down` 을 먼저 하지 않으므로 **옛 컨테이너가 그대로 서비스한다** (db · redis 이미지가 바뀐 커밋이면 마이그레이션 `run` 이 그 둘을 먼저 새로 띄운다) |
| Caddy reload (prod) | 새 버전은 떠 있고 Caddy 는 옛 설정이다. 워크플로는 빨간불 — 서버에서 고친다 |
| 지금 떠 있는 것보다 옛 커밋 (옛 run 의 Re-run) | 아무것도 바꾸지 않고 멈춘다. 일부러 되돌릴 때만 서버에서 `ALLOW_OLD=1` |
| 새 컨테이너 헬스 | 직전에 성공한 태그(`deploy/docker/.deployed-tag`)로 다시 `up --wait`. 워크플로는 빨간불 |

- 이미지 태그는 **커밋 sha** 다. 지금 것과 직전 것 두 개만 남기고 나머지는 지운다.
- 🚨 **DB 는 되돌리지 않는다.** 롤백한 직전 코드는 새 스키마 위에서 돈다. 그래서 마이그레이션은
  직전 코드가 계속 돌 수 있게(expand-only) 짠다.
  - 컬럼 · 테이블 추가, nullable 컬럼 추가 → 한 번에 해도 된다
  - 컬럼 삭제 · 이름 바꾸기 · NOT NULL 로 조이기 → **두 번에 나눈다.** 먼저 코드가 그 컬럼을 안 쓰게 배포하고,
    다음 배포에서 스키마를 바꾼다
  - 마이그레이션 자체를 되돌려야 하면 사람이 서버에서 `alembic downgrade` 를 한다 (자동으로 하지 않는다 — 데이터가 지워질 수 있다)
- 롤백은 **새 커밋의 compose 파일**로 옛 이미지를 띄운다. compose 파일을 크게 바꾼 배포가 실패하면 롤백도 실패할 수 있다.
  그때 Actions 출력에 "롤백도 실패" 가 찍힌다.
- dev · prod 가 동시에 빌드하면 4GB 가 넘친다. `deploy.sh` 가 `flock` 으로 한 번에 하나만 돌게 하고, 다른 쪽은 최대 20분 기다린다.

## dev 격리 — 메모리 상한 · 팀만 열기 (#780)

한 EC2 에 dev 가 같이 뜨면서 생긴 두 위험을 막는다. dev 가 폭주하면 커널이 운영 postgres 를 죽일 수 있고,
dev 가 열려 있으면 누구나 카카오로 가입해 실제 보호자 · 아이 정보가 백업 없는 dev DB 에 쌓인다.

**메모리 상한** — compose 의 `mem_limit: ${API_MEM_LIMIT:-0}` 꼴 (api · web · db · redis). 0 = 상한 없음.

| | api | web | db | redis |
| --- | --- | --- | --- | --- |
| dev `.env` | `API_MEM_LIMIT=768m` | `WEB_MEM_LIMIT=384m` | `DB_MEM_LIMIT=512m` | `REDIS_MEM_LIMIT=128m` |
| prod `.env` | 안 쓴다 (상한 없음 — 지금 그대로) | | | |

- 넘치면 그 컨테이너만 죽고 `restart` 로 다시 뜬다. 값은 첫 dev 배포 뒤 `docker stats` 를 보고 고친다.
- 빈 값 · `-1` 은 compose 가 거부한다. 안 쓸 거면 줄째 지운다.
- 마이그레이션(`compose run`)에도 걸린다. 거기서 넘치면 exit 137 로 `deploy.sh` 가 up 전에 멈추고 옛 컨테이너가 그대로 서비스한다.
- 이미지 빌드에는 안 걸린다. 빌드는 `flock` 이 한 번에 하나로 줄 세운다.
- 확인: `docker inspect ktc4-dev-api --format '{{.HostConfig.Memory}}'` → 바이트, `ktc4-api` → `0`.

**팀만 열기 (basic_auth)** — Caddy 가 막는다 (`deploy/caddy/Caddyfile`).

- `dev.icatch.ai.kr` 는 전체, `api.dev.icatch.ai.kr` 는 로그인 시작(`GET` · `HEAD /api/v1/auth/kakao`)만 막는다.
  웹만 막으면 api.dev 에서 바로 카카오 로그인을 시작해 curl 로 가입까지 끝낼 수 있다.
  api 전체를 막으면 CORS preflight(OPTIONS)가 401 이라 dev 화면이 API 를 못 부른다.
- 그래서 브라우저가 두 번 묻는다 — 화면을 열 때(dev) 한 번, 카카오 로그인을 누를 때(api.dev) 한 번. 같은 계정이다.
- 계정은 **prod** checkout 의 `deploy/docker/.env` 에 둔다. 두 환경을 다 받는 Caddy 가 prod 프로젝트에 떠 있어서다.
  ```bash
  docker run --rm caddy:2.10.0-alpine caddy hash-password    # 비밀번호를 묻고 해시를 찍는다
  ```
  ```
  DEV_BASIC_AUTH_USER=<팀 공용 이름>
  DEV_BASIC_AUTH_HASH='$2a$14$...'
  ```
  🚨 해시는 **작은따옴표로 감싼다.** 안 감싸면 compose 가 해시 속 `$…` 를 변수로 바꿔 Caddy 에 깨진 값이 가고 늘 401 이다.
  `deploy.sh` 가 prod 배포 전에 두 값이 있는지, 해시가 작은따옴표로 감싸였는지 보고 아니면 아무것도 바꾸지 않고 멈춘다.
  비밀번호는 저장소 · PR · 이슈에 쓰지 않는다. [미정] 팀에 계정을 나누는 방법 (#780 🙋).
- 🚨 **순서 — 서버 prod `.env` 에 두 값을 먼저 넣고 main 에 머지한다.** 반대면 main 배포가 `deploy.sh` 의 검사에서 멈춘다(운영은 그대로지만 빨간불).
- 이 변경이 처음 나가는 prod 배포는 Caddy 의 environment 가 바뀌어 Caddy 를 새로 만든다 — 몇 초 dev · 운영이 같이 끊긴다.
- 확인: `curl -s -o /dev/null -w '%{http_code}\n' https://dev.icatch.ai.kr` → 401,
  `https://api.dev.icatch.ai.kr/api/v1/auth/kakao` → 401, `https://api.dev.icatch.ai.kr/health` → 200.

## CI

- `ci-api.yml` · `ci-web.yml` 은 PR 에서 돌고, 배포 워크플로가 `workflow_call` 로 한 번 더 부른다.
  push 트리거는 없다. 있으면 push 마다 같은 검사가 두 번 돈다.
- `ci-contract.yml` 은 게이트에 넣지 않았다. 서버 PR 을 막지 않으려고 만든 검사라서다 (그 파일 머리말).

## 준비 — 처음 한 번 (코드 밖)

1. **IAM** — 역할 `ktc-github-deploy` 의 신뢰 정책 sub 가 이미 `repo:kakaotechcampus-4/ktc4-pusan-3:*` 라서
   `…:environment:dev|prod` 도 통과한다. **할 일 없음** (11-06 확인).
   🚨 정책을 좁힐 때는 env 두 값과 함께 `…:ref:refs/heads/develop` 도 남긴다 — 적재 · `oidc-test` 워크플로는
   environment 없이 develop ref 로 역할을 받는다.
   배포 job 이 `environment:` 를 쓰면 sub 가 브랜치가 아니라 환경 이름이 된다. 빠지면 배포가 인증에서 멈춘다.
2. **GitHub Environments** `dev` · `prod` — 변수 `DEPLOY_DIR` (위 표의 checkout 경로).
   🚨 **배포 브랜치 규칙** — `prod` 는 `main` 만, `dev` 는 `develop` 만 (11-06 걸어 둠). 빼지 않는다.
   🚨 **이 규칙만으로는 막히지 않는다.** 규칙은 `environment:` 를 쓴 job 에만 걸린다. 신뢰 정책이 `:*` 인 동안은
   쓰기 권한이 있는 누구나 아무 브랜치에서 `environment:` 없는 워크플로를 만들어 dispatch 하면 역할을 받아
   운영 EC2 에 SSM 으로 root 명령을 보낼 수 있다 — "main 머지 = 운영" 을 우회한다 (#753 리뷰).
   막으려면 sub 를 `environment:dev` · `environment:prod` · `ref:refs/heads/develop`(적재 · `oidc-test`) 셋으로 좁힌다 — 후속.
   `AWS_ACCOUNT_ID` · `EC2_INSTANCE_ID` 는 저장소 변수 그대로다. prod 에 승인자를 걸지는 않는다 — main 머지가 이미 멘토 리뷰다.
3. **DNS** — `dev.icatch.ai.kr` · `api.dev.icatch.ai.kr` A 레코드를 같은 EC2 로.
4. **서버**
   - `git clone` 으로 `/home/ubuntu/ktc4-pusan-3-dev` 를 만들고 `develop` 을 checkout 한다.
   - dev 의 `deploy/docker/.env` 를 둔다. 위 표의 세 줄, 그리고 `NEXT_PUBLIC_API_BASE_URL=https://api.dev.icatch.ai.kr` 를 넣는다.
     🚨 dev 웹 이미지에는 Firebase 값(`NEXT_PUBLIC_FIREBASE_*`) 다섯 개와 `FCM_KEY_PATH` 를 **비운다** — 웹 푸시가 꺼진 정상 상태다 (11-06 태영님과 결정 · #747).
     같은 Firebase 프로젝트를 쓰면 dev 에서 보낸 알림이 팀원 브라우저에 섞여 구분이 안 된다. 앱은 운영 주소만 열어서 상관없다.
     dev 에서 푸시를 확인해야 할 일이 생기면 그때 웹 값 · `PUSH_DEVICE_ACCEPT` · FCM 키를 같이 켠다 ([`docs/web/web-push-v1.md`](../web/web-push-v1.md))
   - `NEXT_PUBLIC_API_BASE_URL` 을 prod 값 그대로 두면 `deploy.sh` 가 빌드 전에 멈춘다 — dev 화면이 운영 API 를 부르지 않게
   - dev 의 `apps/api/.env` 를 둔다. `PUSH_DEVICE_ACCEPT=false` · `FCM_SERVICE_ACCOUNT_FILE=` 로 푸시를 끈다. `APP_ENV=dev`, dev 카카오 콜백 · CORS(`https://dev.icatch.ai.kr`)를 넣는다.
     LLM · 외부 API · 카카오 키는 prod 와 같은 값을 쓴다 (11-06 결정 — 카카오는 같은 앱에 dev 콜백만 추가 등록). DB 비밀번호만 새로 만든다.
   - dev 의 `deploy/docker/.env` 에 메모리 상한 네 줄을 넣는다 (위 "dev 격리").
   - prod 의 `deploy/docker/.env` 에 `COMPOSE_PROFILES=edge` 와 `DEV_BASIC_AUTH_USER` · `DEV_BASIC_AUTH_HASH` 를 더한다 (위 "dev 격리" — main 머지 전에).
   - 🚨 서버에서 `make api-image` · `web-image` 는 거부된다(`.deployed-tag` 가 있으면). 지금 소스를 배포된 sha 태그로 구워
     롤백할 이미지를 덮기 때문이다. 서버 빌드는 `deploy.sh` 만 한다.
   - swap 을 확인한다. `free -h` 에서 Swap 이 0이면 2GB 를 만든다 (`fallocate -l 2G /swapfile` …).
   - 🚨 일정 알림 발송 cron(`docker exec ktc4-api … app.workers.reminder_dispatch`)은 **prod 에만** 둔다. dev 에는 등록하지 않는다 (11-06 결정).
     dev 에서 실제 기기로 푸시가 나가지 않게 하려는 것이다. 컨테이너 이름이 `ktc4-api` 로 고정이라 prod 줄은 고치지 않는다
   - 컨테이너 감시 cron 한 줄을 더한다:
     `* * * * * cd /home/ubuntu/ktc4-pusan-3-dev && STATE_FILE=/tmp/ktc4-health-alert-dev.state deploy/scripts/health-alert.sh >> /var/tmp/ktc4-health-alert-dev.log 2>&1`
5. **카카오 개발자 콘솔** — dev 콜백 URL(`https://api.dev.icatch.ai.kr/...`)을 등록한다.
6. **첫 prod 전환 — 한 번만 손으로.** 예전 compose 의 기본 네트워크가 `172.30.0.0/24` 를 잡고 있다.
   그래서 공용 네트워크 `ktc4-edge` 를 같은 대역으로 만들 수 없다. prod 를 한 번 내려야 한다 (볼륨은 남는다).
   - **그 전까지 dev 배포는 빨간불이다** — `ktc4-edge` 를 못 만들어 멈춘다(dev 는 아직 안 떠 있어서 잃는 건 없다).
     develop 머지 → develop→main 머지 → 아래 전환을 이어서 한다.
   - ⚠️ **유의사항** — 이 셋(develop 머지 · main 머지 · 첫 전환)은 **같은 날 이어서** 한다. 사이가 벌어지면
     그동안 develop 머지는 운영에도 dev 에도 안 나간다(dev 는 빨간불, 운영은 main 머지 전이라 그대로).
     첫 전환 때 운영이 **1~2분 내려가므로** 사용자가 적은 시간을 고른다.
   - 이미지를 **먼저** 굽고 내린다. 내린 뒤에 구우면 빌드(~10분) 내내 운영이 꺼져 있다. 이 순서면 꺼지는 시간은 마이그레이션 + 기동(1~2분)이다.
   ```bash
   cd /home/ubuntu/ktc4-pusan-3
   git fetch origin main && git reset --hard origin/main && SHA=$(git rev-parse HEAD)
   # 1) 서비스는 그대로 둔 채 새 이미지를 굽는다 (옛 컨테이너는 계속 돈다)
   env API_IMAGE_TAG=$SHA WEB_IMAGE_TAG=$SHA docker compose -f deploy/docker/docker-compose.deploy.yml --env-file deploy/docker/.env build --quiet api web
   # 2) 내린다 — 옛 기본 네트워크(172.30.0.0/24)도 같이 지워진다. 여기서부터 운영이 꺼진다
   docker compose -f deploy/docker/docker-compose.deploy.yml --env-file deploy/docker/.env down
   # 3) 배포 — ktc4-edge 생성 · (캐시라 금방 끝나는) 빌드 · 마이그레이션 · 기동. 실패하면 :local 로 롤백한다
   bash deploy/scripts/deploy.sh $SHA prod
   ```
   끝나면 실패해 있던 dev 배포를 Actions 에서 다시 돌린다(Re-run).
   Re-run 은 **그 run 의 커밋**을 그대로 올린다. 이미 더 새 커밋이 떠 있으면 `deploy.sh` 가 거부한다 — 그때는 Re-run 이 아니라 새 run(`workflow_dispatch`)을 돌린다.

## 남은 것

- dev 의 카카오 키 부팅 검사 — 지금은 `APP_ENV=prod` 만 막는다 (`app/core/config.py`). dev 에 키가 없어도 뜬다
- 적재 워크플로(`load-food-catalog.yml` · `load-places.yml`)는 prod 경로 고정이다. dev 적재가 필요해지면 환경 입력을 더한다
- 빌드는 서버에서 한다. 빌드 중 OOM 이 실제로 나면 Actions 에서 빌드해 ECR 로 올리고 서버는 받기만 하게 옮긴다
