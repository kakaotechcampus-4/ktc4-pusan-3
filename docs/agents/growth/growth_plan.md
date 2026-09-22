# Growth Agent 구현 계획 (`growth_plan.md`)

> 테이블 생성부터 라이브 테스트까지.
> 통합 대상: [`Growth_Agent_명세.md`](Growth_Agent_명세.md) · [`Growth_Tool_명세.md`](Growth_Tool_명세.md) · [`growth_agent_own_table.md`](growth_agent_own_table.md) · [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §4 · [`Agent_공통규약.md`](../shared/Agent_공통규약.md) · [`외부연결_계획.md`](../shared/외부연결_계획.md) §4 · [`RAG_plan.md`](../shared/RAG_plan.md) §4

---

## 0. 이 Agent의 특이점

**구현 난이도보다 콘텐츠 난이도가 높습니다.** 코드 tool은 `compute_growth_delta`(뺄셈) · `check_routine_category`(표 조회) · `pick_next_step`(사슬 한 칸) · `search_growth_doc`(필터+의미 검색)이고, 나머지는 전부 조회·출력입니다. 대신 `growth_doc` 148행을 사람이 써야 하고, 여기서 품질이 결정됩니다.

**그리고 "안 하는 것"이 절반입니다.** 평가 표현 필터가 코드·lint·테스트 세 겹으로 들어갑니다.

**핵심 기능은 `observation_routine`·`observation_education`을 `growth_doc`·도서와 연결하는 추천입니다**(2026-09-22). 그래서 S3(문서 행)이 임계 경로이고, `notice`는 보조입니다.

---

## 1. 전체 단계

| 단계 | 내용 | 선행 | 산출물 |
| --- | --- | --- | --- |
| S0 | 하드 선행 닫기 | – | 결정 5건 |
| S1 | 공통 뼈대 | S0 | Food와 공유 (이미 있으면 건너뜀) |
| S2 | 스키마 · 권한 | S0 | `growth_doc` · `book_catalog` · `book_query_cache` |
| S3 | 콘텐츠 제작 **(임계 경로)** | S2 | 문서 행 148 + 도서 캐시 |
| S4 | 포트 · 컨텍스트 · 게이팅 | S1 S2 | mock `run()` |
| S5 | 코드 tool | S3 S4 | 차분 · 카테고리 · 안전 · 근거 |
| S6 | 모델 tool · 프롬프트 | S5 | 9개 tool |
| S7 | 출력 채널 | S6 | suggestion · readout · 역질의 |
| S8 | 파이프라인 통합 | S7 | 라벨 4개 라우팅 |
| S9 | 테스트 | S8 | 평가 표현 회귀 포함 |
| S10 | 라이브 테스트 | S9 | 내부 검증 · 롤아웃 |

---

## 2. S0 — 하드 선행

| # | 결정 | 없으면 | 담당 |
| --- | --- | --- | --- |
| 1 | ~~`child_growth_log` 단위·NOT NULL~~ | ✅ 닫힘(09-22) — `numeric(4,1)` cm/kg · `check_date date NOT NULL` | – |
| 2 | `child_health` 동의 게이팅 포트 | 키·몸무게 읽기가 법적으로 막힘 | BE |
| 3 | `suggestion.kind` + `suggestion_evidence` 테이블 | 개인화/일반 표시 불가 · 근거를 셀 수 없음 | BE |
| 4 | `hazard_term` 적재 (Activity 소유) | 활동 행 안전 검사 불가 | Activity |
| 5 | 연령 경계 확정 — 예절 24 · 습관 36 (G-7). **성장폭에는 연령 경계를 두지 않는다** | 설정값으로 시작 가능 | PM |

`notice` 테이블은 **하드 선행이 아닙니다.** 없으면 `lookup_notice`만 닫고 나머지는 동작합니다.

---

## 3. S2 — 스키마 · 권한

```
1) growth_doc            (self FK: next_step_of → 같은 테이블)
2) book_catalog → book_query_cache
3) child_growth_log 단위·NOT NULL 변경       (S0-1)
```

DDL은 [`growth_agent_own_table.md`](growth_agent_own_table.md) §5.

**DB가 막는 것 두 개**
- `CHECK (row_type <> 'habit_strategy' OR min_month >= 36)` — 게이팅이 뚫려도 36개월 미만 습관 교정 행이 존재하지 않음
- `CHECK (status <> 'approved' OR reviewed_by IS NOT NULL)` — 미검수 행이 조회되지 않음

권한: agent role은 전부 SELECT + `suggestion` INSERT(writer 경유)뿐. `growth_doc`·`book_catalog` write는 배치·마이그레이션만.

DoD: upgrade/downgrade 왕복 · agent role로 `growth_doc` INSERT 시도 → 거부.

---

## 4. S3 — 콘텐츠 제작 (임계 경로)

### 4-1. 문서 행

| 순위 | `row_type` | 행 수 | 출처 |
| --- | --- | --- | --- |
| P0 | `routine_step` | 40 | 표준보육과정·누리과정 고시(`public_law`) + 국가건강정보포털 |
| P0 | `learning_activity` | 60 (12–35 · 36+) | 고시 + 해설서(`fact_rewrite`) |
| P1 | `habit_strategy` | 20 (`trigger_tags`별) | 루틴 원문 (GT-7) |
| P1 | `manner_practice` · `rhythm_info` | 20 | 동일 |
| P2 | `book_guide` · `measure_guide` | 8 + 4 | `measure_guide`는 Health에서 넘어온 행(성장 판정 제거로 소비처가 Growth로) |

절차: 질문 목록 → 행 작성(draft) → **lint** → 교차 검수(작성자 ≠ 검수자) → approved → 임베딩. [`RAG_plan.md`](../shared/RAG_plan.md) §2

**Growth 전용 lint 두 개**
1. **평가 표현** — 또래 · 평균 · 정상 · 발달이 · 늦 · 느리 · 뛰어나 · 재능 · 소질 · 문제 행동
2. **`hazard_term` 스캔** — `materials`·`body`가 행의 `min_month`와 충돌하면 적재 거부

`next_step_of` 사슬은 양치·배변·옷 입기 3종부터(GT-4).

### 4-2. 도서

| # | 작업 | DoD |
| --- | --- | --- |
| 3-1 | 정보나루 인증키 + **서버 IP 등록** | 한도 500 → 30,000/일 |
| 3-2 | 어댑터 + 계약 테스트 | 실제 응답 샘플 → dataclass |
| 3-3 | 연령 산출식 (`age_min/max_month`) | API 연령 코드·청구기호·서명 키워드 조합 (GT-3) |
| 3-4 | `form` 분류 (보드북/그림책/지식책) | 불명이면 `unknown` → **영아기 제외** |
| 3-5 | 사서추천도서 월 1회 배치 | `pick_weight` 반영 |
| 3-6 | 주제어(`subjects`) 색인 | 관심사 매칭 |

---

## 5. S4 — 포트 · 컨텍스트 · 게이팅

```
app/agents/growth/
├── agent.py · context.py · prompt.py · registry.py · result.py
├── schemas/ · store/ports.py · tools/
```

**포트**: `observation_education` · `observation_routine` · **`observation_activity`(주입, import 아님)** · `profile_affinity(domains: tuple[str,...])` · `child_growth_log` · `notice` · `growth_doc` · `book_catalog` · `hazard_term` · `SuggestionWriter`

**게이팅** ([`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §4)

| tool | `min_month` |
| --- | --- |
| routine·activity·affinity 검색 · `propose_routine_plan` · `search_books` · `propose_books` · `compute_growth_delta` | 0 |
| education 검색 · `lookup_notice` · `propose_learning_activity` | **12** |

라벨 안에서 코드가 정하는 것: 루틴 모드(0–11 `rhythm_info` / 12+ `next_step` / 36+ `habit_fix`) · 허용 카테고리(24+ 예절, 36+ 습관) · KB 인덱스(12–35 / 36+) · 도서 연령 필터. **성장폭에는 연령 축이 없다** — `compute_growth_delta`는 측정 로그 전부를 읽는다.

DoD: mock `run()` · Activity 패키지 import 0건(import-linter) · `test_growth_registry.py`.

---

## 6. S5 — 코드 tool

| 순서 | tool | DoD |
| --- | --- | --- |
| 5-1 | `compute_growth_delta` | 측정일 **전부** 표기 · 측정 1건 이하 → readout · **모델 호출 0** · 판정어 0건 · 반올림 0건 |
| 5-2 | `check_routine_category` | 연령 허용표 대조 · 불허 → readout 교체(모델 1회는 유지) |
| 5-3 | `rank_evidence` 연결 | domains 복수 · `profile_affinity`는 읽기만 (감쇠는 Curator) |
| 5-4 | `search_growth_doc` | 월령·`row_type`·카테고리 필터 → 의미 검색 top-3 · 쿼리에 보호자 발화 없음 · **교육과정도 루틴 자료도 같은 테이블** |
| 5-4a | `pick_next_step` | 관찰 `assistance_level` → 사슬의 바로 다음 행 · 건너뛰기 불가 · 사슬 끝이면 일반 템플릿 |
| 5-5 | 평가 표현 필터 | 출력 사후 · 걸리면 후보 **삭제**(수정 아님) |
| 5-6 | `hazard` 사후 스캔 | 활동 후보의 `materials`·문장 |

---

## 7. S6 — 모델 tool · 프롬프트

조회 6 (`search_education_memory` · `search_routine_memory` · `search_activity_memory` · `search_affinity` · `lookup_notice` · `search_books`) + 출력 3 (`propose_learning_activity` · `propose_routine_plan` · `propose_books`).

**출력 검증**
- 근거 id가 `rank_evidence` 상위 N 안
- `propose_books`: **직전 `search_books` 결과의 ISBN만**
- `propose_routine_plan`: `habit` + `trigger` NULL → 역질의 · **근거 0 → 역질의(카테고리 무관)**. `routine_coaching`은 일반 추천을 내지 않는다
- 평가 표현 필터 · hazard 스캔
- 근거가 `observation_activity`면 ref에 표시 → 화면 문구 분기

`growth_review`는 이 경로를 **타지 않습니다.** AI 클라이언트를 아예 호출하지 않습니다.

---

## 8. S7 — 출력 채널

| 채널 | 내용 |
| --- | --- |
| `suggestions` | 교육 활동 · 루틴 · 도서 (draft +24h) · **3개** · 필터 후 3개 미만이면 재호출 1회 · 저장 즉시 추천 카드 화면 |
| `readouts` | `growth_delta`(code) · 닫힘 안내(code) |
| `needs_observation` | `trigger` 질문 또는 습관 현재 여부 — 한 번에 하나 |
| `event_requests` | 없음 |

DoD: 성장폭 readout에 **측정일 2개**가 항상 있음 · 닫힘 문구가 상수와 글자 단위로 같음.

---

## 9. S8 — 파이프라인 통합

- `IMPLEMENTED_AGENTS`에 `growth` · **라벨 4개** 라우팅
- Supervisor 경계 예시 투입: activity↔growth 4줄 · 신체 성장 3줄("잘 크고 있어?"까지 전부 `growth_review`)
- `growth_review`·닫힘 경로는 `model_calls=0`으로 집계

DoD: "오늘 블록 쌓는 거 배웠대" → **Memory만**(Growth 미호출) · "잘 크고 있어?" → Health.

---

## 10. S9 — 테스트

| 파일 | 핵심 |
| --- | --- |
| `test_growth_registry.py` | 게이팅 표 · 코드 tool 비노출 |
| `test_growth_gating.py` | 11/12 · 23/24 · 35/36개월 양쪽 · 동의 철회 |
| `test_growth_delta.py` | 측정 1건 → 안내 · **간격이 짧아도 수치가 그대로 나옴** · **AI 0회** · 측정일 전부 표기 · 반올림 0건 |
| `test_growth_routine.py` | `trigger` NULL → 역질의 · 30개월 습관 → 닫힘 readout · **20개월 자립 + 근거 0 → 역질의**(일반 제안 아님) · **8개월 → `rhythm_info` readout 하나, `suggestion` 0건** |
| `test_growth_output.py` | ISBN 검증(직전 검색 결과 + 만료 전 suggestion 제외) · 근거 id · hazard |
| `test_growth_doc.py` | **신규** — `search_growth_doc` 쿼리에 보호자 발화 0건 · 월령 밖 행 미노출 · 결과가 `memory_kind='growth_doc'`으로만 담김(품질 지표에서 제외) |
| `test_growth_next_step.py` | **신규** — `pick_next_step`이 사슬의 바로 다음 칸 · 건너뛰기 0건 · 사슬 끝이면 일반 템플릿 |
| `test_growth_wording.py` | **평가 표현 회귀** — 금지어 사전 전체를 출력 10종에 대해 |
| `test_growth_evidence.py` | affinity 0행(Curator 없음) → 티어 3 또는 general |
| 계약 테스트 | 도서 API 응답 샘플 |
| 골든 | 라벨 4개 × 단계 3개 = 12 스냅샷 |

---

## 11. S10 — 라이브 테스트

### 11-1. 드라이런
`suggestion` INSERT를 끄고 실제 데이터로 20건. 확인: 문서 행 조회 적중률 · 도서 연령 필터 정확도 · 평가 표현 0건 · p95.

### 11-2. 내부 도그푸딩 (1~2주)

| 지표 | 목표 |
| --- | --- |
| **평가 표현 노출** | **0건** — 하나라도 나오면 롤백 |
| 연령 부적합 도서 | 0건 (영아에게 글밥 많은 책 등) |
| hazard 미탐 | 0건 |
| 문서 행 적중(일반 템플릿으로 안 내려간 비율) | ≥ 0.7 |
| 성장폭 readout 측정일 누락 | 0건 |
| 판정 요청에 판정 표현 노출 | 0건 ("잘 크고 있어?" → 추이 + 검진 안내) |
| p95 응답 | < 10초 |

### 11-3. 롤아웃 게이트

| 게이트 | 조건 |
| --- | --- |
| G1 | 평가 표현 0 · 골든 12종 통과 |
| G2 | 영아(0–11개월) 계정에서 교육 활동 → Activity 안내로 정상 전환 |
| G3 | 습관 교정 요청이 36개월 경계 양쪽에서 올바르게 갈림 |
| G4 | Curator 미구현 상태(affinity 0행)에서 일반 추천 정상 |

### 11-4. 롤백
`IMPLEMENTED_AGENTS`에서 제거. 라벨 4개가 Supervisor에서 빠지면 해당 발화는 Activity 또는 Memory로 흡수됩니다.

### 11-5. 관측
`{run_id, child_id, stage, task_type, model_calls, evidence_mode, doc_hit, closed_reason, latency_ms}` — 관심사·책 제목 원문은 남기지 않습니다.

---

## 12. 리스크

| 리스크 | 신호 | 대응 |
| --- | --- | --- |
| 문서 행 제작이 지연된다 | S3이 2주 넘김 | `routine_step` 40행만으로 `routine_coaching` 먼저 릴리스 |
| 해설서 라이선스가 막힌다 | 법무 회신 | 고시 본문만으로 행 재작성(`public_law`) |
| 도서 연령 필터가 부정확 | 부적합 추천 | `form='unknown'` 제외 폭 넓히기 · 수동 보정 |
| 평가 표현이 새어나간다 | lint 우회 표현 | 금지어 사전 확장 + 골든 스냅샷 |
| `notice` 부재로 기관 연동 공백 | – | `lookup_notice` 닫고 진행(설계됨) |
| 몰입도 루프 불가 | 추천 → 관찰 역추적 없음(GT-6 폐기) | 중기로 미룸. `engagement_level`은 관찰 단위로만 본다 |

---

## 13. 열린 항목

G-3 · G-7 ([`Growth_Agent_명세.md`](Growth_Agent_명세.md) §10) · GT-1~5 · GT-7 ([`growth_agent_own_table.md`](growth_agent_own_table.md) §7) · `growth_doc` 원문 확보 · `notice`
