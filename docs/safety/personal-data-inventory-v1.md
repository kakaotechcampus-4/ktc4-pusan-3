# 개인정보 처리 현황 — 우리가 실제로 무엇을 다루는가

문서 목적: 개인정보 처리방침에 쓸 **사실**을 코드에서 뽑아 한곳에 모은다. 방침 문구는 여기 없다 — [privacy-decisions-v1.md](privacy-decisions-v1.md) 의 결정이 닫힌 뒤에 쓴다.
기준 브랜치: `develop` (b4827a0)
작성일: 2026-09-17
담당: 박재형 (PM)
선행 문서: [tech-spec §5-2 NF-04·05](../overview/tech-spec.md) · [auth-kakao-v1 §7](../api/auth-kakao-v1.md) · 루트 [CLAUDE.md §2 · §9](../../CLAUDE.md)

> **읽는 법.** 모든 줄에 근거 파일이 붙어 있다. 근거가 없는 줄은 이 문서에 넣지 않는다 — 추측을 사실처럼 적는 순간 방침이 거짓이 된다.
> 아직 정해지지 않은 칸은 `🔸 미정` 으로 두고 어느 결정이 그것을 닫는지 적는다.

---

## 0. 세 줄 요약

1. **수집이 매우 좁다.** 보호자는 카카오 회원번호 하나, 아이는 별명·생일·관계. 실명·연락처·주소·IP·접속기록을 받지 않는다.
2. **민감정보를 다룬다.** 알레르기·건강 기록은 개인정보보호법 제23조 민감정보이고, 별도 동의로 이미 분리돼 있다.
3. **외부로 나가는 것이 둘 있다.** 보호자가 쓴 문장 원문과 급식표 사진 원본. 둘 다 Elice ML API 를 거치고, **그 뒤에 누가 있는지는 확인되지 않았다.**

---

## 1. 수집 항목

정보주체가 둘이다. **아이**(만 14세 미만)와 **보호자**. 아이 정보는 보호자가 법정대리인으로서 동의한다 (제22조의2).

### 1-1. 보호자

| 항목 | 저장 위치 | 유형 | 동의 항목 | 근거 |
|---|---|---|---|---|
| 카카오 회원번호 | `auth_identity.provider_user_id` | 식별자 | `privacy_account` | [identity/models.py:34-48](../../apps/api/app/domains/identity/models.py) |
| 별명 | `parent.nickname` (nullable) | 일반 | `privacy_account` | [identity/models.py:27-31](../../apps/api/app/domains/identity/models.py) |
| 세션 토큰 | `session.token_hash` (SHA-256, 원문 없음) | 식별자 | — | [identity/models.py:51-79](../../apps/api/app/domains/identity/models.py) |
| 아이와의 관계 | `parent_child.relation` (엄마/아빠/조부모/돌봄/기타) | 일반 | `child_basic` | [child/models.py:20-35](../../apps/api/app/domains/child/models.py) |

별명은 카카오에서 가져오지 않는다. 설정 화면에서만 받고, 지금은 그 화면이 없어 사실상 비어 있다 ([auth-kakao-v1.md §6](../api/auth-kakao-v1.md)).

### 1-2. 아이

| 항목 | 저장 위치 | 유형 | 동의 항목 | 근거 |
|---|---|---|---|---|
| 별명 | `child.nickname` | 일반 | `child_basic` | [child/models.py:38-47](../../apps/api/app/domains/child/models.py) |
| 생일 | `child.birth_date` | 일반 | `child_basic` | 〃 |

나이는 저장하지 않고 생일로 매번 계산한다. 성별·키·몸무게는 **컬럼 자체가 없다** ([food/store/ports.py:9](../../apps/api/app/agents/food/store/ports.py) — "키·몸무게는 수집 범위 결정 전까지 비움").

### 1-3. 민감정보 (제23조)

| 항목 | 저장 위치 | 동의 항목 | 근거 |
|---|---|---|---|
| 알레르기·만성질환·식이제한 (이름·별칭·심각도·반응·관리법·메모) | `health_safety` | `child_health` | [safety/models.py:33-75](../../apps/api/app/domains/safety/models.py) |
| 건강 관찰 (발화 원문·증상·부위·의심 원인·조치) | `observation_health` | `child_health` | [observation/models.py:111-138](../../apps/api/app/domains/memory/observation/models.py) |

