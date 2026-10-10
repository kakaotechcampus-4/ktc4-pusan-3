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
| **알림 본문** | 환경 · 레벨 · 로거:함수:줄 · **코드에 적힌 로그 글귀** · 예외 **종류 이름** · 시간. 값을 내보내려면 `extra={"alert_detail"}` 로 표시 — 서버가 모양을 검사한 값만 | 값(args) · 원문 · 트레이스백 · 예외 메시지는 밖으로 안 나간다 (§3) |
| **세기 · 다음 할 일** | 머리에 🔴(지금) · 🟠(오늘) · 🟡(내일) · 🟢(복구), 셋째 줄에 다음에 칠 명령 | 현업의 심각도 구분 · 런북을 가장 작게. 전부 같은 세기면 알림 피로로 다 무시한다. 🔴 만 `@here` 로 폰을 울릴지는 실사용자 뒤에 정한다 |
| **구별** | 채널 하나, 보내는 이름이 출처별 — `api-alert` · `browser-alert` · `infra-alert` (`extra={"alert_source"}`) | 웹훅 · 채널을 늘리지 않는다. 쪼개고 싶으면 그때 URL 을 하나 더 |
| **화면 오류** | 오류 바운더리 → `POST /client-errors`(로그인한 보호자만) → ERROR 로그 → 같은 알림 | 보호자 폰의 오류는 서버로 오지 않았다. 값은 종류 · digest · 경로 · 기기 요약뿐이고 모양을 서버가 검사한다. DB 에 넣지 않는다 (처리방침 ⑥) |
| **web 서버 오류** | Next `src/instrumentation.ts` 의 `onRequestError` → 웹훅으로 직접 (`web-alert`). 종류 · digest · 경로 · 라우트만, 분당 상한, 개발 모드는 끔 | Next 서버엔 보호자 토큰이 없어 `/client-errors` 를 못 쓴다. 웹 컨테이너에 `ALERT_WEBHOOK_URL` 을 compose 가 넘긴다 |
| **컨테이너 감시** | 서버 cron 1분 · `deploy/scripts/health-alert.sh` — docker healthcheck 결과가 **달라졌을 때만** 같은 웹훅으로 | api 가 못 뜨거나 redis 가 죽은 건 api 안의 알림이 말할 수 없다 (§5) |
| **서버 밖 가동 감시** | UptimeRobot 무료 — 5분마다 api `/health` · web `/` 를 밖에서 찔러 Discord 로 (웹훅은 따로 `uptime-alert`) | 서버 안의 cron 은 EC2 자체가 죽으면 같이 말을 못 한다. 코드 없음 · 보호자 정보 안 감 (응답 코드만). 무료 플랜에 Discord 연동 포함, 비상업 용도 |
| **SaaS(Sentry 등)** | 안 쓴다 | 요청 본문 · 사용자 정보가 밖으로 가는 도구는 §10 결정 전에는 못 쓴다 |

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
- 다른 프로세스 — `alembic upgrade`, `scripts/*`, 앞으로의 `app/workers/*` 는 `app.main` 을 불러오지 않아 알림이 없다
- 프로세스를 fork 하는 실행기(gunicorn `--preload` 등) — 알림 스레드가 자식으로 넘어가지 않는다. uvicorn 단일 프로세스 전제다
- 로그인 전 화면(로그인 · 동의)의 브라우저 오류 — 인증 없는 창구를 두지 않아서다. 아무나 그 주소로 팀 채널을 울려 진짜 알림을 묻을 수 있다. 두 화면뿐이라 감수한다. 기기 요약(운영체제 · 주요 버전 · 앱/브라우저)을 로그에 남기므로 처리방침 ⑥ 에 한 구절을 더한다 (#166)

## 6. 열린 것

- 알림 부착을 import 시점이 아니라 FastAPI lifespan 으로 옮길지 — 지금은 `configure_logging()` 을 다시 부르면 핸들러가 조용히 사라진다(문서로만 막음). lifespan 이면 순서가 보장되고 import 에 부작용이 없지만, README 의 한 줄 확인 명령이 복잡해진다
- 도메인 (10-10 사기로 함) — 모니터는 IP 가 아니라 도메인으로 등록한다. EC2 를 중지 → 시작하면 공인 IP 가 바뀌는데(고정 IP 불가), 그때 도메인의 A 레코드만 고친다. 도메인이 생기면 HTTPS 를 붙일 수 있고, UptimeRobot 의 "SSL · 도메인 만료" 알림이 그때부터 쓸모 있다
- ⏰ **시험 서버(dev · staging)를 띄우면** — 10-10 팀에서 얘기 중. 그때 고칠 것:
  - Next 서버 알림의 꼬리표 — `apps/web/src/instrumentation.ts` 가 `prod` 로 박혀 있다. 시험 서버도 production 빌드라 `[prod]` 로 찍힌다 → compose 가 web 에 `APP_ENV` 를 넘기고(api 와 같은 이름) 그걸 읽는다
  - 컨테이너 감시 — 시험 서버의 cron 줄 앞에 `ALERT_ENV=dev` (`deploy/scripts/health-alert.sh` 머리말)
  - api — 서버 `.env` 의 `APP_ENV=dev` 면 꼬리표는 저절로 맞다. 다만 카카오 키 부팅 검사가 지금 `prod` 만 막는다 (`app/core/config.py` `_require_kakao_keys_in_prod` — "dev 서버가 생기면 다시 판단" 주석)
  - Discord — 시험 서버 알림을 같은 채널에 섞을지, 채널(웹훅)을 따로 둘지 정한다. 섞으면 꼬리표만으로 가른다
  - UptimeRobot — 시험 서버도 감시할지 (무료 플랜은 모니터 50개라 자리는 있다). 도메인은 하위 이름(예: `dev.<도메인>`)으로
- 배포 스크립트가 `compose up` 직후 healthcheck 를 기다렸다가 바로 알리는 것 — 지금은 cron 이 1분 안에 잡으므로 있으면 좋은 것 (#166)
- 처리방침 ⑥ 의 기기 요약 구절과 로그 보관 기간 숫자 (#166, PM) · 법률 검토 때 "자동으로 남는 항목은 고지로 충분한지"
