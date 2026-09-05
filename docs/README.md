# 육아기억 AI 문서 인덱스

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

- [api/api-interface-v1.html](api/api-interface-v1.html) — 화면 01~10 을 그리는 최소 API 28개 계약 확정 · 공통 Ref/에러/Idempotency 규약 · 공통 타입 5종 · 승인 게이트 2곳 · 열린 결정 3건 (브라우저로 열기)

*아직 문서 없음.* 외부 연동(나이스 급식 · Calendar · OCR).

## 웹 · 화면

*아직 문서 없음.* 화면 01~10, 입력 · 진행 오버레이 · 저장 확인 · 제안 · 승인 · 기록 고치기, 디자인 토큰.

## 안전 · 개인정보

*아직 문서 없음.* 가드레일 7개, 법정대리인 동의 절차, 보관·삭제 범위, 외부 LLM 전달 범위. → [지금 열려 있는 결정](#지금-열려-있는-결정-기한-있음)

## 평가 (Eval)

*아직 문서 없음.* eval 케이스 10개, Trajectory 판정, 골든셋, 주 1회 회귀 실행.

## 운영 (Ops)

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
