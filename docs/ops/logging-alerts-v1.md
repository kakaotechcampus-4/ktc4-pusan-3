# 서버 로그 적재 · 에러 알림 v1

문서 목적: 멘토 #267 리뷰 2번 "서버 로그를 모으고 알리는 장치"에서 내린 결정과 근거를 고정한다.
운영 절차(로그는 어디서 보나 · 알림 켜는 법 · 확인 명령)는 [`apps/api/README.md` "로그와 알림"](../../apps/api/README.md#로그와-알림)에 있다.

기준 브랜치: `feat/be-error-alert-webhook` · [PR #302](https://github.com/kakaotechcampus-4/ktc4-pusan-3/pull/302)
작성일: 2026-10-09
담당: 박재형 (알림 코드) · 김명성 (api 를 배포 compose 에 올릴 때 로그 설정, [#166](https://github.com/kakaotechcampus-4/ktc4-pusan-3/issues/166))
선행: 루트 CLAUDE.md §2(원문 대신 id) · §9(public 저장소) · §10(외부 전달 범위 미정)

## 1. 결정

| 무엇 | 어떻게 | 왜 |
|---|---|---|
| **적재(모으기)** | 앱이 아니라 **docker 로그 드라이버 `journald`** — compose 의 `x-logging` 앵커로 전 서비스에 | 앱은 stderr 로만 찍고, 어디에 모을지는 실행 환경이 정한다 (§2) |
| **알림** | `app.*` 로거의 **ERROR 이상 → Discord 웹훅**, 앱 코드 (`app/core/alerts.py` · `app/integrations/discord.py`) | docker 에는 "로그 줄을 골라 사람에게 보내는" 기능이 없다 |
| **알림 본문** | Discord 카드(embed) 한 장 — 제목(**코드에 적힌 로그 글귀** 또는 예외 **종류 이름**) · 로거:함수:줄 · 환경 · 시각. 값을 내보내려면 `extra={"alert_detail"}`(값 줄) · `extra={"alert_heading"}`(제목) 으로 표시 — 서버가 모양을 검사한 값만 | 값(args) · 원문 · 트레이스백 · 예외 메시지는 밖으로 안 나간다 (§3) |
| **세기 · 다음 할 일** | 카드 왼쪽 색 막대 + 제목 앞 🔴(지금) · 🟠(오늘) · 🟡(내일) · 🟢(복구), 바닥글에 다음에 칠 명령 | 현업의 심각도 구분 · 런북을 가장 작게. 전부 같은 세기면 알림 피로로 다 무시한다. 🔴 만 `@here` 로 폰을 울릴지는 실사용자 뒤에 정한다 |
| **구별** | 채널 하나, 보내는 이름이 출처별 — `api-alert` · `browser-alert` · `report-alert` · `infra-alert` (`extra={"alert_source"}`) | 웹훅 · 채널을 늘리지 않는다. 쪼개고 싶으면 그때 URL 을 하나 더 |
| **화면 오류** | 오류 바운더리, 그리고 렌더 밖 예외(`window` 의 `error` · `unhandledrejection` · #732) → `POST /client-errors`(로그인한 보호자만) → ERROR 로그 → 같은 알림 | 보호자 폰의 오류는 서버로 오지 않았다. 값은 종류 · digest · 경로 · 기기 요약뿐이고 모양을 서버가 검사한다. DB 에 넣지 않는다 (처리방침 ⑥) |
| **앱 안 신고** | 설정의 신고 창 → `POST /reports`(로그인한 보호자만) → 오류가 아닌 **ERROR** 로그("신고 접수") → 같은 웹훅에 `report-alert` · 🟡, 바닥글은 조회 SQL. 값은 분류 · 신고 번호뿐 | Play 정책(AI 생성 · 사용자 제작 콘텐츠)이 앱 안 신고를 요구한다 (#487). 보호자가 쓴 글은 `report` 테이블에만 — 알림을 보면 `SELECT category, message FROM report WHERE id = '<번호>'` 로 읽는다. 알림이 분당 한도에 밀려 빠질 수 있으니 처리 안 된 신고는 `WHERE handled_at IS NULL` 로 따로 본다. 보호자당 24시간 10건 상한 — 보호자 행을 잠그고 세서 동시에 보내도 넘치지 않는다. 분당 알림 한도는 오류 알림과 같이 쓴다 |
| **web 서버 오류** | Next `src/instrumentation.ts` 의 `onRequestError` → 웹훅으로 직접 (`web-alert`). 종류 · digest · 경로 · 라우트만, 분당 상한, 개발 모드는 끔 | Next 서버엔 보호자 토큰이 없어 `/client-errors` 를 못 쓴다. 웹 컨테이너에 `ALERT_WEBHOOK_URL` 을 compose 가 넘긴다 |
| **컨테이너 감시** | 서버 cron 1분 · `deploy/scripts/health-alert.sh` — docker healthcheck 결과와 api 의 `/health/ready`(DB · Redis 에 닿는가)가 **달라졌을 때만** 같은 웹훅으로 | api 가 못 뜨거나 redis 가 죽은 건 api 안의 알림이 말할 수 없다 (§5) |
| **서버 밖 가동 감시** | UptimeRobot 무료 — 5분마다 api `/health` · web `/` 를 밖에서 찔러 Discord 로 (웹훅은 따로 `uptime-alert`) | 서버 안의 cron 은 EC2 자체가 죽으면 같이 말을 못 한다. 코드 없음 · 보호자 정보 안 감 (응답 코드만). 무료 플랜에 Discord 연동 포함, 비상업 용도 |
| **SaaS(Sentry 등)** | 로그 · 오류 추적은 안 쓴다 | 요청 본문 · 사용자 정보가 밖으로 가는 도구는 쓰지 않는다. 로그 줄에는 `%s` 에 채운 값(원문)이 있다 (§4) |
| **서버 메트릭** | Grafana Cloud 로 보낸다 — Alloy 컨테이너 1개 (#946, 11-10 PM) | 숫자와 고정 라벨(응답 코드 · 메서드 · 디스크 장치)만 나가고 `parent_id` · 경로 · IP · Host 헤더가 없어 개인정보가 아니다 — 처리방침의 위탁 · 국외 이전 대상이 아니다. 무엇을 왜는 §7 |

## 2. 왜 앱 파일 핸들러가 아닌가

파일 핸들러(앱이 직접 로그 파일을 쓰고 자르는 것)를 달면 "배포를 넘어 로그가 남는다"는 장점이 있지만,

- stderr 핸들러는 남겨야 하므로(터미널 · `docker logs`) docker 쪽 회전 설정은 **어차피 필요**하고, 같은 로그가 두 벌이 된다
- 볼륨(호스트 디스크 연결)을 붙여야 하고, 첫 배포에서 폴더 쓰기 권한에 자주 걸린다
- 워커가 2개 이상이 되면 같은 파일을 두 프로세스가 돌리면서 줄이 섞이거나 사라진다 (지금은 `--workers 1` 전제라 괜찮지만, 조용히 깨지는 종류다)
- 그 장점 하나는 docker 의 journald 드라이버로도 얻는다 (§3)

앱 핸들러가 이기는 조건은 둘뿐이다 — **api 를 docker 없이 띄우거나, journald 를 못 쓰는 서버일 때.** 그때 20줄로 넣는다.

## 3. 왜 journald 인가 — json-file 의 수명

docker 기본 로그 드라이버(json-file)는 컨테이너의 화면 출력을 호스트 파일에 받아 둔다. 그 파일은 **컨테이너와 수명이 같다.** 재시작은 남지만, 새 이미지로 `docker compose up` 을 치면 docker 는 컨테이너를 고치는 게 아니라 **지우고 새로 만들어서**(재생성) 그 전까지의 로그가 함께 사라진다. "어제 저녁에 입력했는데 추천이 안 떴어요"를 오늘 아침 배포 뒤에 보면 없다. 기본값은 회전도 없어서 디스크가 찰 때까지 커진다.

`journald` 드라이버면 로그가 docker 밖 OS(systemd 저널)에 쌓여 컨테이너를 지워도 남고, 보관 용량은 OS 가 돌리며(기본 디스크의 10%, 최대 4GB), `docker compose logs api` 도 그대로 된다. 조건은 서버가 systemd 리눅스라는 것인데, 서버는 카테캠이 준 **EC2 t3.medium · Ubuntu 24.04 LTS · 서울 · 디스크 50GB 고정** 이고 Ubuntu 는 저널을 기본으로 디스크에 남긴다.

> web · redis 도 json-file 이었다 — 배포했다면 배포마다 두 컨테이너의 로그가 지워졌을 것이다. PR #302 에서 compose 의 `x-logging` 앵커로 둘 다 journald 로 바꿨고, 아직 첫 배포 전이라 첫 배포부터 그대로 적용된다 (따로 할 일 없음). 나중에 로그 설정을 바꾸면 그때는 컨테이너를 다시 올려야 적용된다. 맥의 Docker Desktop 에는 journald 가 없어서 로컬에서 띄워 볼 때만 `deploy/docker/.env` 에 `LOG_DRIVER=json-file` 을 둔다.

## 4. 알림 설계와 안전

- **값은 나가지 않는다.** 알림의 글귀는 코드에 적힌 문장(`"run %s 의 job 이 %s 로 끝났다"`) 그대로이고 `%s` 를 채운 값은 서버 로그에만 있다. 누가 `log.error("%s", 원문)` 을 써도 아이 이야기가 Discord 로 가는 일이 **구조적으로** 없다. 예외도 종류 이름만 — 메시지에는 입력 원문이 섞일 수 있다 (pydantic 의 `input_value` 등). 어디서 났는지는 위치(로거:함수:줄)가 말해 주고, 자세한 건 서버 로그에서 그 줄을 찾는다.
- **요청 처리에 영향이 없다.** 로그 핸들러가 HTTP 를 직접 기다리면 그동안 서버의 모든 요청이 멈춘다. 그래서 큐에 넣고 스레드 하나가 보낸다. 전송이 실패하면 WARNING 한 줄 — Discord 쪽 실패(`WebhookError`)는 상태 코드만, 다른 예외는 종류 이름만. httpx 의 예외 메시지와 INFO 로그에는 웹훅 URL(비밀)이 통째로 있어서, 메시지는 번역하고(`WebhookError`) httpx 로거는 WARNING 에 못 박았다 (`logging_config`).
- **서버 종료를 붙들지 않는다.** flush 는 기한(4초)이 있고, 큐는 분당 상한(10건)만큼만 담고, 보내기 사이를 0.5초 띄운다(Discord 는 2초당 5건쯤부터 429). 못 보낸 건 다음 알림 머리에 "(앞서 N건 생략)" 으로 적는다.
- **웹훅 URL 은 비밀이다.** 아는 사람은 누구나 그 채널에 글을 올릴 수 있다. `.env` 에만 둔다 (§9). 모양(`https://discord.com/api/webhooks/…`)이 아니면 부팅에서 끊는다 — 채널 링크를 붙여 넣으면 Discord 가 200 을 주면서 아무것도 안 올려, 알림이 켜진 줄 알고 영영 못 받는다.
- **`APP_ENV=local` 이면 주소가 있어도 끈다.** 노트북의 오류가 팀 채널로 가지 않게. 테스트(`make test`)도 `.env` 에 무엇이 있든 보내지 않는다 (`tests/__init__.py`).

## 5. 알림이 못 잡는 것

api 안의 알림은 **api 프로세스 안의 `app.*` 로거**에만 달린다. Discord 가 조용하다고 다 괜찮은 건 아니다.

- 부팅 실패 · 컨테이너 죽음 · 재시작 반복 — api 안의 알림은 api 가 살아 있을 때만 말할 수 있다. 그래서 밖에서 본다: 서버 cron 이 1분마다 `deploy/scripts/health-alert.sh` 로 compose 컨테이너 전부의 healthcheck 결과를 읽어 **달라진 것만** 같은 웹훅으로 보낸다 (복구도). 웹훅 전송이 실패하면 상태를 저장하지 않아 다음 분에 다시 보낸다. 죽어 있는 동안 매분 울리지 않는다. docker 의 healthcheck 는 판정만 하고 통보는 안 하기 때문에 이 한 겹이 필요하다. 서버에 cron 한 줄 등록은 Session Manager 로 (README)
- `uvicorn` · `asyncio` · `sqlalchemy` 로거 — 대상이 아니다. `uvicorn.error` 의 "Exception in ASGI application" 은 `errors.py` 의 마지막 그물이 이미 ERROR 로 찍은 같은 예외라 일부러 뺐다
- 다른 프로세스 — `alembic upgrade`, `scripts/*` 는 `app.main` 을 불러오지 않아 알림이 없다. `app/workers/*`(cleanup · reminder_dispatch · profile_recompute)는 `app.main` 과 같은 순서로 `configure_alerts` 를 직접 달아 **알림이 간다** — 카드는 위와 같이 종류 이름만이고, 서버 로그에도 예외 메시지 없이 종류 · 위치만 남긴다 (`log_error_without_message` · #670)
- 프로세스를 fork 하는 실행기(gunicorn `--preload` 등) — 알림 스레드가 자식으로 넘어가지 않는다. uvicorn 단일 프로세스 전제다
- 로그인 전 화면(로그인 · 동의)의 브라우저 오류 — 인증 없는 창구를 두지 않아서다. 아무나 그 주소로 팀 채널을 울려 진짜 알림을 묻을 수 있다. 두 화면뿐이라 감수한다. 기기 요약(운영체제 · 주요 버전 · 앱/브라우저)을 로그에 남기므로 처리방침 ⑥ 에 한 구절을 더한다 (#166)

## 6. 열린 것

- 알림 부착을 import 시점이 아니라 FastAPI lifespan 으로 옮길지 — 지금은 `configure_logging()` 을 다시 부르면 핸들러가 조용히 사라진다(문서로만 막음). lifespan 이면 순서가 보장되고 import 에 부작용이 없지만, README 의 한 줄 확인 명령이 복잡해진다
- 도메인 (10-10 사기로 함) — 모니터는 IP 가 아니라 도메인으로 등록한다. EC2 를 중지 → 시작하면 공인 IP 가 바뀌는데(고정 IP 불가), 그때 도메인의 A 레코드만 고친다. 도메인이 생겨 HTTPS 를 Caddy 로 붙였고(`deploy/caddy/Caddyfile`), UptimeRobot 의 "SSL · 도메인 만료" 알림이 그때부터 쓸모 있다
- **시험 서버(dev)** — 11-06 띄움 ([deploy-v1.md](deploy-v1.md)). 꼬리표 · 컨테이너 감시 cron 은 거기서 정리했다. 아래는 그때 같이 본 것으로, Discord 채널 · UptimeRobot 은 아직 열려 있다:
  - 알림 꼬리표 — `deploy/docker/.env` 에 `APP_ENV=dev` 한 줄. compose 가 web(Next 서버 알림)에 넘기고 컨테이너 감시도 같은 줄을 읽는다 (10-10 이렇게 바꿈 — 코드 · cron 줄은 안 고친다). api 도 compose 가 같은 줄을 넘겨 `apps/api/.env` 의 `APP_ENV` 를 덮는다 (`docker-compose.deploy.yml`)
  - ~~api — 카카오 키 부팅 검사가 `prod` 만 막는다~~ — dev 도 막는다 (#310, `app/core/config.py` `_require_kakao_keys_on_servers`)
  - ~~컨테이너 감시가 dev 의 api 준비 상태(`/health/ready`)를 건너뛴다~~ — 이름을 `ktc4-api` 로만 찾던 것을 `ktc4-dev-api` 도 찾게 고쳤다 (#310). cron 줄은 [deploy-v1.md](deploy-v1.md) 준비 4
  - Discord — 시험 서버 알림을 같은 채널에 섞을지, 채널(웹훅)을 따로 둘지 정한다. 섞으면 꼬리표만으로 가른다
  - UptimeRobot — 시험 서버도 감시할지 (무료 플랜은 모니터 50개라 자리는 있다). 도메인은 하위 이름(예: `dev.<도메인>`)으로
- ~~배포 스크립트가 `compose up` 직후 healthcheck 를 기다렸다가 바로 알리는 것~~ — `deploy.sh` 가 `up --wait` 로 기다리고, 실패하면 롤백 후 워크플로를 빨간불로 끝낸다 ([deploy-v1.md](deploy-v1.md))
- 처리방침 ⑥ 의 기기 요약 구절과 로그 보관 기간 숫자 (#166, PM) · 법률 검토 때 "자동으로 남는 항목은 고지로 충분한지"

## 7. 서버 메트릭 — Grafana Cloud (#946)

§1 의 알림은 "죽었나 · 틀렸나"만 말한다. 서버가 **얼마나 버거운지**(dev · prod 가 4GB · 50GB 한 대를 나눠 쓴다, #747 · #780)는 추세로 봐야 보여서 숫자를 모은다.

| 무엇 | 메트릭 | 보고 무엇을 하나 |
|---|---|---|
| CPU · load | `node_cpu_seconds_total` · `node_load*` | 빌드 · 배포가 겹칠 때 서비스가 밀리는지 |
| 메모리 · swap · **OOM kill** | `node_memory_*` · `node_vmstat_pswp*` · `node_vmstat_oom_kill` | dev 폭주가 운영을 누르는지. 컨테이너가 메모리 때문에 죽었는지는 swap 보다 OOM kill 이 바로 보인다 |
| 디스크 (`/`) | `node_filesystem_avail_bytes` | 50GB 고정 — 이미지 · journald · DB 가 채우는 속도 |
| 트래픽 in/out | `caddy_http_request_size_bytes` · `caddy_http_response_size_bytes` | 사진 업로드 · 데이터 전송 비용. 서버 네트워크 장치(`node_network_*`)는 못 쓴다 — `/proc/net` 은 읽는 프로세스의 네트워크라 Alloy 컨테이너 것이 나오고, 서버 것을 보려면 `network_mode: host` 다(아래에서 막음). api 가 밖으로 부르는 S3 · LLM · 외부 API 트래픽은 여기 안 잡힌다 |
| 요청 · 응답 코드 | `caddy_http_requests_total` · `caddy_http_request_duration_seconds_count{code}` | 5xx 가 늘었는지. **사이트별(prod/dev × web/api)로는 나누지 않는다** — 아래 `per_host`. 운영만 보려면 아래 api 줄 |
| **운영 api** 요청 · 응답 코드 · 응답 시간 (#975) | `api_http_response_seconds_count` · `_sum` `{method, route, code}` | dev 가 안 섞인 운영 숫자 — **운영 5xx 알림은 이것으로 건다**. 라벨은 라우트 틀(`/children/{cid}` — `/api/v1` 이 안 붙는다) · 메서드 5개 + `OTHER` · 응답 코드 묶음(`5xx`). 없는 경로 · 안 맞는 메서드는 `route="unmatched"` 하나. 응답 시간은 응답 시작까지(SSE 제외 효과) · `/health` 는 세지 않는다. api 가 죽어 Caddy 가 내는 502 · 504 는 여기 안 잡힌다 — `up{job="prometheus.scrape.api"} == 0` 알림이 그 몫 |
| 응답 시간 | `caddy_http_response_duration_seconds` 의 `_sum / _count` (첫 바이트까지 평균) | p95 는 못 본다 — 구간(`_bucket`)은 조합마다 시계열이 늘어 버린다(아래). 전체 시간(`request_duration`)에는 SSE(`/runs/{rid}/events`) 연결 시간이 섞이고 Caddy 에 경로 라벨이 없어 뺄 수 없다 (쓸 만한지는 실측) |

- **어떻게** — Alloy(Grafana 의 수집 프로그램) 컨테이너 1개가 60초마다 서버 자원과 Caddy 의 내부 창구(`:2020`, 호스트 포트 없음 · Alloy 주소 172.30.0.11 만 받음), 운영 api 의 창구(`ktc4-api:9100` — Caddy 가 넘기는 `:8000` 과 다른 포트라 인터넷에 안 열리고, 역시 Alloy 주소만 받음 · `apps/api/app/api/metrics.py`)를 읽어 보낸다. dev api(`ktc4-dev-api`)도 창구를 열지만 긁지 않는다. 창구가 안 열려도 api 는 뜬다(경고만) 설정은 `deploy/alloy/config.alloy`, 값 3개(`GRAFANA_*`)는 prod `deploy/docker/.env` 에만
- **수집기는 쓰는 5개만** (cpu · loadavg · meminfo · filesystem · vmstat) — 서버는 ~100 줄(CPU 수에 비례)이다. 서버 메트릭의 `instance` 는 compose 의 `hostname: ktc4-ec2` 로 고정한다 — 없으면 컨테이너 ID 라 배포마다 시계열이 새로 생긴다. 수집마다 `sample_limit` (서버 2,000 · Caddy 50,000) — 넘으면 그 회차를 통째로 버린다
- 🚨 **Caddy 는 보낼 것만 고른다** (`prometheus.relabel "caddy"`) — Caddy 원본은 메서드 × 응답 코드 조합 하나마다 ~50줄이고, 인터넷 누구나 메서드 · 오류 코드를 섞어 보내 조합을 늘릴 수 있으며 카운터는 Caddy 재시작 전까지 남는다 (#946 재검증: 153 → 4,207줄 재현). 합계 · 개수만 남기고 구간 · Go 런타임을 버려 조합당 ~7줄로 줄이고, 1xx~5xx 밖의 응답 코드는 버린다. 임의 메서드는 Caddy 가 `OTHER` 하나로 묶는다. Caddy 한도는 Alloy 메모리 안전장치일 뿐이라 크게 둔다 — 낮으면 넘는 순간 Caddy 메트릭이 통째로 비고 그 위의 알림도 조용해진다. 그래서 **`up{job="prometheus.scrape.caddy"} == 0` 에도 알림을 건다**
- 🚨 **Alloy 에 서버 경로를 하나도 붙이지 않는다** (#946 보안 검토 H1 · 재현) — `/` 를 읽기 전용으로 붙여도 그 안의 `/run/docker.sock` 에 연결할 수 있고(읽기 전용은 소켓 연결을 막지 않는다), `/var/lib/docker/containers/*/config.v2.json` 에서 모든 컨테이너 환경변수(LLM 키 · DB 비밀번호)가, 서버의 `.env` · FCM 키 · TLS 개인키 · DB 파일까지 읽힌다. 필요도 없다 — Docker 는 `/proc/stat` · `meminfo` · `loadavg` · `vmstat` 을 컨테이너별로 나누지 않아 컨테이너 안에서 서버 전체 값이 나오고, 디스크는 컨테이너 루트(overlay)를 재면 Docker 가 쓰는 루트 디스크 값이 나온다 (로컬 실측). 그리고 비 root(65534) · `cap_drop: ALL` · `no-new-privileges` · 읽기 전용 · digest 고정 — 뚫려도 서버 파일 · 다른 컨테이너 비밀에는 닿지 않는다. 다만 edge 에 있어서 web · api(dev 포함)에 Caddy 를 거치지 않고 닿는다 — dev basic_auth · 로그인 차단을 비켜 가는 길이지만 edge 의 다른 컨테이너가 원래 가진 길과 같다
- 🚨 **Caddy `per_host` 를 켜지 않는다** (M1 · 재현) — 2.10 은 들어온 Host 헤더를 그대로 라벨로 써서, 인터넷 누구나 Host 만 바꿔 보내면 Host 하나에 시계열이 ~57개씩 늘고(무료 한도 초과) 상한 없는 Caddy 메모리가 커진다. 설정에 없는 Host 를 `_other` 로 묶는 보호가 2.11 에 들어왔지만([caddyserver/caddy#7306](https://github.com/caddyserver/caddy/pull/7306)) **2.11 도 막지 못한다** (#975 · 2.11.9 로컬 재현) — HTTPS 로 사이트 블록에 걸린 요청은 Host 원문을 포트까지 라벨로 쓴다. `Host: icatch.ai.kr:1001` · `icatch.ai.kr:abc` 처럼 포트 자리만 바꾸면 한 번에 ~52줄씩 새로 생기고 숫자가 아니어도 된다. 그래서 운영만 따로 보는 것은 Caddy 가 아니라 운영 api 메트릭(위 표)으로 한다 (11-11)
- **컨테이너별 메트릭(cadvisor)은 뺐다** — `docker.sock` 을 붙여야 하는데, `:ro` 여도 서버 root 이고 이 서버에서는 AWS 역할(호스트 네트워크 컨테이너를 띄워 imds-firewall 을 비켜 간다) · 다른 컨테이너의 비밀(DB 비밀번호 · API 키)까지 닿는다. 얻는 건 컨테이너별 메모리 하나라 서버 메모리가 튈 때 `docker stats` 로 본다 (11-10 PM)
- **감시 도구가 배포를 막지 않는다** — Alloy 는 profile `monitor` 라 배포 · 롤백의 `up --wait` 밖이다. `deploy.sh` 가 prod 배포 성공 뒤 `alloy validate` 를 거쳐 따로 띄우고, 실패하면 🟠 경고만 남긴다. 감시가 하루 비는 건 괜찮고 서비스가 멈추는 건 안 된다. `GRAFANA_*` 가 비어도 Alloy 는 죽지 않고 보내기만 재시도한다 (실측). dev 는 띄우지 않는다 — 서버에 하나면 다 본다
- **`network_mode: host` 를 쓰지 않는다** — imds-firewall 은 컨테이너에서 나가는 길만 막는다. edge 의 자동 주소(.128 위)는 막힌다
- **알림** — Grafana Alerting → 지금 Discord 채널(`infra-alert` 와 같은 곳). 채널이 늘면 덜 본다. 🚨 순간값이 아니라 **5분 이상 계속**(pending period 5m)일 때만 — 배포 중 잠깐 튀는 건 안 오게. 규칙은 Grafana 화면에서 만들고 코드로 두지 않는다
  - 🚨 **Alloy 가 죽으면 데이터가 아예 안 와서 `up == 0` 도 안 울린다** — 규칙마다 "No data" 를 Alerting 으로 둔다(OK 로 두면 조용하다). Alloy 컨테이너의 죽음 · 재시작 반복은 컨테이너 감시(`health-alert.sh`)가 이미 infra-alert 로 알린다 — compose 의 모든 컨테이너를 본다
  - `up` 의 job 이름은 Caddy 가 `prometheus.scrape.caddy`, 운영 api 가 `prometheus.scrape.api`, 서버가 `integrations/unix` 다 (Alloy 가 붙인다)
  - 🚨 배포 때 api 를 바꾸는 동안(drain 최대 ~110s + 기동) api 수집이 빈다 — pending 5m 이라 울리지 않는다
- **안 하는 것** — 로그(Loki) · 트레이스 · DB · Redis exporter. api 의 도메인 지표(run 소요 시간 · 부분 결과 횟수)는 다음 이슈 — #975 는 요청 · 코드 · 시간만