심각도에 `anaphylaxis`(아나필락시스)·`emergency` 값이 있다. 잘못 다루면 되돌릴 수 없는 위해로 이어지는 정보다.

건강 관찰만 `embedding` 컬럼이 없다. 승격 파이프라인 밖이라 벡터화 대상이 아니다 ([observation/models.py:112](../../apps/api/app/domains/memory/observation/models.py)).

### 1-4. 생활 기록

| 테이블 | 개인정보에 해당하는 컬럼 | 동의 항목 | 근거 |
|---|---|---|---|
| `observation_food` | `raw_text`(발화 원문), 먹은 것·양·반응 | `child_basic` | [observation/models.py:80-85](../../apps/api/app/domains/memory/observation/models.py) |
| `observation_education` | `raw_text`, 주제·형태·시간·몰입도 | `child_basic` | 〃 `:88-96` |
| `observation_activity` | `raw_text`, 활동, **`location`(장소)**, **`companions`(동반자)** | 🔸 고지 범위 밖 → 결정 2-B | 〃 `:99-108` |
| `profile_affinity` | 성향 요약, `embedding` Vector(1536) | `child_basic` | [profile/models.py:30-44](../../apps/api/app/domains/memory/profile/models.py) |
| `event` · `event_item` · `reminder` | 일정 제목·시각·분류(건강 포함)·준비물 | `child_basic` | [schedule/models.py:36-86](../../apps/api/app/domains/schedule/models.py) |
| `calendar` | `diary_text`, `image_urls`(사진 주소 배열) | `child_basic` | 〃 `:89-103` |
| `suggestion` | 추천 내용·근거 참조 | `child_basic` | [suggestion/models.py:33-58](../../apps/api/app/domains/suggestion/models.py) |
| `correction` | 보호자의 정정 판정 | `child_basic` | [correction/models.py:20-43](../../apps/api/app/domains/correction/models.py) |

**🔸 고지가 빠진 것 두 가지.** 동의 화면의 `child_basic` 설명은 "별명·생일·관계"까지만 적는다 ([consent.ts](../../apps/web/src/lib/consent.ts)). 그런데 실제로는 아래가 더 들어온다.

- **활동 관찰의 장소와 동반자.** 동반자는 다른 아이나 교사, 즉 **동의를 받은 적 없는 제3자**다.
- **기관명.** 급식표를 읽으면 `institution_name` 이 남는다 ([meal_plan/schema.py:80](../../apps/api/app/providers/meal_plan/schema.py)). "이 아이가 어느 기관에 다니는가" 가 된다.
- **모든 `raw_text`.** 보호자가 쓴 문장에 아이 이름·기관명·병원명·다른 아이 이름이 들어 있으면 그대로 저장된다.

→ 결정 2-B 에서 "고지하고 받을지, 안 받을지" 를 정한다.

### 1-5. 동의 기록

| 테이블 | 무엇 | 근거 |
|---|---|---|
| `consent` | 동의·철회 이력. 고치거나 지우는 함수가 없고 행만 쌓는다. 마지막 행이 현재 상태 | [consent/models.py:47-103](../../apps/api/app/domains/consent/models.py) · [repository.py:3-6](../../apps/api/app/domains/consent/repository.py) |
| `consent_retention` | 계정·아이를 지운 뒤에도 남기는 증빙. `purge_at = 삭제일 + 365일` | [consent/models.py:106-141](../../apps/api/app/domains/consent/models.py) · [retention.py:24](../../apps/api/app/domains/consent/retention.py) |
| `policy_version` | 약관·방침 본문과 버전. 한번 만들면 고치지 않고 새 행을 만든다 | [policy/models.py:11-33](../../apps/api/app/domains/policy/models.py) |

**365일에는 법적 근거가 없다.** 코드 주석이 그렇게 적고 있다 — "법에서 정한 기간이 아니라 현재 서비스가 정한 정책이다. 출시 전에 법적 근거와 보관 항목을 최종 확인해야 한다" ([retention.py:27-29](../../apps/api/app/domains/consent/retention.py)). 🔸 결정 1.

---

## 2. 수집하지 않는 것

방침에 "수집하지 않습니다" 를 쓰려면 근거가 필요하다. 아래는 전부 코드로 확인했다.

