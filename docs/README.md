# 아이캐치 문서 인덱스

> 육아를 가장 많이 아는 AI가 아니라, 우리 아이를 가장 오래 알아온 AI.

카카오테크 캠퍼스 4기 2단계 · 부산대 3팀. 각 문서는 기능 단위(`*-v1`)로 **결정 · 근거 · 검증**을 고정한다.
제품 전반 컨텍스트는 [overview/service-plan.md](overview/service-plan.md) 와 [overview/tech-spec.md](overview/tech-spec.md) 를 먼저 본다.

폴더는 **도메인 기준**으로 나눈다 — 기능 문서는 도메인 폴더(`memory·agents·api·web`)에, 개요·안전·평가·운영·기획·검증은 성격별 폴더에 둔다.
`docs/` 루트에는 이 `README.md` 만 둔다.

---

## 개요 · 범위

- [overview/service-plan.md](overview/service-plan.md) — 서비스 기획 최종안 (문제 정의 · 포지셔닝 · 동작 방식 · 안전 설계 · 성공 지표 · 첫 100명)
- [overview/tech-spec.md](overview/tech-spec.md) — 테크스펙 (요구사항 · 아키텍처 · 데이터 모델 · API 명세 · 리스크 · 테스트 전략)

<details>
<summary><b>서비스 기획 최종안 절 찾기</b></summary>

