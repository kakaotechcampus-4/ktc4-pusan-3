# 암호화 현황 v1 — 전송 · 저장 구간과 하지 않는 것

문서 목적: 개인정보 처리방침이 보호자에게 약속한 "전송 구간 · 저장 구간 암호화"가 **실제로 어디서 어떻게 지켜지는지**,
그리고 무엇을 일부러 하지 않는지를 확인 근거와 함께 고정한다.

작성일: 2026-11-04
담당: 김명성
선행: 처리방침 `apps/api/app/domains/policy/privacy_policy/` (2026-10-15 · 2026-11-03 판의 "전송 구간 · 저장 구간의 암호화") · #670

## 0. 한눈에

```
보호자 폰 ──① 전송──▶ Caddy ──② 서버 안──▶ api ──▶ Postgres ──③ 디스크──▶ EBS 볼륨
            HTTPS(TLS)·HSTS      평문(의도)                       EBS 암호화
```

| 구간 | 장치 | 상태 | 막는 것 | 못 막는 것 |
| --- | --- | --- | --- | --- |
| ① 전송 | TLS — Caddy 자동 HTTPS (`deploy/caddy/Caddyfile`) | 동작 중 | 와이파이 · 통신사 등 중간에서 엿보기 · 가짜 서버 | — |
| ① 전송의 첫 요청 | HSTS `max-age=86400` (1일) | #670 에서 추가 | `http://` 로 먼저 연 첫 요청을 가로채 https 로 못 가게 붙잡는 공격 | 그 브라우저가 **처음** 오는 순간 (§2) |
| ② 서버 안 | 없음 — 같은 호스트의 docker 네트워크 | 의도된 평문 | — | 서버 권한을 얻은 사람 |
| ③ 디스크 | EBS 볼륨 암호화 (AWS 관리 키) | **확인함** (§1) | 디스크 · 스냅샷이 밖으로 새는 경우 | 서버에 로그인했거나 api 권한을 얻은 사람 — 그건 접근 권한 관리 몫 |

## 1. 디스크 암호화 확인 기록

처리방침에는 이미 "서버의 디스크는 암호화되어 있습니다"라고 적혀 있었지만 아무도 확인하지 않았다. 확인했고 사실과 맞다.

| 항목 | 값 |
| --- | --- |
| 확인일 | 2026-11-04 |
| 대상 | 배포 서버 `i-0ea74f1ead5a8d784` (ap-northeast-2) — 루트 볼륨 1개 `vol-03ba0313e7852de3e` |
| 결과 | `Encrypted: True` |
| 확인 경로 | 인스턴스 안에서 인스턴스 역할(`ktc-ec2-ssm-role`)로 AWS CLI 조회 (아래). 확인 뒤 CLI 는 지웠다 |
| 기록 | #670 이슈 댓글 |

```bash
# 인스턴스 안에서 — ID · 리전은 메타데이터에서 (인스턴스 밖에서는 이 주소가 안 열린다)
TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
sudo snap install aws-cli --classic
aws ec2 describe-volumes --region $REGION --filters Name=attachment.instance-id,Values=$ID \
  --query 'Volumes[].{id:VolumeId,encrypted:Encrypted}' --output table
sudo snap remove aws-cli
```

- 서버 안에서 `lsblk` 등으로는 구분되지 않는다 — EBS 는 읽고 쓸 때 AWS 가 투명하게 풀고 잠근다.
- 🚨 **다시 확인할 때**: 인스턴스를 새로 만들거나 · 옮기거나 · 볼륨을 새로 붙일 때. 계정의 "새 볼륨 기본 암호화"
  설정은 앞으로 만들 볼륨에만 걸리므로, 그것만으로 지금 볼륨이 암호화됐다고 볼 수 없다.
- 2026-11-03 판 처리방침은 "활동 사진 파일의 저장"을 AWS 위탁에 넣었다. 사진 저장은 아직 코드가 없다 —
  만들 때 저장 위치(같은 EBS 인지, S3 라면 기본 암호화 SSE-S3 가 걸리는지)를 이 표에 더한다.

## 2. HSTS

`Strict-Transport-Security: max-age=86400` 을 두 사이트(`icatch.ai.kr` · `api.icatch.ai.kr`)의 https 응답에 붙인다.

- 브라우저는 이 헤더를 받으면 그 도메인을 **기간 동안 https 로만** 연다. 사용자가 `http://` 를 쳐도 네트워크에
  내보내기 전에 https 로 바꾼다. 헤더는 응답마다 붙어서 접속할 때마다 기한이 다시 1일로 늘어난다.
- **1일로 시작한 이유** — 한 번 보낸 기간은 보호자 브라우저에 남아 서버에서 되돌릴 수 없다. 그 기간에 https 가
  깨지면(인증서 만료 · 설정 실수) 우회 버튼 없이 접속이 막힌다. 문제없이 돌면 늘린다 (예: 1년 `31536000`).
- **아직 하지 않는 것** — `includeSubDomains`(모든 하위 도메인이 https 여야 한다) · `preload`(브라우저에 목록을
  넣어 첫 방문까지 막는다. 빠지는 데 몇 달이 걸린다).
- 앱(웹뷰)은 처음부터 `https://` 주소를 열어 영향이 없다. 의미가 있는 것은 웹 브라우저에서 주소를 직접 치거나
  옛 `http://` 링크를 누를 때다.

## 3. 하지 않는 것과 이유

| 하지 않는 것 | 이유 |
| --- | --- |
| 채팅 E2E 암호화 | 서버가 원문을 읽어야 서비스가 성립한다 — 관찰을 나누고(LLM) 기억을 만든다 |
| 컬럼 단위 암호화 (pgcrypto) | 요구하는 문서가 없고, 키가 같은 서버에 있어 디스크 암호화보다 얻는 것이 작다 |
| api ↔ db · redis 사이 TLS | 같은 호스트의 docker 브리지 — 밖으로 나가는 구간이 없다 |

## 4. 로그와 예외 — 원문이 새는 다른 길

암호화와 별개로, 원문이 로그로 새면 위 장치가 의미 없어진다 (루트 CLAUDE.md §2 "로그에 원문 대신 id").

- 처리되지 않은 예외는 **종류와 코드 위치만** 남긴다 — 메시지에 입력 원문이 섞일 수 있다
  (`apps/api/app/api/errors.py` 마지막 그물 · #670). DB 오류 처리기와 같은 방식이다.
- Starlette 이 그 예외를 다시 던져 uvicorn 이 "Exception in ASGI application" 으로 한 번 더 찍는 줄도
  메시지를 뺀다 (`apps/api/app/core/logging_config.py` `_HideExceptionMessage`).
- 코드 위치(traceback)에는 **소스 코드 줄**이 보인다. 값이 아니라 코드라 원문은 아니지만,
  예외 메시지를 만드는 줄에 상수로 개인정보를 쓰지 않는다.