| 항목 | 확인 방법 |
|---|---|
| 실명 · 주민등록번호 · 주소 · 연락처 · 이메일 | 컬럼 없음. 카카오에서도 안 받음 |
| 이메일 · 프로필 사진 | 회원번호만 주는 API 를 일부러 골랐다 — [kakao/client.py:81-100](../../apps/api/app/integrations/kakao/client.py) ("`/v2/user/me` 가 아니라 `access_token_info` 다. 전자는 이메일·프로필까지 실어 오는데 NF-04 는 최소 수집을 요구한다") |
| 아이 성별 · 키 · 몸무게 | 컬럼 없음 |
| IP 주소 · User-Agent · 기기 정보 · 접속 기록 | 전 코드 검색 결과 0건. 해당 테이블도 없음 |
| 위치정보 (GPS) | 수집 코드 없음. `observation_activity.location` 은 보호자가 문장으로 쓴 장소명이지 좌표가 아니다 |
| 광고 식별자 · 분석 도구 | 의존성 없음. 프론트 외부 오리진 요청 0건 ([design-system-v1.md](../web/design-system-v1.md)) |

**받지만 저장하지 않는 것**

| 항목 | 처리 | 근거 |
|---|---|---|
| 카카오 접근 토큰 · 갱신 토큰 | 회원번호를 얻고 즉시 버린다 | [kakao/client.py:58-79](../../apps/api/app/integrations/kakao/client.py) |
| 카카오 인가 코드 | 교환 즉시 무효 | [auth-kakao-v1.md §7-4](../api/auth-kakao-v1.md) |
| 세션 토큰 원문 | 해시만 저장 | [identity/models.py:73](../../apps/api/app/domains/identity/models.py) |
| 사진 재분석 힌트 문구 | 저장하지 않음 | [api-interface-v1.html](../api/api-interface-v1.html) §09 |
| 급식표 원본 이미지 | 저장소가 없다. 읽고 버린다 | [image_ocr.py](../../apps/api/app/providers/meal_plan/image_ocr.py) — 디스크 쓰기 없음. 🔸 결정 6 |

**한 가지 예외.** 초대 토큰만 원문으로 저장된다 ([child/models.py:60](../../apps/api/app/domains/child/models.py)). 다른 토큰은 전부 해시다. 방침과 별개로 고칠 항목.

---

## 3. 외부로 나가는 것

전송 지점은 셋뿐이다. 전 코드 검색으로 확인했다.

### 3-1. Elice ML API → 대화형 모델

| 무엇이 | 언제 | 근거 |
|---|---|---|
| **보호자가 쓴 문장 원문 전체** | 기록을 남길 때마다 | [pipeline.py:189](../../apps/api/app/agents/pipeline.py) (의도 분류) · [memory/agent.py:113-115](../../apps/api/app/agents/memory/agent.py) (관찰 추출) |
| 같은 문장의 조각이 한 번 더 | 첫 분류가 어긋나 다시 보낼 때 | [pipeline.py:358-366](../../apps/api/app/agents/pipeline.py) — 주석에 "모델 입력이라 조각 원문이 들어간다" |
| 알레르기 이름 | 식단 추천 시 (**아직 미구현**) | [food/prompt.py](../../apps/api/app/agents/food/prompt.py) — `NotImplementedError`. 🔸 결정 3-A |

**아이 별명·생일은 프롬프트에 넣지 않는다.** 시스템 프롬프트에 들어가는 것은 현재 시각과 시간대뿐이다 ([memory/prompt.py:53-60](../../apps/api/app/agents/memory/prompt.py)). 아이 이름이 나가는 경로는 **보호자가 문장 안에 직접 쓴 경우 하나뿐**이고, 그걸 지우거나 바꾸는 코드는 없다.

### 3-2. Elice ML API → Gemini (급식표 사진)

| 사실 | 근거 |
|---|---|
| 사진 원본이 **축소·재인코딩 없이** 통째로 전송된다 | [image_ocr.py:117-140](../../apps/api/app/providers/meal_plan/image_ocr.py) — docstring 에 명시 |
| **같은 사진이 한 번의 읽기에 2회** 전송된다 (출력 길이 상한 때문에 1~15일·16~31일로 나눔) | 〃 `:36` |
| 게이트웨이 프롬프트 캐시가 켜져 있어 **최대 1시간** 남을 수 있다 | 〃 `:247` · [meal_ocr_eval.py:186](../../apps/api/scripts/meal_ocr_eval.py) |
| 부르는 모델은 `gemini-3.1-pro-preview` | [core/config.py:37](../../apps/api/app/core/config.py) |