| § | 내용 |
| --- | --- |
| [1](overview/service-plan.md#1-누구의-어떤-문제인가) | 누구의, 어떤 문제인가 — 페르소나 · 문제 한 문장 · Blind 근거 3건 |
| [2](overview/service-plan.md#2-무엇을-바꾸는가) | 무엇을 바꾸는가 — 지금 vs 우리 서비스 · 포지셔닝 |
| [3](overview/service-plan.md#3-어떻게-동작하는가) | 어떻게 동작하는가 — 5단계 파이프라인 · 화면 흐름 · **자율성 경계** |
| [4](overview/service-plan.md#4-무엇을-만들고-무엇을-안-만드는가) | Must / **Won't** |
| [5](overview/service-plan.md#5-왜-우리여야-하는가) | "그냥 챗봇으로 하면 왜 안 되나요?" 에 대한 답 |
| [6](overview/service-plan.md#6-안전-설계--여기가-제일-조심스러운-부분) | **안전 설계** — 가드레일 7개 · AI가 틀렸을 때 화면 · 개인정보 |
| [7](overview/service-plan.md#7-잘-됐다는-걸-뭘로-아는가) | 성공 지표 · eval 케이스 10개 |
| [8](overview/service-plan.md#8-3개월-뒤에-남는-것--첫-100명) | 락인 · 데이터 해자 · 첫 100명 채널 · 10주 로드맵 |

</details>

<details>
<summary><b>테크스펙 절 찾기</b></summary>

| § | 내용 |
| --- | --- |
| [배경](overview/tech-spec.md#배경) · [목표](overview/tech-spec.md#목표) | 왜 만드는가 · 성공 정의 · 세부 지표 5개 |
| [일정](overview/tech-spec.md#일정) | 주차별 **제거할 불확실성** + 확인 방법 |
| [담당](overview/tech-spec.md#담당) | 6인 역할 · 소유 영역 · 의사결정 원칙 |
| [요구사항](overview/tech-spec.md#요구사항) | 기능 `F-01`~`F-15` · 비기능 `NF-01`~`NF-09` |
| [아키텍처 설계](overview/tech-spec.md#아키텍처-설계) | 기술 스택 (Next.js / FastAPI / PostgreSQL+pgvector) · 흐름도 |

> 데이터 모델(DBML)과 인터페이스 명세는 테크스펙에서 **분리**했다. 정본은 저장소 밖에 있고, 확정되면 `api/` 에 기능 문서로 고정한다.
| [리스크](overview/tech-spec.md#리스크) | ①~⑬ · ⭐과해석 · **④법정대리인 동의 · ⑤외부 LLM 전달 범위 · ⑥삭제 범위** |
| [요청사항](overview/tech-spec.md#요청사항) | 담당자별 미결 질문 3건 + 기한 |
| [시나리오](overview/tech-spec.md#시나리오) | 핵심 시나리오 7단계 · 실패·예외 7종 |
| [트레이드오프](overview/tech-spec.md#트레이드오프) | 무엇을 고르고 무엇을 버렸나 |
| [테스트 전략](overview/tech-spec.md#테스트-전략) | 결과 축 + **경로(Trajectory) 축** · eval 10개 · 층위별 담당 |
| [릴리즈](overview/tech-spec.md#릴리즈) · [스펙 아웃](overview/tech-spec.md#스펙-아웃) | 배포 시점 · 첫 100명 채널 · 뺀 것과 이유 |
| [추가 지표](overview/tech-spec.md#추가-지표) | 제품 / 품질(eval) / 운영 지표 계산식 |

</details>

## 자료 (Assets)

- [assets/agent-flow.png](assets/agent-flow.png) — Agent 처리 흐름도 (인식 → 승인① → 계획 → 행동 → 결과 → 승인② → 반영, 실패 시나리오 포함)
- [assets/prototype.html](assets/prototype.html) — 프로토타입 standalone 시연본 (화면 01~10, 브라우저로 직접 열기)

> ⚠️ 테크스펙 §아키텍처 설계가 참조하던 `부산대3팀_아키텍처.png` 는 Notion export 에 빠져 있다. Notion 원본에서 받아 `assets/` 에 넣고 링크를 되살릴 것.
> 발표 PDF · 시연 영상도 저장소에 없다 (Notion 원본에 있음).

## Memory

*아직 문서 없음.* Child / Observation Memory 스키마, Fact·Observation·Inference 3분류, Curator 승격·감쇠, Correction 루프.

## Agent

*아직 문서 없음.* Supervisor 라우팅(안전 사전검사 · 의도 분류), Food · Activity · Education · Health Agent, 공통 컨텍스트 주입.

## API · 백엔드

- [api/api-interface-v1.html](api/api-interface-v1.html) — ⚠️ **초기 프로토타입 시점의 글이라 갱신하지 않는다.** 문서 안에는 "이 문서가 정본" 이라고 적혀 있지만 그대로 두고, **확정된 계약은 아래 기능 문서들이 대체한다** (§04 → `auth-kakao-v1.md`, §05 초대 → `invite-v1.md`). 화면이 무엇을 부르는지 훑는 용도로는 여전히 가장 빠르다 — 화면 01~10 을 그리는 최소 API 27개 계약 확정 · 공통 Ref/에러/Idempotency 규약 · 공통 타입 5종 · 승인 게이트 2곳 · 열린 결정 2건 (브라우저로 열기)
- [api/auth-kakao-v1.md](api/auth-kakao-v1.md) — **서버 주도 인가 코드 흐름.** `redirect_uri` 를 API 오리진 하나로 고정(preview 도메인은 등록 불가) · 클라이언트는 1회용 코드를 받아 `{code, bind}` 로 교환하고 `bind` 가 그 홉을 지킨다 · **동의 전에는 `parent` 를 만들지 않는다**(`signup` 신설) · 계약서 §01 `Bearer` 유지, 무인증 5개와 302 엔드포인트 2개를 예외로 명시 · 불투명 세션 12시간, refresh 없음 · `session`·`auth_handoff` 테이블 신설안 · `parent.nickname` nullable
- [api/invite-v1.md](api/invite-v1.md) — **초대를 링크가 아니라 코드로.** 카톡 링크는 인앱 브라우저에서 열려 `sessionStorage` 가 날아가면 `bind` 까지 잃고 **로그인 자체가 깨진다** · Crockford Base32 8자(`I`·`L`·`O`·`U` 제외) · 정규화한 값을 해시로 저장 · 24시간 · 1회용 · 🚨 **시도 제한이 이 선택의 전제**(40비트 · 확인과 수락이 같은 통) · 관계는 **받는 쪽이 수락할 때** 고른다 · 🔶 수락 전 확인 `GET /invites/{code}` 와 에러 5종은 협의 대상 · ⚠️ 계약서 §05 를 **대체한다**
- [api/idempotency-v1.md](api/idempotency-v1.md) — **되돌릴 수 없는 POST 5개의 중복 실행 방지.** 계약서 §01 의 "헤더가 없으면 400" 을 동작까지 채운다 — 같은 키·같은 요청은 **처음 응답 재생**, 다른 요청은 422, 처리 중은 409 · **2xx 만 저장**(403 을 캐시하면 동의 후 재시도가 막힌다) · 키 스코프 `(parent_id, method, path, key)` · 에러 코드 4개 신설 제안 · 클라이언트는 전용 함수의 **필수 인자**로 강제하고 목은 같은 동작을 회귀 테스트로 건다 (#29 리뷰 반영)

*아직 문서 없음.* 외부 연동(나이스 급식 · Calendar).

## 일정 (event)

- [event/event-draft-flow-v1.md](event/event-draft-flow-v1.md) — 승인 전 `event` 행을 두지 않기로 하면서 생긴 초안 계약 · `op`(create/update) 두 모양 · `items` 는 최종 목록(빠진 `item_id` 는 삭제) · `before` 는 원본 전체이고 `changed` 는 뺌 · `draft_id` 는 화면 전용 키 · `is_prepared` 는 초안이 읽지 않음

## 급식표 (meal-plan)

- [meal-plan/meal-plan-pipeline-v1.md](meal-plan/meal-plan-pipeline-v1.md) — 입력 3종(사진 · 엑셀 · 한글)이 모이는 `MealPlanJSON` 과 `MealPlanReader` 확정 · `source` 필드로 검수 필요 여부 구분 · `meal_type` 5종 · `allergen_codes` 는 JSON 에 없음(규칙이 뽑음) · 저장 기준은 미정(우선 아이 기준) · 못 읽은 칸은 그 칸만 비움
- [meal-plan/ocr-model-eval-v1.md](meal-plan/ocr-model-eval-v1.md) — 사진 입력 기본 모델 `gemini-3.1-pro-preview` 확정(정답셋 178항목, 메뉴 단위 번호 일치 100%) · 채점 지표는 메뉴 단위 번호 일치가 정본 · 게이트웨이 출력 상한 6,000 → 2분할 · 후보 방식 A/A+/A′/B

## 웹 · 화면

- [web/design-system-v1.md](web/design-system-v1.md) — 색 31 · 타이포 8단계 · 레이아웃 · 컴포넌트 사양 확정 · 승인 게이트는 `caution`, 실패는 뉴트럴 · 그림자 1단계 · 전 구간 1열 고정
- [web/kakao-login-v1.md](web/kakao-login-v1.md) — 로그인은 API 호출이 아니라 페이지 이동 · 셸은 인앱 인증 세션만 열고 토큰을 안 만짐 · 복귀는 `/auth/callback` 웹앱 공통 · 에러 문구는 프론트가 만듦 · 세션은 `sessionStorage` 12시간
- [web/mock-screens-v1.md](web/mock-screens-v1.md) — 목 서버 화면 확인 조회표 · 시나리오별 주소 · 저장소 둘(sessionStorage/localStorage) 초기화 스니펫
- [web/event-draft-ui-v1.md](web/event-draft-ui-v1.md) — 초안이 생기는 시점 3곳(한 줄 입력 · 제안 · 사진)과 경로별 흐름 · 그릇은 셋 카드는 한 벌 · 초안 만들기는 게이트가 아니고 제출만 게이트 ㉠ · 건별 제출 · 못 읽은 일자를 화면이 안 채움 · 초안은 세션 스토리지에만

*아직 문서 없음.* 화면 01~10, 입력 · 진행 오버레이 · 저장 확인 · 제안 · 승인 · 기록 고치기.

## 안전 · 개인정보

*아직 문서 없음.* 가드레일 7개, 법정대리인 동의 절차, 보관·삭제 범위, 외부 LLM 전달 범위. → [지금 열려 있는 결정](#지금-열려-있는-결정-기한-있음)

## 평가 (Eval)

*아직 문서 없음.* eval 케이스 10개, Trajectory 판정, 골든셋, 주 1회 회귀 실행.

## 운영 (Ops)

- [ops/alembic-collaboration-v1.md](ops/alembic-collaboration-v1.md) — 마이그레이션 협업 규칙 · make 명령어 치트시트 · autogenerate 한계 · revision 충돌 해결법 확정

*아직 문서 없음.* 배포, 보안 5종 체크, 모델 호출·토큰 비용 실측.

## 기획 · 검증

*아직 문서 없음.* 주차 계획 · 회고(`plans/`), QA 기록 · 스크린샷(`verification/`).

---

## 지금 열려 있는 결정 (기한 있음)

두 개요 문서에서 `미정` 으로 남아 있는 항목. **원문이 기준**이고, 결정되면 해당 도메인 폴더에 기능 문서로 고정한 뒤 이 표에서 뺀다.

> 승격 임계값 · 감쇠 기준 · 법정대리인 동의 시점 · 급식 데이터 출처는 **확정되어 이 표에서 뺐다.** 확정된 값은 아직 문서로 고정되지 않았다 — 각각 `memory/` · `safety/` · `api/` 에 기능 문서로 남길 것.

| 결정할 것 | 기한 | 원문 | 결정 후 갈 곳 |
| --- | --- | --- | --- |
| 개인정보 보관 범위·기간 | **9월 2주** | [NF-04](overview/tech-spec.md#5-2-비기능적-요구사항) | `safety/` |
| 삭제 범위 (Memory·원문·Embedding·로그) · 공지 원문 제3자 정보 | **9월 2주** | [리스크 ⑥](overview/tech-spec.md#리스크) | `safety/` |
| 외부 LLM 전달 범위 · 식별정보 제거 · 국외 처리 여부 | **9월 2~4주** (스키마 확정과 동시) | [리스크 ⑤](overview/tech-spec.md#리스크) | `safety/` |
| 냅킨 계산 — 유저 1인당 월 모델 비용 | **9월 1주** | [요청사항 3](overview/tech-spec.md#요청사항) | `ops/` |
| Agent Workflow · API Schema 확정 | **9월 2~4주** | [일정](overview/tech-spec.md#일정) | `agents/` · `api/` |
| Agent 참조 문서(건강검진 정보, 0~5세 선호 활동 자료) 출처 | **9월 2주** | [요청사항 1](overview/tech-spec.md#요청사항) | `agents/` |
| 블라인드 비교 선택률 · 재방문율 목표치 | 미정 | [기획 §7](overview/service-plan.md#7-잘-됐다는-걸-뭘로-아는가) | `eval/` |

---

## 문서 작성 규칙

새 기능 문서(작업 로그성 `*-v1.md`)는 다음 규칙을 따른다.

### 1. 파일 위치 · 이름

도메인 폴더 안에 둔다. 어디에 속하는지 애매하면 **누가 그 코드를 소유하는지**로 판단한다.

| 폴더 | 무엇이 들어가나 | 소유 |
| --- | --- | --- |
| `overview/` | 기획·테크스펙·범위 등 제품 전반 문서 | 박재형 (PM) |
| `memory/` | Child·Observation Memory 스키마, 3분류, Curator 승격·감쇠, Correction | 이시하 (AI) |
| `agents/` | Supervisor 라우팅, Food · Activity · Education · Health Agent, 프롬프트·컨텍스트 | 이시하 (AI) |
| `api/` | 엔드포인트 계약, SSE, 승인 게이트, 외부 API 연동 | 김명성 (백엔드) |
| `event/` | 일정 초안 계약, 제출 API 가 지켜야 할 규칙 | 이시하 (AI) |
| `meal-plan/` | 급식표 입력(사진 · 엑셀 · 한글) → 구조화 JSON · 알레르기 번호 · 저장 | 박재형 (PM) |
| `web/` | 화면 01~10, 컴포넌트, 디자인 토큰 | 고태영 (프론트) |
| `safety/` | 가드레일, 동의 절차, 개인정보 보관·삭제 범위 | 박재형 (PM) |
| `eval/` | 테스트 케이스, Trajectory 판정, 골든셋, 회귀 결과 | 오현식 · 이도헌 |
| `ops/` | 배포, 보안 체크, 비용·호출 수 실측 | 김명성 (백엔드) |
| `plans/` | 주차 계획 · 회고 (`YYYY-MM-DD-제목.md`) | 박재형 (PM) |
| `verification/` | QA 기록 · 스크린샷 (`제목-YYYY-MM-DD.md`) | 전원 |
| `assets/` | 다이어그램 · 프로토타입 등 **문서가 아닌 파일** | — |

- 이름은 `기능-단위-v1.md` (kebab-case, **기능 1개 = 문서 1개**). 개편·후속은 `...-enhancements-v1` · `...-v2` 로.
- 새 도메인이 생기면 폴더를 추가하고 **이 인덱스에 항목을 넣는다.** 인덱스에 없는 폴더는 없는 것으로 친다.
- 문서를 추가하면 위 해당 섹션의 `*아직 문서 없음.*` 줄을 지우고 목록으로 바꾼다.

### 2. 인덱스 등재 형식

한 문서 = 한 줄. 설명은 **무엇을 고정했는지**를 쓴다 (기능 이름 반복 금지).

```markdown
- [memory/observation-schema-v1.md](memory/observation-schema-v1.md) — observation 4계층 테이블 확정 · 공통 컬럼 · soft ref 는 jsonb 로
```

### 3. 상단 헤더 블록 (제목 바로 아래, 순서 고정)

```markdown
# <제목> v1

문서 목적: <이 문서가 무엇을 고정하는지 한 줄>

기준 브랜치: `feat/...`
작성일: YYYY-MM-DD
담당: <이름>
선행 문서: [`docs/<폴더>/<이름>.md`](상대경로) (관계 설명)   ← 있으면
```

- `문서 목적:` · `기준 브랜치:` · `작성일:` · `담당:` 은 필수. 나머지는 해당될 때만.
- `overview·plans·verification·assets` 는 살아있는 참조/기록 문서라 이 헤더 규칙에서 예외.

### 4. 링크 규칙

- **문서 간**: 같은 폴더는 `./이름.md`, 다른 폴더는 `../<폴더>/이름.md`. 표시 텍스트는 루트 기준 전체 경로(`docs/<폴더>/이름.md`)로 적어 폴더가 바뀌어도 의미가 남게 한다.
- **코드 참조**: 문서에서 루트까지 `../../` 후 실제 경로. 파일이 사라지면 링크를 풀고 "대체·제거됨" 을 명시한다 — **끊긴 링크로 남기지 않는다.**
- **개요 문서 참조**: 절 제목으로 가리킨다 (예: [테크스펙 §리스크](overview/tech-spec.md#리스크)).
- `overview/` 의 두 문서는 **Notion export 원문**이다. 내용 수정은 Notion 에서 하고 다시 export 해 덮어쓴다. 저장소에서 직접 고치면 다음 export 때 날아간다.

### 5. 주의

🚨 이 저장소는 **public** 이다. 문서 본문에 API 키·토큰·실제 사용자 발화를 붙여넣지 마라.
1단계에서 학생 토큰 8건이 유출됐고 그중 일부가 `docs/*.md` 에서 나왔다 ([.gitignore](../.gitignore) 상단 참고).
