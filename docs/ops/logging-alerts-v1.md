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
| **적재(모으기)** | 앱이 아니라 **docker 로그 드라이버 `journald`** | 앱은 stderr 로만 찍고, 어디에 모을지는 실행 환경이 정한다 (§2) |
| **알림** | `app.*` 로거의 **ERROR 이상 → Discord 웹훅**, 앱 코드 (`app/core/alerts.py` · `app/integrations/discord.py`) | docker 에는 "로그 줄을 골라 사람에게 보내는" 기능이 없다 |
| **알림 본문** | 환경 · 레벨 · 로거:함수:줄 · **코드에 적힌 로그 글귀** · 예외 **종류 이름** · 시간 | 값(args) · 원문 · 트레이스백 · 예외 메시지는 밖으로 안 나간다 (§3) |
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

> 지금 `deploy/docker/docker-compose.deploy.yml` 의 web · redis 도 json-file 이라 **배포할 때마다 두 컨테이너의 로그가 지워지고 있다.** api 를 올릴 때 둘에도 같은 설정을 넣는 것이 좋다 (명성님).

## 4. 알림 설계와 안전

- **값은 나가지 않는다.** 알림의 글귀는 코드에 적힌 문장(`"run %s 의 job 이 %s 로 끝났다"`) 그대로이고 `%s` 를 채운 값은 서버 로그에만 있다. 누가 `log.error("%s", 원문)` 을 써도 아이 이야기가 Discord 로 가는 일이 **구조적으로** 없다. 예외도 종류 이름만 — 메시지에는 입력 원문이 섞일 수 있다 (pydantic 의 `input_value` 등). 어디서 났는지는 위치(로거:함수:줄)가 말해 주고, 자세한 건 서버 로그에서 그 줄을 찾는다.
- **요청 처리에 영향이 없다.** 로그 핸들러가 HTTP 를 직접 기다리면 그동안 서버의 모든 요청이 멈춘다. 그래서 큐에 넣고 스레드 하나가 보낸다. 전송이 실패하면 WARNING 한 줄 — Discord 쪽 실패(`WebhookError`)는 상태 코드만, 다른 예외는 종류 이름만. httpx 의 예외 메시지와 INFO 로그에는 웹훅 URL(비밀)이 통째로 있어서, 메시지는 번역하고(`WebhookError`) httpx 로거는 WARNING 에 못 박았다 (`logging_config`).
- **서버 종료를 붙들지 않는다.** flush 는 기한(4초)이 있고, 큐는 분당 상한(10건)만큼만 담고, 보내기 사이를 0.5초 띄운다(Discord 는 2초당 5건쯤부터 429). 못 보낸 건 다음 알림 머리에 "(앞서 N건 생략)" 으로 적는다.
- **웹훅 URL 은 비밀이다.** 아는 사람은 누구나 그 채널에 글을 올릴 수 있다. `.env` 에만 둔다 (§9). 모양(`https://discord.com/api/webhooks/…`)이 아니면 부팅에서 끊는다 — 채널 링크를 붙여 넣으면 Discord 가 200 을 주면서 아무것도 안 올려, 알림이 켜진 줄 알고 영영 못 받는다.
- **`APP_ENV=local` 이면 주소가 있어도 끈다.** 노트북의 오류가 팀 채널로 가지 않게. 테스트(`make test`)도 `.env` 에 무엇이 있든 보내지 않는다 (`tests/__init__.py`).

## 5. 알림이 못 잡는 것

알림은 **api 프로세스 안의 `app.*` 로거**에만 달린다. Discord 가 조용하다고 다 괜찮은 건 아니다.

- 부팅 실패 — `.env` 설정 오류로 서버가 뜨지 못하면 핸들러도 없다. compose 의 `restart` 가 되살리기를 반복할 뿐이다 → healthcheck 와 배포 스크립트에서 "api 가 뜨지 않았다"를 같은 웹훅으로 보내는 것으로 (#166, api 가 compose 에 합류할 때)
- `uvicorn` · `asyncio` · `sqlalchemy` 로거 — 대상이 아니다. `uvicorn.error` 의 "Exception in ASGI application" 은 `errors.py` 의 마지막 그물이 이미 ERROR 로 찍은 같은 예외라 일부러 뺐다
- 다른 프로세스 — `alembic upgrade`, `scripts/*`, 앞으로의 `app/workers/*` 는 `app.main` 을 불러오지 않아 알림이 없다
- 프로세스를 fork 하는 실행기(gunicorn `--preload` 등) — 알림 스레드가 자식으로 넘어가지 않는다. uvicorn 단일 프로세스 전제다

## 6. 열린 것

- 알림 부착을 import 시점이 아니라 FastAPI lifespan 으로 옮길지 — 지금은 `configure_logging()` 을 다시 부르면 핸들러가 조용히 사라진다(문서로만 막음). lifespan 이면 순서가 보장되고 import 에 부작용이 없지만, README 의 한 줄 확인 명령이 복잡해진다
- 부팅 실패 알림 (§5 첫 항목) — 배포 compose · 스크립트 쪽 (#166)
- web 서비스의 로그 드라이버 (§3 인용)