급식표 사진에는 보통 아이 정보가 없다. 다만 기관명이 찍혀 있고, 보호자가 다른 것을 잘못 올릴 가능성이 있다.

### 3-3. 카카오 (로그인)

나가는 것은 인증 자격증명뿐이다. 아이 정보·발화·건강정보는 카카오로 가지 않는다 ([kakao/client.py:29-32](../../apps/api/app/integrations/kakao/client.py)).

### 3-4. 🔸 확인되지 않은 것 — 이 문서의 가장 큰 구멍

**Elice 뒤에 누가 있는지 모른다.**

Gemini 는 구글 모델이라 Elice 가 자체 장비로 돌릴 수 없다. 구글 서버로 넘어간다면 **국외 이전**이고, 그러면 방침에 이전받는 자의 명칭·연락처·국가·이용목적·보유기간을 적어야 한다 (제28조의8 제2항). 지금은 그 칸을 채울 수 없다.

- 저장소에서 제공사 이름이 나오는 유일한 곳은 가격표의 모델 ID 접두사다 ([meal_ocr_eval.py:35-39](../../apps/api/scripts/meal_ocr_eval.py) — `anthropic/`, `google/`)
- Elice 공개 소개 페이지에도 데이터 처리 위치·보관·학습 정책 설명이 없다
- [tech-spec 리스크 ⑤](../overview/tech-spec.md) 가 이미 "외부 사업자 보관·학습 정책 확인 / 국외 처리 여부도 미확인" 을 미해결로 적어 뒀다

→ **PM 이 Elice 에 서면으로 물어야 한다.** 🔸 결정 3-C.

**단, 이 구멍은 출시 시점에는 없어진다.** Elice 팀 크레딧은 개발 기간 한정이고 **출시할 때는 API 를 직접 결제한다** (2026-09-17, PM). 결제하는 회사가 곧 수탁자라 이름과 소재국이 확정된다. 그래서 방침에 쓸 내용은 **"출시 때 어느 회사 모델을 쓰는가"** 로 결정되고, 국내 제공사를 고르면 국외 이전 항목 자체가 사라진다. 🔸 결정 3-C-2.

Elice 질의는 그래도 보낸다. 개발 중에 실제 사용자 데이터가 들어오면 그 기간의 처리 사실이 되기 때문이다.

---

## 4. 저장 위치

| 무엇 | 어디에 | 근거 |
|---|---|---|
| 모든 테이블 | PostgreSQL 18 + pgvector 0.8.6 한 곳 | [docker-compose.yml:8](../../deploy/docker/docker-compose.yml) · [apps/api/CLAUDE.md](../../apps/api/CLAUDE.md) |
| 벡터 임베딩 | 같은 DB 의 같은 행에 있는 컬럼. 별도 벡터 DB 없음 | [observation/models.py:61](../../apps/api/app/domains/memory/observation/models.py) |
| 세션·1회용 코드 | 같은 DB. Redis 없음 | [identity/models.py:82-98](../../apps/api/app/domains/identity/models.py) |
| 사진 파일 | **저장소가 없다.** `calendar.image_urls` 에 주소 문자열 칸만 있다 | [schedule/models.py:103](../../apps/api/app/domains/schedule/models.py) |
| 서버 위치 | 🔸 **문서에 없다.** 인스턴스 종류 언급이 하나 있을 뿐 리전이 없다 | [auth-kakao-v1.md](../api/auth-kakao-v1.md) |

임베딩을 만드는 코드는 아직 없다. 컬럼만 있고 비어 있다. **외부 임베딩 API 를 쓰게 되면 전송 지점이 하나 늘고 결정 3-A 가 다시 열린다.**

브라우저에도 남는다. 로그인 토큰과 가입 진행 상태가 그렇다 ([auth-kakao-v1.md §5-4](../api/auth-kakao-v1.md)).

---

## 5. 로그

**코드는 규칙을 잘 지킨다.** 발화 원문·프롬프트·응답 본문·이미지를 로그에 남기지 않고 개수·모델명·토큰 수·지연만 남긴다.

