# Food Agent 구현 계획 (`food_plan.md`)

> 테이블 생성부터 라이브 테스트까지. 이 문서 하나로 Food를 끝까지 끌고 간다.
> 통합 대상: [`Food_Agent_명세.md`](Food_Agent_명세.md) · [`Food_Tool_명세.md`](Food_Tool_명세.md) · [`food_agent_own_table.md`](food_agent_own_table.md) · [`영양소_계산_설계.md`](영양소_계산_설계.md) · [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §3 · [`Agent_공통규약.md`](../shared/Agent_공통규약.md) · [`외부연결_계획.md`](../shared/외부연결_계획.md) §2 · [`RAG_plan.md`](../shared/RAG_plan.md) §3

---

## 0. 전체 단계

| 단계 | 내용 | 선행 | 산출물 |
| --- | --- | --- | --- |
| S0 | 하드 선행 닫기 | – | 결정 6건 |
| S1 | 공통 뼈대 | S0 | `common/` · `app/rules/` |
| S2 | 스키마 · 권한 | S0 | 마이그레이션 7개 테이블 |
| S3 | 데이터 적재 | S2 | 시드 · 동기화 배치 · 문서 행 |
| S4 | 포트 · 컨텍스트 · 게이팅 | S1 S2 | mock `run()` |
| S5 | 코드 tool | S3 S4 | 안전·해석·계산 |
| S6 | 모델 tool · 프롬프트 | S5 | 9개 tool |
| S7 | 출력 채널 | S6 | suggestion · readout · 역질의 |
| S8 | 파이프라인 통합 | S7 | 라우팅 · 동시 실행 |
| S9 | 테스트 | S8 | 단위 · 계약 · 골든 |
| S10 | 라이브 테스트 | S9 | 내부 검증 · 롤아웃 |

**임계 경로는 S3입니다.** 코드는 며칠이면 되지만 이유식 문서 행 45개와 섭취기준 시드는 사람이 원문을 읽어야 합니다. S1·S2와 **병렬로 시작**하세요.

---

## S0. 하드 선행 — 이게 없으면 시작하지 않는다

| # | 결정 | 없으면 | 담당 |
| --- | --- | --- | --- |
| 1 | `child.allergy_status` (`none`/`has`/`unknown`) | **알레르기 없는 아이가 추천을 영영 못 받음** | PM · BE |
| 2 | 온보딩에 `child_health` 동의 단계 | 알레르기 수집이 법적으로 성립 안 함 | PM |
| 3 | `suggestion`에 `kind` · `task_type` · `content jsonb` · `reference_refs` | "또래 기준" 표시 불가 | BE |
| 4 | 급식 경로 = **기관 공지 OCR 우선** + 대체·제외 메뉴 확인 흐름(`intake_daily` 급식 행) | 유아기 tool 절반이 죽음 · 급식을 먹은 것으로 잘못 셈 | PM · OCR |
| 5 | 이유기 시작 월령(4개월) · 질식 주의 해제(48개월) 근거 | 게이팅 숫자가 근거 없음 | 의학 자료 담당 |
| 6 | 기록 행 수 임계(7일 10행) · 분석 윈도우(7일) | 설정값으로 시작 가능 — **문서에만 기록** | AI |

DoD: 1~4가 이슈로 닫히고 스키마 PR이 머지됨.

---

## S1. 공통 뼈대

| 파일 | 내용 |
| --- | --- |
| `common/tool_runtime.py` | `ToolResult` · `ErrorCode` · `execute_tool(allowed=)` · `specs_for()` |
| `common/schemas/task.py` | `DomainTask` |
| `common/suggestion.py` | `SuggestionDraft.build()` — personalized인데 ref 0행이면 거절 |
| `common/readout.py` | `Readout(authored_by)` |
| `common/evidence.py` | `rank_evidence()` — **티어 1·2는 polarity ∈ {+1, −1}**. 기피도 근거다 |
| `app/rules/age.py` | `life_stage()` · 민법 달력 계산 · `date` 타입 강제 |
| `app/rules/term_match.py` | 공백 제거 부분 일치 + guards + 최엄격 우선 (Activity와 공유) |

DoD: `test_evidence_ranking.py` 통과 · `//30` 나눗셈 0건 · import-linter로 `<domain> → common` 방향 고정.

---

## S2. 스키마 · 권한

마이그레이션 순서(FK 의존):

```
1) allergen_term
2) menu_catalog → menu_alias
3) nutrient_reference
4) food_doc
6) intake_daily
6a) daycare_meal
7) child.allergy_status / gestational_weeks   (S0-1)
8) suggestion 컬럼 추가                        (S0-3)
```

DDL은 [`food_agent_own_table.md`](food_agent_own_table.md) §7.

**권한**

| role | 부여 |
| --- | --- |
| agent | `intake_daily` INSERT/UPDATE · **`daycare_meal` UPDATE/DELETE** · 나머지 SELECT |
| batch | `menu_catalog` · `menu_alias` · `nutrient_reference` · `food_doc` UPSERT · **`daycare_meal` INSERT**(OCR 최초 적재) |
| agent | `health_safety` write **비부여** |

`daycare_meal`에서 **최초 적재는 배치, 이후 갱신·삭제는 agent**로 갈라 준다. Food가 없는 급식을 지어내지 못하게 INSERT를 주지 않는다 — 고칠 대상은 OCR이 넣어 둔 행뿐이다.

DoD: `alembic upgrade head` → `downgrade -1` 왕복 성공 · 권한 테스트 2개(agent role로 `health_safety` INSERT → 거부 · agent role로 `daycare_meal` INSERT → 거부).

---

## S3. 데이터 적재 (임계 경로)

| # | 작업 | 분량 | DoD |
| --- | --- | --- | --- |
| 3-1 | `allergen_term` 시드 (19종 + aliases + guards) | 19행 | "계란/달걀/난류" → 코드 1 · "밀크티"가 밀로 안 걸림 |
| 3-2 | `nutrient_reference` 시드 | 4개 연령군 × 항목 × ref_type | **2인 대조 완료** · AMDR 단위 `%kcal` |
| 3-3 | 식약처 영양성분 API 어댑터 + 계약 테스트 | – | 실제 응답 샘플 → 내부 dataclass 변환 |
| 3-4 | 레시피 DB 어댑터 + 재료 파서 | – | 파서 정확도 기준 합의(FT-7) |
| 3-5 | `menu_catalog` 초기 동기화 배치 | 수천 행 | `resolved=true` 비율 리포트 |
| 3-6 | `stage_min` 수동 태깅 | 상위 300행 | `LifeStage.stage` 네 값 · 나머지는 `toddler` 기본 |
| 3-7 | `menu_alias` 초기 사전 | 급식 표기 위주 | 급식 30개 샘플 해석률 측정 |
| 3-8 | `food_doc` P0 — `weaning_stage`·`weaning_ingredient` | **45행** | lint 통과 · 교차 검수 · 출처 4필드 |
| 3-9 | `food_doc` P1 — `meal_pattern` 30 · `nutrient_note` 20 | 50행 | 동일 |
| 3-10 | OCR → `daycare_meal` 연결 | – | 식단표 이미지 1장 → 행 생성 |

**문서 행 작성 순서**: 질문 목록 → 행 작성(draft) → lint → 교차 검수 → approved → 임베딩. 절차는 [`RAG_plan.md`](../shared/RAG_plan.md) §2.

---

## S4. 포트 · 컨텍스트 · 게이팅

```
app/agents/food/
├── agent.py        run(task, context, *, client=None) -> DomainAgentResult
├── context.py      FoodContext(child_id, now, tz, gate, 포트 8종)
├── registry.py     TOOL_HANDLERS · CODE_TOOLS · tools_for(task_type, gate)
├── prompt.py       구획 고정 → 동적(예시·제외) 뒤
├── schemas/        common · task · records · menu · nutrition · recommend · infant · tool_defs
├── store/ports.py  Protocol 8종
└── tools/
```

**포트**: `observation_food` · `profile_affinity(food)` · `health_safety` · `child` · `menu_catalog` · `intake_daily` · **`daycare_meal`(읽기+쓰기)** · `food_doc` · `SuggestionWriter`

**게이팅** ([`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §3)

| 라벨 \ 단계 | infant_milk 0–3 | infant_weaning 4–11 | toddler · preschool 12+ |
| --- | --- | --- | --- |
| `meal_recommendation` | `()` | 3 | 5 |
| `nutrient_analysis` | `()` | `()` | 7 |

추가 닫힘: 동의 없음 → 전부 · `safety_ok=False` → 식단 추천 · `allergy_status='has'` + 등록 0건 → 식단 추천.

DoD: mock `run()`이 포트·클라이언트를 **건드리지 않고** `DomainAgentResult` 반환 · `test_food_registry.py` 통과.

---

## S5. 코드 tool

| 순서 | tool | DoD |
| --- | --- | --- |
| 5-1 | `resolve_menu` | 캐시 미스 → API 1회 → upsert · 실패는 `unresolved`(추정 0건) |
| 5-2 | `filter_food_safety` | 19종 교집합 · `term_match` · **연령 규칙(꿀·질식)** · 조회 실패 → `SAFETY_UNAVAILABLE` |
| 5-2a | `resolve_meal_date` | 사전 표 그대로 · 기준일은 `Gate.today` · 과거 30일 밖 거절 · 미해결은 되묻기 |
| 5-3 | `compute_intake_daily` | 결측 행 생성 0건 · `catalog_version` 기록 |
| 5-4 | `select_kdri_group` · `evaluate_nutrient_bands` | EAR 기준 · 히스테리시스 · 행 수 미달 → `insufficient` |
| 5-5 | `build_candidate_pool` | 알레르기·연령 금지·반복·단계 필터 순서 고정 · **기피로는 아무것도 빼지 않음** |
| 5-6 | `sample_candidates` | `seed=run_id` 재현 · 다양성 제약 · **필터 뒤에 위치** · 가중치 **부족 식품군 > 선호 > 기피** |
| 5-7 | `search_food_doc` | 단계 필터 → 의미 검색 top-3 · 쿼리에 보호자 발화 없음 |
| 5-8 | `rank_evidence` 연결 | `profile_affinity`는 Curator 산출물 — 읽기만. 감쇠를 Agent가 다시 세지 않는다 |

**이 단계까지 오면 모델 없이도 결과가 나옵니다.** 수유기·영아기 미지원 안내와 안전 차단이 전부 코드 경로입니다.

---

## S6. 모델 tool · 프롬프트

| 그룹 | tool |
| --- | --- |
| 조회 | `search_food_memory` · `analyze_meal_records` · `check_repeated_menus` · `lookup_daycare_menu` · `lookup_nutrition` · `compare_diet_balance` · `guide_weaning_stage` |
| 출력 | `propose_meal_candidates` · `report_nutrient_analysis` |

**출력 검증 (전부 코드)**
- `menu_key`가 샘플링된 목록 안인가 → 아니면 후보 삭제
- 근거 id가 `rank_evidence` 상위 N 안인가
- `report_nutrient_analysis`에 숫자·`%`·단위 → **거절**
- 사후 `filter_food_safety` 재실행
- 기피 근거를 인용했는데 `reason`에 무엇을 피했는지가 없으면 → **거절**
- 근거 0 → `kind="general"` + 코드 템플릿으로 `reason` 덮어쓰기

**프롬프트**: 고정 구획(역할·하지 않는 것·후보·근거·실패·출력) 먼저, 동적 구획(문서 행 예시·제외 목록) 뒤 → 캐시 접두 유지. 코드가 막는 것은 프롬프트에 중복하지 않습니다.

DoD: 가짜 LLM으로 9개 tool 전부 1회 호출 경로 통과 · `model_calls ≤ 1`.

---

## S7. 출력 채널

| 채널 | 내용 |
| --- | --- |
| `suggestions` | **3개** · `draft` · `+24h` · `kind` 필수 · 사후 필터 후 3개 미만이면 재샘플링 + 재호출 1회 · 저장 즉시 추천 카드 화면 |
| `readouts` | 영양 서술(model) · 미지원·기록 부족 안내(**code**) |
| `needs_observation` | `polarity IS NULL` candidate 1개만 |
| `event_requests` | **없음** (식사 알림은 Memory 프롬프트 규칙) |

DoD: `authored_by="code"` 문자열이 상수와 글자 단위로 같음 · `status`는 tool 인자에 없음.

---

## S8. 파이프라인 통합

- `IMPLEMENTED_AGENTS`에 `food` 추가 · 라벨 2개 라우팅
- `asyncio.gather(..., return_exceptions=True)` · 부분 실패 이벤트
- 예산: run 5회 · Agent 1회(필터 후 3개 미만 재호출만 2회) · 0회 경로 카운트 검증
- 20초 초과 → 부분 결과

DoD: 혼합형 입력("오늘 당근 먹었어. 저녁 뭐 줄까?")에서 **Memory 저장 → Food 추천** 순서가 지켜짐.

---

## S9. 테스트

| 파일 | 핵심 |
| --- | --- |
| `test_food_tool_schema.py` | 직렬화 · **쓰기 tool은 `daycare_meal` 2개뿐** · 설명에 값 예시 없음 |
| `test_food_registry.py` | 게이팅 표 · 허용 목록 밖 미실행 · 코드 tool 비노출 |
| `test_food_safety.py` | 알레르기·꿀 차단 · 조회 실패 → AI 0회 · `unchecked` 표시 · **기피로는 풀이 줄지 않음** |
| `test_food_nutrition.py` | 행 수 미달 → `insufficient` · EAR/RNI 구분 · 히스테리시스 |
| `test_food_evidence.py` | 기피(−1)가 근거로 나감 · 기피 인용 시 `reason` 필수 · **부족 식품군 > 기피** 가중 · `polarity IS NULL` 제외 |
| `test_food_gating.py` | 3/4 · 11/12 · 35/36 · 47/48개월 양쪽 · 동의 철회 |
| `test_food_output.py` | 풀 밖 메뉴 거절 · `%` 0건 · 근거 0 → general |
| `test_food_sampling.py` | 같은 `run_id` 동일 · 다른 run 다름 · 필터 뒤 실행 |
| `test_food_daycare.py` | **신규** — span 검증 · `resolve_meal_date` 사전 · slot 모호 시 되묻기(갱신) / 전부 삭제(결석) · 갱신 뒤 안전 경고는 내되 **저장은 막지 않음** · 승인 모달 0건 · 보존 범위 밖 거절 · 행 0건이면 라벨 닫힘 |
| 계약 테스트 | 저장된 API 응답 샘플 → dataclass 변환 |
| 골든 | 입력 10종 → 출력 스냅샷 (문구 회귀) |

전부 가짜 LLM·가짜 포트(외부 호출 0).

---

## S10. 라이브 테스트

### 10-1. 드라이런 (읽기만)
실제 DB·실제 API, **`suggestion` INSERT는 끈다.** 팀 계정 아이 데이터로 20건 실행 → 로그로 후보·근거·차단 사유 확인.

확인: 메뉴 해석률 · 기록 행 수 분포 · 안전 차단 오탐(먹어도 되는 걸 막았나) · 응답 시간 p95.

### 10-2. 내부 도그푸딩 (1~2주)
팀원 실제 아이 계정. 매일 1회 이상 사용.

| 지표 | 목표 |
| --- | --- |
| 안전 미탐(위험 후보 노출) | **0건** — 하나라도 나오면 롤백 |
| 메뉴 해석률 | ≥ 0.8 |
| 일반 추천 비율 | 측정만 (Curator 전이라 높을 것) |
| `suggestion` 승인율 | 측정만 |
| p95 응답 | < 10초 |
| AI 0회 경로 실제 0회 | 100% |

### 10-3. 롤아웃 게이트

| 게이트 | 조건 |
| --- | --- |
| G1 | 안전 미탐 0 · 골든 스냅샷 전부 통과 |
| G2 | 알레르기 등록 아이 3명 이상에서 차단 동작 확인 |
| G3 | 영아(4–11개월) 계정 1건 이상 실사용 |
| G4 | 20초 초과 시 부분 결과 확인 |

### 10-4. 롤백
`IMPLEMENTED_AGENTS`에서 `food` 제거 → Supervisor가 라우팅하지 않음. 스키마는 되돌리지 않습니다(읽기 전용이라 무해).

### 10-5. 관측
`{run_id, child_id, stage, task_type, model_calls, coverage, blocked_count, evidence_mode, latency_ms}` — **메뉴명·알레르기 라벨 원문은 남기지 않습니다**(NF-05).

---

## 11. 리스크

| 리스크 | 신호 | 대응 |
| --- | --- | --- |
| 급식 데이터가 끝내 안 붙는다 | OCR 연결 지연 | `lookup_daycare_menu`를 데이터 있는 아이만 열기(이미 설계됨) |
| 메뉴 해석률이 낮다 | `unresolved` 다수 | `menu_alias` 보강 · 기록 부족 안내로 정직하게 |
| 알레르기 오탐으로 후보가 0개 | "조건에 맞는 메뉴가 없어요" 빈발 | guards 보강 · 풀 크기 로그 |
| F-1 재료 누락 | 사후 필터 통과했는데 위험 | 재료는 카탈로그에서만(설계 완료) · 미해석은 풀에서 제외 |
| 섭취기준 시드 오타 | 판정이 한쪽으로 쏠림 | 2인 대조 · 골든 테스트 |

---

## 12. 열린 항목

F-1 · F-2 · F-3 · F-6 · F-13 · F-16~18 ([`Food_Agent_명세.md`](Food_Agent_명세.md) §10) · FT-1~7 ([`food_agent_own_table.md`](food_agent_own_table.md) §10) · N-1~8 ([`영양소_계산_설계.md`](영양소_계산_설계.md) §9)
