# 배포 v1 — CI 게이트 · 자동 롤백 · dev/prod 분리

담당: 김명성 · 작성일: 2026-11-06

## 한눈에

```
develop push ─┐                                    ┌─ dev  (dev.icatch.ai.kr · api.dev.icatch.ai.kr)
              ├─ API CI + Web CI ─ 통과 ─ deploy.sh ┤
main push ────┘        │                           └─ prod (icatch.ai.kr · api.icatch.ai.kr)
                    빨간불 → 배포 안 함
deploy.sh: 빌드 → 배포 전 백업 → 마이그레이션 → up --wait(헬스) ─ 실패 → 직전 이미지로 up → 워크플로 빨간불
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
| `deploy/docker/.env` | `COMPOSE_PROFILES=edge` | `COMPOSE_PROJECT_NAME=ktc4-dev` · `STACK_SUFFIX=-dev` · `APP_ENV=dev` · `API_EDGE_IP=172.30.0.21` |
| GitHub Environment | `prod` | `dev` |

## 롤백 — 무엇이 되돌아가고 무엇이 안 되돌아가나

`deploy/scripts/deploy.sh` 가 한다. 판정은 compose 의 `up --wait` 다. 각 서비스의 healthcheck 가
healthy 가 될 때까지 기다리고, 안 되면(최대 240초) 실패로 본다.

| 실패 지점 | 결과 |
| --- | --- |
| CI | 배포하지 않는다 |
| 굽지 않는 이미지(caddy · db · redis) 받기 · Caddyfile 검사(prod) | 여기서 멈춘다. 아무것도 바꾸지 않았다 |
| 이미지 빌드 · 마이그레이션 | 여기서 멈춘다. `down` 을 먼저 하지 않으므로 **옛 컨테이너가 그대로 서비스한다** (db · redis 이미지가 바뀐 커밋이면 마이그레이션 `run` 이 그 둘을 먼저 새로 띄운다) |
| 배포 전 백업 (#758 — 빌드 뒤 · 마이그레이션 앞) | 여기서 멈춘다. 아무것도 바꾸지 않았다 — 마이그레이션을 되돌릴 곳이 이 백업뿐이라 백업 없이 넘어가지 않는다. DB 컨테이너가 죽어 백업이 안 되는데 그걸 고치는 배포면 Actions 의 "배포" 를 손으로 돌리며 `skip_backup` 을 켠다 — 백업 없이 넘어가고 Discord 와 Actions 에 남는다. 새 스택의 첫 배포(DB 컨테이너가 아직 없거나 테이블이 0개)도 같다 |
| Caddy reload (prod) | 새 버전은 떠 있고 Caddy 는 옛 설정이다. 워크플로는 빨간불 — 서버에서 고친다 |
| 지금 떠 있는 것보다 옛 커밋 (옛 run 의 Re-run) | 아무것도 바꾸지 않고 멈춘다. 일부러 되돌릴 때만 서버에서 `ALLOW_OLD=1` |
| 새 컨테이너 헬스 | 직전에 성공한 태그(`deploy/docker/.deployed-tag`)로 다시 `up --wait`. 워크플로는 빨간불 |

- 이미지 태그는 **커밋 sha** 다. 지금 것과 직전 것 두 개만 남기고 나머지는 지운다.
- 🚨 **DB 는 되돌리지 않는다.** 롤백한 직전 코드는 새 스키마 위에서 돈다. 그래서 마이그레이션은
  직전 코드가 계속 돌 수 있게(expand-only) 짠다.
  - 컬럼 · 테이블 추가, nullable 컬럼 추가 → 한 번에 해도 된다
  - 컬럼 삭제 · 이름 바꾸기 · NOT NULL 로 조이기 → **두 번에 나눈다.** 먼저 코드가 그 컬럼을 안 쓰게 배포하고,
    다음 배포에서 스키마를 바꾼다
  - **CI 가 막는다** (#782) — `scripts/check_expand_only.py` 가 새 마이그레이션의 `upgrade()` 와 거기서 부르는
    같은 파일의 함수에서 아래를 찾으면 빨간불. 목록 · 이유의 정본은 스크립트 머리말이다.
    - `drop_column` · `drop_table` · `rename_table` · `alter_column(new_column_name=… / nullable=False)`
    - `server_default` 없는(`None` 포함) NOT NULL · `primary_key` `add_column`
    - NOT NULL 인 게 보이는 컬럼의 `alter_column(server_default=None)` — `nullable=False` · `existing_nullable=False`
      또는 같은 upgrade 에서 NOT NULL 로 추가한 컬럼. 안 보이면 nullable 일 수 있어 넘긴다
    - 같은 이름의 `drop_constraint` + `create_check_constraint` (CHECK 교체) — enum 이 CHECK 라 값 빼기 · 바꾸기가
      이 모양이다. 넓히기(값 더하기)도 걸린다 — 값이 상수 · 헬퍼 인자로 흘러와 코드로 못 가린다
    - `alter_column(type_=…)` — `VARCHAR(n)` → `VARCHAR(m ≥ n)` · `TEXT` 처럼 넓히기가 확실한 것만 통과
      (길이는 `String(8)` · `String(length=8)` 둘 다 읽는다. 길이가 상수가 아니면 넓히기로 보지 않는다)
    - `op.execute` 원문 SQL(상수 · 파일 안 변수 · `sa.text`)의 `DROP TABLE`, `ALTER TABLE` 의
      `DROP [COLUMN]` · `RENAME` · `ALTER [COLUMN] … TYPE` · `SET NOT NULL`
    - `drop_index` · `drop_constraint` 만 있는 것은 보지 않는다 (기존 마이그레이션에서 오탐만 나왔다)
  - 넘기는 표시는 **그 연산 바로 위 줄이나 같은 줄의 주석**이고, 그 연산 하나만 넘긴다. docstring · 문자열 안의 글자는 안 된다.
    - `# contract: #<앞 배포 PR 번호>` — 두 번째 배포(앞 배포로 코드가 이미 안 쓰는 컬럼을 지우는 것)
    - `# expand-ok: <이유>` — CHECK 교체 · `type_` 에만. 넓히기라 직전 코드가 그대로 돈다는 것을 사람이 봤다는 뜻이다
    - 🚨 헬퍼 함수 안 연산에 단 `# expand-ok:` 는 그 헬퍼의 **모든 호출**에 통한다. 넓히기 표시를 단 헬퍼는 넓히기에만
      쓴다 — 같은 `_swap()` 으로 값 빼기까지 하면 빼기도 같이 넘어간다
  - 못 보는 것: SQL 을 함수 반환값 · 문자열 덧셈으로 만든 것, 원문 SQL 의 `ADD COLUMN … NOT NULL`(DEFAULT 없음),
    원문 SQL 로 CHECK 를 지우고 다시 만드는 것(`DROP CONSTRAINT` + `ADD CONSTRAINT … CHECK`). 새 unique 제약처럼
    의미를 봐야 하는 것도 이 검사 밖이다
  - 🚨 **contract PR 은 앞 PR 이 prod(main) 에 배포된 뒤에 develop 에 머지한다.** 표시는 배포 순서를 확인하지 못한다.
    둘이 develop 에 따로 머지돼도 develop → main 한 번에 같이 실리면, prod 헬스 실패 때 앞 PR 이전 이미지로
    롤백되고 그 이미지는 지운 컬럼을 읽는다.
  - 대상은 스크립트의 `BASELINE`(`d5e8a1c4b937`)과 그 조상을 뺀 revision 전부다 — BASELINE 앞에서 갈라져 나중에
    merge 된 revision 도 들어간다. 평소엔 올리지 않는다. **규칙을 넓히는 PR 에서만** prod DB 의 현재 revision
    (서버 prod checkout 에서 `alembic current`)으로 같이 올린다 — main head 가 아니라 실제 prod DB 기준이다
    (#753 전에는 prod 가 develop push 로 배포돼 main head 와 다를 수 있다). 배포된 revision 이 새 규칙에 걸려 배포 게이트가 막히지 않게. git diff 를 쓰지 않는 이유는
    CI checkout 이 depth 1 이고 배포가 부른 실행엔 비교할 base 가 없어서다.
  - 마이그레이션 자체를 되돌려야 하면 사람이 서버에서 `alembic downgrade` 를 한다 (자동으로 하지 않는다 — 데이터가 지워질 수 있다)
  - 마이그레이션이 잠금 대기(`lock_timeout` — `canceling statement due to lock timeout`)로 실패했으면 긴 쓰기
    트랜잭션에 막힌 것이다. 같은 실행의 revision 은 모두 되돌아가 반쯤 적용된 상태가 없고, 옛 컨테이너가 그대로
    서비스한다. 잠시 뒤 배포를 다시 돌린다 (삭제 이력 `de2051c89680` 은 표마다 트리거를 걸며 5초까지만 기다린다 · #812 리뷰)
- 롤백은 **새 커밋의 compose 파일**로 옛 이미지를 띄운다. compose 파일을 크게 바꾼 배포가 실패하면 롤백도 실패할 수 있다.
  그때 Actions 출력에 "롤백도 실패" 가 찍힌다.
- dev · prod 가 동시에 빌드하면 4GB 가 넘친다. `deploy.sh` 가 `flock` 으로 한 번에 하나만 돌게 하고, 다른 쪽은 최대 20분 기다린다.
  매일 백업 뒤의 복원 점검(cron, `restore-check.sh`)도 같은 잠금을 잡는다 — 임시 DB 가 빌드와 겹치지 않게 (#758).

## drain — 옛 api 가 하던 일을 끝내고 내려간다 (#882)

`up` 은 옛 api 를 멈춘 뒤 새 api 를 띄운다(이름 · edge IP 가 고정이라 둘이 같이 못 뜬다). 멈출 때 옛 api 는:

```
SIGTERM ─ uvicorn: 새 연결 거부 → 열린 요청 · SSE 를 기다림 (상한 70s, Dockerfile --timeout-graceful-shutdown)
        ─ lifespan: 남은 run → 그다음 Curator 를 기다림 (상한 30s, main.DRAIN_SECONDS — 넘기면 취소하고 정리를 5s 기다림)
        ─ lifespan: LLM 사용량 마지막 flush (≤5s) · 클라이언트 닫기 → 종료 (exit 143)
120s    ─ 그때까지 안 끝나면 SIGKILL (compose api stop_grace_period)
```

- 🚨 **70 + 30 + 취소 정리 5 + flush 5 = 110 < 120** 을 지킨다. 하나를 바꾸면 셋을 같이 본다. 유예를 다 쓰지 않는다 — drain 이 끝나는 대로 내려간다.
- uvicorn 상한 70s 는 run 마감 60s(`runner.RUN_DEADLINE_SECONDS`)보다 길어야 한다. 짧으면 상한에서 끊긴 SSE 마다 ERROR traceback 이 찍혀 배포 때 Discord 가 울린다.
- 취소된 run 은 러너의 finally 를 거쳐 failed 로 닫히고 Idempotency 키를 놓는다. drain 이 없던 때는 화면이 안 붙은 run 이 SIGTERM 순간 흔적 없이 사라졌다 — uvicorn 이 정리 뒤 SIGTERM 을 다시 일으켜 죽어서 asyncio 취소 처리도 돌지 않는다 (#882 실측).
- drain 하는 동안 새 요청은 받을 곳이 없다 — Caddy 가 새 api 가 뜰 때까지 붙잡는다(`wait_upstream`, deploy/caddy/Caddyfile · #883).
- web 은 그대로 둔다 — run 같은 장기 작업을 들고 있지 않다.

## dev 격리 — 메모리 상한 · 팀만 열기 (#780)

한 EC2 에 dev 가 같이 뜨면서 생긴 두 위험을 막는다. dev 가 폭주하면 커널이 운영 postgres 를 죽일 수 있고,
dev 가 열려 있으면 누구나 카카오로 가입해 실제 보호자 · 아이 정보가 dev DB 에 쌓인다.

**메모리 상한** — compose 의 `mem_limit` · `memswap_limit: ${API_MEM_LIMIT:-0}` 꼴 (api · web · db · redis). 0 = 상한 없음.

| | api | web | db | redis |
| --- | --- | --- | --- | --- |
| dev `.env` | `API_MEM_LIMIT=768m` | `WEB_MEM_LIMIT=384m` | `DB_MEM_LIMIT=512m` | `REDIS_MEM_LIMIT=128m` |
| prod `.env` | 안 쓴다 (상한 없음 — 지금 그대로) | | | |

- 넘치면 그 컨테이너만 죽고 `restart` 로 다시 뜬다. 값은 첫 dev 배포 뒤 `docker stats` 를 보고 고친다.
- `memswap_limit` 을 같은 값으로 둔다. `mem_limit` 만 두면 Docker 가 swap 을 같은 양만큼 더 줘서(`MemorySwap` = 2배, 로컬 확인)
  dev 가 죽지 않고 서버 swap 2GB 를 채우며 운영까지 느려진다 (#808 리뷰).
- 🚨 `${X:-0}` 은 **빈 값도 0(상한 없음)** 으로 바꿔 오류 없이 띄운다. 그래서 `deploy.sh` 가 dev 배포 전에
  네 값이 다 있고 0 이 아닌지 본다 (`--check-env` · make 배포 타깃도 같다). prod 는 안 쓴다 — 0 이면 compose 설정에서 빠져서 컨테이너가 다시 만들어지지 않는다.
- 마이그레이션 · 적재(`compose run --rm api-task`)에도 걸린다 — `api-task` 가 api 와 같은 값을 쓴다. 거기서 넘치면 exit 137 로 `deploy.sh` 가 up 전에 멈추고 옛 컨테이너가 그대로 서비스한다.
- 이미지 빌드에는 안 걸린다. 빌드는 `flock` 이 한 번에 하나로 줄 세운다.
- 확인: `docker inspect ktc4-dev-api --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}'` → 같은 바이트 두 개, `ktc4-api` → `0 0`.

**팀만 열기 (basic_auth)** — Caddy 가 막는다 (`deploy/caddy/Caddyfile`).

- `dev.icatch.ai.kr` 는 전체, `api.dev.icatch.ai.kr` 는 로그인 시작과 콜백(`GET` · `HEAD /api/v1/auth/{provider}` · `…/callback`)만 막는다.
  웹만 막으면 api.dev 에서 바로 카카오 로그인을 시작해 curl 로 가입까지 끝낼 수 있다.
  - **콜백도 막는 이유** — 콜백은 `oauth_state` 쿠키의 state 와 쿼리의 state 가 같은지만 본다. 쿠키는 서명 · 서버 저장이 없어
    누구나 만든다. 공개 client_id 와 dev redirect_uri 로 카카오 동의를 받고 쿠키를 직접 붙여 콜백을 부르면 handoff 코드가 나오고,
    `POST /auth/{provider}` · `/signup` 은 열려 있어 가입이 끝난다 (#808 리뷰). state 검사는 피해자 브라우저를 지키는
    로그인 CSRF 방어라 공격자 자신의 요청은 못 막는다.
  - provider 를 가리지 않는다 — `AuthProvider` 에 apple · google · naver 가 있어 붙이는 날 Caddyfile 수정 없이 막힌다.
  - 열어 두는 것 — `…/status`, `POST` 교환 · 가입 · refresh · logout, `OPTIONS`. api 전체를 막으면 CORS preflight(OPTIONS)가
    401 이라 dev 화면이 API 를 못 부른다. 교환 · 가입은 콜백이 준 코드가 있어야 쓸 수 있다.
- 브라우저가 묻는 건 보통 두 번이다 — 화면을 열 때(dev) 한 번, 카카오 로그인을 누를 때(api.dev) 한 번. 같은 계정이다.
  콜백은 로그인 시작(`/api/v1/auth/kakao`)과 같은 오리진 · 같은 realm 의 그 아래 경로라, 브라우저가 그때 넣은 계정을 다시 보내
  또 묻지 않는 게 보통이다 (기본 인증은 쿠키가 아니라 SameSite 와 무관하다). [미확인] 실제 브라우저에서는 첫 dev 배포 때 본다 —
  다시 물어도 같은 계정을 넣으면 된다.
- 계정은 **prod** checkout 의 `deploy/docker/.env` 에 둔다. 두 환경을 다 받는 Caddy 가 prod 프로젝트에 떠 있어서다.
  ```bash
  # 무작위 비밀번호를 만들어 찍고(팀에 나눌 값), 해시는 stdin 으로 만든다 — 비밀번호가 셸 기록 · ps 에 안 남는다
  PW="$(openssl rand -hex 16)"; echo "$PW"
  printf '%s\n' "$PW" | docker run --rm -i httpd:2.4-alpine htpasswd -niBC 10 <팀 공용 이름>
  # → <팀 공용 이름>:$2y$10$…   의 : 뒤가 해시다
  ```
  ```
  DEV_BASIC_AUTH_USER=<팀 공용 이름>
  DEV_BASIC_AUTH_HASH='$2y$10$...'
  ```
  - `caddy hash-password` 는 쓰지 않는다 — 터미널 없이 부르면 `Error: EOF` 로 끝나고, cost 가 14 로 고정이다.
  - cost 10 인 이유 — Caddy 는 매 요청 bcrypt 비교를 한다. 로컬(맥 · Caddy 2.10)에서 cost 14 는 한 번에 1.5~3초,
    cost 10 은 0.1~0.2초였다. 길고 무작위인 비밀번호라 cost 를 낮춰도 맞히기 어렵다.
  - 🚨 **그래도 남는 것** — Caddy 는 없는 사용자 이름이면 내장 가짜 해시(cost 14 고정)와 비교한다. 아무 이름의
    `Authorization: Basic …` 요청 하나가 cost 14 비교 한 번(로컬 1.6~4.7초)을 쓴다 — 우리 해시 cost 와 무관하다.
    Caddyfile 만으로는 못 막는다(요청 수 제한은 Caddy 플러그인이 필요하다). [미정] 실제로 운영이 느려지면 후속으로 본다.
  🚨 해시는 **작은따옴표로 감싼다.** 안 감싸면(큰따옴표도) compose 가 해시 속 `$…` 를 변수로 바꿔 Caddy 에 깨진 값이 가고 늘 401 이다.
  `deploy.sh` 는 `.env` 줄이 아니라 **compose 가 해석해 Caddy 에 넘기는 값**(`docker compose config --format json`)을 본다 —
  따옴표 · 줄 끝 공백 · 주석 · `\r` 을 compose 와 똑같이 푼 값이다. 사용자 이름에 공백 · `:` 이 없는지, 해시가 bcrypt 모양
  (`$2a|b|y$NN$` + 53자)인지 보고 아니면 build 전에 멈춘다. 배포할 커밋의 compose 파일로 읽으려고 `git reset` 뒤에 본다.
  Caddyfile 검사에도 그 값을 `docker run -e NAME`(값은 환경으로 물려줌)으로 넘겨 `ps` 에 해시가 보이지 않는다.
  비밀번호는 저장소 · PR · 이슈에 쓰지 않는다. [미정] 팀에 계정을 나누는 방법 (#780 🙋).
- 🚨 **순서 — 서버 prod `.env` 에 두 값을 먼저 넣고 main 에 머지한다.** 반대면 main 배포가 `deploy.sh` 의 검사에서 멈춘다(운영은 그대로지만 빨간불).
  넣을 때는 작은따옴표만 눈으로 확인한다. 값이 맞는지는 main 배포의 검사가 build 전에 본다 — 틀리면 멈추고 운영은 그대로다.
  (지금 prod checkout 은 이 변경 전 커밋이라 `--check-env` 로 미리 보면 Caddy environment 가 없어 실패로 나온다.)
- 이 변경이 처음 나가는 prod 배포는 Caddy 의 environment 가 바뀌어 Caddy 를 새로 만든다 — 몇 초 dev · 운영이 같이 끊긴다.
- 확인: `curl -s -o /dev/null -w '%{http_code}\n' https://dev.icatch.ai.kr` → 401,
  `https://api.dev.icatch.ai.kr/api/v1/auth/kakao` · `…/kakao/callback` → 401, `…/kakao/status` · `https://api.dev.icatch.ai.kr/health` → 200.

**make 로 굽지 않기** — 서버에서 make 가 지금 작업 트리를 배포된 sha 이름으로 구우면 롤백할 이미지를 덮는다 (#753 · #808 리뷰).

- `api-image` · `web-image` 는 서버(`.deployed-tag` 가 있으면)에서 거부된다.
- `api-up` · `web-up` · `deploy-up` 은 `--no-build` 다. 이미지가 없으면 굽지 않고 실패한다.
- `deploy-migrate` · `deploy-load-*` 는 배포와 같은 잠금 안에서 `.deployed-tag` 를 읽고, 그 태그의 api 이미지가 없으면 멈춘다.
  `compose run` 에는 `--no-build` 가 없고 `--pull never` 여도 이미지가 없으면 굽기 때문이다 (compose v2.34 로컬 확인).
- 🚨 **로컬에서 처음 띄울 때** 는 `make api-image web-image` 를 먼저 돈다 — up · migrate 가 더는 알아서 굽지 않는다.

**일회용 작업은 `api-task` 로** — 마이그레이션(deploy.sh · `make deploy-migrate`)과 적재(`make deploy-load-*`)는
`compose run --rm api-task <명령>` 이다. `api-task` 는 api 와 같은 이미지 · 환경 · 메모리 상한이고 다른 것은 셋뿐이다.

- **ktc4-edge 에 안 붙는다.** `compose run` 은 서비스의 고정 IP 까지 복사한다. 그래서 `run --rm api` 는 떠 있는 api 와
  같은 주소(.20 · .21)를 두 번 잡아 `Address already in use` 로 멈춘다. 첫 배포 · 첫 전환은 api 가 없어서 통과하고
  두 번째 배포부터 늘 터졌다 (11-09 dev 배포 3회 실패). DB 만 보면 되니 Caddy 망 · S3 열쇠(.20)에 닿을 이유도 없다.
- **`build:` 가 없고 `pull_policy: never` 다.** 이미지가 없으면 굽지도 받지도 않고 멈춘다 — 위 "make 로 굽지 않기" 와 같은 이유.
- **profile(`task`)이 있다.** `up` 은 띄우지 않고, `run` 으로 이름을 부를 때만 켜진다.
- `test_deploy.py` 가 deploy.sh · Makefile 의 `run --rm <서비스>` 가 고정 IP 서비스를 부르면 실패한다.

## CI

- `ci-api.yml` · `ci-web.yml` 은 PR 에서 돌고, 배포 워크플로가 `workflow_call` 로 한 번 더 부른다.
  push 트리거는 없다. 있으면 push 마다 같은 검사가 두 번 돈다.
- `ci-contract.yml` 은 게이트에 넣지 않았다. 서버 PR 을 막지 않으려고 만든 검사라서다 (그 파일 머리말).

## 준비 — 처음 한 번 (코드 밖)

1. **IAM** — 역할 `ktc-github-deploy` 의 신뢰 정책 sub 가 이미 `repo:kakaotechcampus-4/ktc4-pusan-3:*` 라서
   `…:environment:dev|prod` 도 통과한다. **할 일 없음** (11-06 확인).
   🚨 정책을 좁힐 때는 env 두 값과 함께 `…:ref:refs/heads/develop` 도 남긴다 — 적재 · 백업 감시 워크플로는
   environment 없이 develop ref 로 역할을 받는다.
   배포 job 이 `environment:` 를 쓰면 sub 가 브랜치가 아니라 환경 이름이 된다. 빠지면 배포가 인증에서 멈춘다.
2. **GitHub Environments** `dev` · `prod` — 변수 `DEPLOY_DIR` (위 표의 checkout 경로).
   🚨 **배포 브랜치 규칙** — `prod` 는 `main` 만, `dev` 는 `develop` 만 (11-06 걸어 둠). 빼지 않는다.
   🚨 **이 규칙만으로는 막히지 않는다.** 규칙은 `environment:` 를 쓴 job 에만 걸린다. 신뢰 정책이 `:*` 인 동안은
   쓰기 권한이 있는 누구나 아무 브랜치에서 `environment:` 없는 워크플로를 만들어 dispatch 하면 역할을 받아
   운영 EC2 에 SSM 으로 root 명령을 보낼 수 있다 — "main 머지 = 운영" 을 우회한다 (#753 리뷰).
   막으려면 sub 를 `environment:dev` · `environment:prod` · `ref:refs/heads/develop`(적재 · 백업 감시) 셋으로 좁힌다 — 후속.
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
   - dev 의 `deploy/docker/.env` 에 메모리 상한 네 줄을 넣는다 (위 "dev 격리"). 🚨 없으면 dev 배포가 `deploy.sh` 검사에서 멈춘다.
   - prod 의 `deploy/docker/.env` 에 `COMPOSE_PROFILES=edge` 와 `DEV_BASIC_AUTH_USER` · `DEV_BASIC_AUTH_HASH` 를 더한다 (위 "dev 격리" — main 머지 전에).
   - 🚨 서버에서 make 는 이미지를 굽지 않는다 (위 "make 로 굽지 않기"). 서버 빌드는 `deploy.sh` 만 한다 —
     빌드 인자(`NEXT_PUBLIC_*`)를 바꿨으면 `deploy.sh` 로 다시 배포한다.
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

- 적재 워크플로(`load-food-catalog.yml` · `load-places.yml`)는 prod 경로 고정이다. dev 적재가 필요해지면 환경 입력을 더한다
- 빌드는 서버에서 한다. 빌드 중 OOM 이 실제로 나면 Actions 에서 빌드해 ECR 로 올리고 서버는 받기만 하게 옮긴다