| 지점 | 근거 |
|---|---|
| 모델 호출 | [llm_client.py:184-199](../../apps/api/app/agents/common/llm_client.py) — "발화 원문·프롬프트·응답 본문은 로그에 남기지 않는다" |
| 파이프라인 | [pipeline.py:403-428](../../apps/api/app/agents/pipeline.py) — "민감한 원문은 제외하고 처리 상태만" |
| 급식표 OCR | [image_ocr.py:7](../../apps/api/app/providers/meal_plan/image_ocr.py) — "로그에는 토큰 수만. 이미지·원문은 남기지 않는다" |
| 카카오 | [kakao/client.py:12-14](../../apps/api/app/integrations/kakao/client.py) — "인가 코드·토큰·회원번호를 예외 메시지에도 로그에도 싣지 않는다" |

**🚨 그런데 웹서버 기본 접속 로그가 그 규칙을 깬다.**

실행 명령에 접속 로그를 끄는 설정이 없고 ([Makefile:35](../../Makefile)), 카카오 콜백 주소는 인가 코드를 주소의 쿼리로 받는다 ([routers/auth.py:175-182](../../apps/api/app/api/v1/routers/auth.py)). 그래서 요청 줄 전체가 로그에 남으면 인가 코드가 같이 남는다. [auth-kakao-v1.md §7-5](../api/auth-kakao-v1.md) 의 "콜백 URL 전체를 로깅하지 않는다" 와 정면으로 어긋난다.

로그 설정 자체가 없어서 ([main.py](../../apps/api/app/main.py) 에 로깅 설정 없음) 보관 기간·수집처도 정의된 적이 없다. 🔸 결정 2-A.

---

## 6. 삭제

| 사실 | 근거 |
|---|---|
| 삭제 경로는 하나뿐이다. 증빙 복사 → 동의 행 삭제 → 대상 삭제를 한 트랜잭션으로 한다 | [retention.py:6-7, 33-53](../../apps/api/app/domains/consent/retention.py) |
| 아이·보호자 행을 지우면 관찰·건강·연결·세션이 FK 로 따라 지워진다 | 각 모델의 `ondelete="CASCADE"` |
| 기록을 남긴 보호자만 탈퇴하면 기록은 남고 작성자 연결만 끊긴다 | [observation/models.py:74](../../apps/api/app/domains/memory/observation/models.py) (`SET NULL`) |
| 임베딩은 같은 행의 컬럼이라 따로 지울 것이 없다 | 〃 `:61` |
| 기억 전체 삭제를 눌러도 동의 이력은 남는다 | [api-interface-v1.html](../api/api-interface-v1.html) §07 |
| `deleted_at` 칸이 있지만 쓰는 코드가 없다 — 유예기간을 아직 안 정해서 | [identity/models.py:31](../../apps/api/app/domains/identity/models.py) · [child/models.py:44](../../apps/api/app/domains/child/models.py). 🔸 결정 5 |
| 증빙 파기 배치는 있는데 **실행 스케줄이 안 붙어 있다** | [workers/consent_purge.py:6-7](../../apps/api/app/workers/consent_purge.py) |
| 사진 원본·썸네일 정리는 범위 밖으로 명시돼 있다 | [retention.py:9-11](../../apps/api/app/domains/consent/retention.py) |

---

## 7. 아직 못 만든 것

방침을 쓸 때 "있는 것처럼" 적으면 안 되는 항목들이다.

| 무엇 | 상태 |
|---|---|
| 동의를 저장하는 API (`POST /consents`) | 계약서와 프론트 목에만 있고 서버에 없다. 지금 구현된 라우터는 로그인 7개뿐 |
| 아이·기록·사진 API | 전부 미구현 |
| 법정대리인 확인 | `guardian_attested` 칸이 있는데 **프론트가 `true` 를 상수로 보낸다** ([consent/page.tsx:115](../../apps/web/src/app/auth/consent/page.tsx)). 이대로면 증빙에 정보량이 0이다. 🔸 결정 4 |
| 방침 본문 등록 | `policy_version` 에 넣는 함수는 있는데 **부르는 스크립트가 없다**. 지금 프론트가 보내는 버전 값은 DB 에 존재하지 않는다 |
| 개인정보 보호책임자 | 정해진 적 없다. 법정 필수 기재사항이다 |

---

## 8. 이 문서를 고쳐야 하는 때

- 새 컬럼에 개인정보가 들어갈 때
- 외부 API 를 부르는 코드가 늘 때 (임베딩 API 가 가장 유력하다)
- 파일 저장소가 생길 때
- 동의 항목이 늘거나 줄 때

고친 뒤에는 `policy_version` 에 새 방침 행을 넣어야 하는지 함께 판단한다. 방침과 이 문서가 어긋나면 방침 쪽이 거짓이 된다.
