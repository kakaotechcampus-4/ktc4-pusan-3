# Health Agent 구현 계획 (`health_plan.md`)

> 테이블 생성부터 라이브 테스트까지.
> 통합 대상: [`Health_Agent_명세.md`](Health_Agent_명세.md) · [`Health_Tool_명세.md`](Health_Tool_명세.md) · [`health_agent_own_table.md`](health_agent_own_table.md) · [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §5 · [`Agent_공통규약.md`](../shared/Agent_공통규약.md) · [`외부연결_계획.md`](../shared/외부연결_계획.md) §5 · [`RAG_plan.md`](../shared/RAG_plan.md) §5

---

## 0. 이 Agent의 특이점

1. **마지막에 붙입니다.** 앞의 셋으로 뼈대가 굳은 뒤에 시작합니다. 안전에 가장 민감하고 외부 의존이 가장 많습니다.
2. **유일하게 아이 데이터를 직접 씁니다** — 한 번 물어 확인한 복약 중단 하나뿐입니다. 복약 코스의 생성·수정은 초안 → 보호자 제출이고, 복용 체크는 보호자가 화면에서 회차마다 합니다(10-20).
3. **라벨 5개 중 3개가 모델 호출 0회**입니다. 판단은 코드와 공식 기준표가 합니다.
4. **의료 검수가 릴리스 게이트**입니다. 응급 신호·병원 안내 문구는 검수 전에 나가지 않습니다.

---

## 1. 전체 단계

| 단계 | 내용 | 선행 | 산출물 |
| --- | --- | --- | --- |
| S0 | 하드 선행 닫기 | – | 결정 4건 |
| S1 | 기준 상수 확보 | S0 | 검진 · 접종 · 증상코드 |
| S2 | 스키마 · 권한 | S0 | 복약 3 + 공유 변경 2 |
| S3 | 고정 문구 + 의료 검수 **(임계 경로)** | S2 | `reference/health_phrases.yaml` 56행 |
| S4 | 외부 어댑터 | S0 | 심평원 · E-Gen · 달빛 |
| S5 | 포트 · 컨텍스트 · 게이팅 | S1 S2 | mock `run()` |
| S6 | 코드 tool (계산·서식) | S1 S3 S5 | 0회 경로 3개 라벨 |
| S7 | 복약 쓰기 경로 | S2 S5 | CRUD + 기록 |
| S8 | 모델 tool (진료 요약) | S6 | 1개 라벨 |
| S9 | 출력 채널 · hook | S7 S8 | readout 7종 · medication_drafts |
| S10 | 파이프라인 · Supervisor 안전 | S9 | 응급 사전검사 정합 |
| S11 | 테스트 | S10 | 안전 회귀 |
| S12 | 라이브 테스트 | S11 | 단계 롤아웃 |

### 1-1. 이슈 순서 (10-18)

위 단계를 이슈 여덟로 나눠 순서대로 진행한다. 1번은 develop 으로 먼저 들어갔고, 2번 브랜치(`feat/ai-519-health-skeleton`)가 통합 브랜치다. 3–6 은 앞 PR 이 머지된 뒤 시작한다. 7 은 2 이후 3–6 과 병렬로 해도 된다. 8(연결)은 5번의 BE/FE 작업이 들어온 뒤 시작한다.

| # | 이슈 | 맞물리는 단계 | 다른 직군 확인 |
| --- | --- | --- | --- |
| 1 | 명세 개정 4 (#514) | S0 | PM — 루트 문구 |
| 2 | 뼈대 + 케이스 재실행 eval | S5 | – |
| 3 | 검진 · 접종 (`schedule_check`) | S1 · S6 일부 | – |
| 4 | 증상 반복 안내 — 날 수 세기 · 만성질환 연결 | S1 · S3 · S6 | (질환 × 증상) 안내 문구 확인 — 원문 대조 · 연결 적절성 (의료인 검수 필수 아님, PM 확인 대기) |
| 5 | 복약 기록 · 초안 + 전달 서류 | S2 · S6 · S7 | BE · FE — 복약 테이블 · 제출 API · 회차 체크 API · 초안 카드 · 체크 화면 (8번 선행) |
| 6 | 진료 준비 정리 · 열 타임라인 · 검진 맥락 요약 | S2(체온 3칸) · S8 | 의료 검수 — 3개월 미만 발열 응급 기준 |
| 7 | 병원 찾기 (`place_lookup`) — 다른 작업자 | S4 · S6 | PM — 위치 약관 목적 · 다시 동의, API 키 · 서버 IP |
| 8 | 연결 — Supervisor · pipeline · 복약 어댑터 (§16) | S9 · S10 | BE · FE — SSE 번역 · 되묻기 보관 · 답하기 화면은 같이 |

연결 이슈(§16)는 **5번의 BE/FE 작업이 develop 에 들어온 뒤** 연다(10-20 변경 — 원래는 7번 확인 직후였다). 복약을 실제 테이블로 읽지 못하는 채로 연결하면 `medication` · `care_handoff` 가 빈 저장소로 돌기 때문이다. 처방전 OCR 경로(`prescription_draft`)는 이 순서 밖이다 — OCR 파이프라인과 테이블이 생기면 따로 연다.

---

## 2. S0 — 하드 선행

| # | 결정 | 없으면 | 담당 |
| --- | --- | --- | --- |
| 1 | **의료 검수자 확보** | 응급 문구·`seek_care` 릴리스 불가 | PM |
| 2 | ~~`safety_confirmations` 채널 승인~~ | ✅ 폐기 — 알레르기 후보 감지를 v1에서 뺐다 | – |
| 3 | `care_handoff` 라벨 승인 (H-7) | 투약의뢰서·응급 카드가 갈 라벨 없음 | PM |
| 4 | ~~hard delete 시 복용 기록 처리~~ | ✅ 닫힘 — 중단을 soft delete(`status='stopped'`)로. M-12·M-14 함께 닫힘 | – |

> 2026-09-22: 성장 판정 제거로 `child.gender` · 성장도표 LMS · `child_growth_log` 단위 · `growth_check` 승인 네 건이 Health 선행에서 빠졌다.

추가로 **OCR 팀과 `prescription_draft` 소유 합의**가 필요합니다. Health는 읽기만 합니다.

---

## 3. S1 — 기준 상수

| 파일 | 내용 | DoD |
| --- | --- | --- |
| `reference/checkup_nhis.yaml` | 일반 8회 · 구강 4회 | 월령 범위 검증 |
| `reference/vaccine_2026.yaml` | 표준예방접종일정 | **매년 교체** 절차 문서화 |
| `reference/symptom_codes.yaml` | 증상 표준 코드 + 별칭 | `health_phrases.yaml`의 `seek_care` 키 · `condition_guidance.yaml`의 증상 키와 **동일 어휘** |
| `reference/condition_codes.yaml` (10-18) | 질환 표준 코드 + 별칭 + guards — 보호자가 `health_safety`에 적는 만성질환 이름을 맞춘다 | 사전 밖 이름은 기본 문장으로 떨어지는지 · 알레르기 항목은 대상 아님 |
| `reference/condition_guidance.yaml` (10-18) | (질환 코드 × 증상 코드) → 고정 안내 문구 + 출처 | **원문 대조 + 연결 적절성 확인**(작성자 가능, 의료인 검수 필수 아님) · 출처는 공공누리 0·1 · 고시 · CC BY만 · `medical_reviewed: true` + `reviewed_by` 가 있는 행만 로드 |
| `allergen_term` | Food와 공유 | 이미 적재돼 있으면 재사용 |


---

## 4. S2 — 스키마 · 권한

```
1) prescription_draft        (OCR 팀 소유 · Health는 SELECT)
2) medication_schedule       (+ indication_text · symptom_codes · storage · med_form · source · source_draft_id. ends_on NOT NULL · status는 active/stopped 둘뿐 — 끝남은 ends_on 으로 계산)
3) medication_dose           (회차 1행 + checked_at 체크 칸 · generated column)
4) observation_health.temperature · measured_at · measure_site   ← Memory 소유, 변경 요청
```

DDL은 [`health_agent_own_table.md`](health_agent_own_table.md) §7.

**권한**

| role | 부여 |
| --- | --- |
| agent | `medication_schedule` 중단 UPDATE(`status` · `updated_at`) · 나머지는 SELECT (10-18, 10-20 — 복용 체크는 보호자 화면) |
| app (보호자 세션) | 복약 초안 제출 — `medication_schedule` · `medication_dose` INSERT/UPDATE, 수정 시 아직 오지 않은 회차만 재생성. 회차 체크(`checked_at`) 채우기·비우기. `stopped` 코스 수정은 거절 |
| agent | `health_safety` write **비부여** · `prescription_draft` write **비부여** |
| app (보호자 세션) | `health_safety` INSERT (`created_by = parent.id`) |

DoD: 왕복 마이그레이션 · `make_interval` IMMUTABLE 확인 · agent role로 `health_safety` INSERT → 거부 · 발송 쿼리가 `status='active'`와 조인함 · agent role로 `medication_schedule` 의 `status` · `updated_at` 밖 칸 UPDATE → 거부 · `medication_dose` DELETE는 수정 제출(dose 재생성)에서만 호출됨.

---

## 5. S3 — 문서 행 + 의료 검수 (임계 경로)

| 순위 | `row_type` | 행 수 | 검수 |
| --- | --- | --- | --- |
| P0 | `emergency_sign` | 10 | **의료 검수 필수** |
| P0 | `seek_care` | 15 | **의료 검수 필수** |
| P1 | `checkup_round` | 8 | 교차 검수 |
| P1 | `vaccine_note` | 20 | 교차 검수 |
| P2 | `measure_guide` · `visit_prep` | 6 | 교차 검수 |

- 고정 문구는 **테이블이 아니라 `reference/health_phrases.yaml`입니다**(2026-09-22). 임베딩도 의미 검색도 쓰지 않고 키 정확 일치로만 꺼냅니다. 비슷한 문구가 잘못 끌려오면 안 됩니다.
- `medical_reviewed=true`가 아니면 `emergency_sign`·`seek_care`는 `approved`가 될 수 없습니다(CHECK).
- `emergency_sign` 행과 Supervisor 규칙은 `lookup_key`로 **1:1**이어야 합니다 → CI 테스트.
- 진단 표현 lint(`~염`·`~증`·의심·진단·처방·용량) 통과 필수.

---

## 6. S4 — 외부 어댑터

| 어댑터 | 캐시 | DoD |
| --- | --- | --- |
| 심평원 병원정보 | 24h | 진료과목 코드 확정(E-2) · 계약 테스트 |
| 국립중앙의료원 응급의료정보 | **캐시 금지** | 실시간 · 타임아웃 2.5s · 재시도 0 |
| 달빛어린이병원 목록 | 월 1회 동기화 | 심평원 좌표 조인 |

공통: 내부 dataclass 변환 · 실패는 전용 예외 · **좌표는 요청 바디로만, 저장·로그·예외 메시지에 없음**.

---

## 7. S5 — 포트 · 컨텍스트 · 게이팅

**포트**: `observation_health` · `observation_food` · `health_safety` · `medication_*` · `prescription_draft` · `child` · 기준 상수 · 병원 API · consent

**게이팅**

| 축 | 닫히는 것 |
| --- | --- |
| `child_health` 동의 철회 | **전 라벨** |
| 위치 권한 없음 | `place_lookup` |
| `health_safety` 조회 실패 | `visit_summary` 진행 + 경고 줄 |
| 진행 중 복약 0건 | 투약의뢰서 (응급 카드는 동작) |
| 체온 기록 0건 | `build_fever_timeline` |

DoD: mock `run()` · 라벨 5개 각각 `tools_for()` 스냅샷 테스트.

---

## 8. S6 — 코드 tool (모델 0회 경로)

| 순서 | tool | DoD |
| --- | --- | --- |
| 6-2 | `compute_checkup_schedule` · `compute_vaccine_schedule` | 월령 → 차수 · **"놓쳤어요" 0건** · 72개월+ 전환 |
| 6-3 | `normalize_symptom_term` | 사전 적용 · 미등록 표현은 원문 유지 |
| 6-4 | `group_symptom_episodes` | 48시간 병합 · **3일 연속 발열 = 1건** (타임라인 표시용) |
| 6-5 | `check_symptom_repetition` | **기록된 날** 기준 최근 7일 3일 · 같은 날 여러 번 = 1일 · 3일 연속 = 3일 (10-18) |
| 6-5a | `lookup_condition_guidance` | 질환 사전 매칭 · (질환 × 증상) 행 있을 때만 질환 문장·카드 · 알레르기 제외 · 사전 밖 이름은 기본 문장 · **쓰기 0건** |
| 6-6 | `build_fever_timeline` | 약 계열 단어 0건 · 다음 예정은 **등록 일정에서만** |
| 6-7 | `find_pediatric_places` | 시간대 분기 · 실패 문구 |
| 6-8 | `summarize_checkup_context` | **발달 문항 0건** |
| 6-9 | `build_emergency_card` · `compose_daycare_med_notice` | 고정 서식 · 서명 문구 항상 · 모델 0회 |

**이 단계가 끝나면 라벨 5개 중 3개가 완성됩니다**(schedule_check · place_lookup · care_handoff).

---

## 9. S7 — 복약 쓰기 경로

| 순서 | 작업 | DoD |
| --- | --- | --- |
| 7-1 | `resolve_dose_timing` | 표현 사전 · 미해결 → 역질의(저장 0건) · `fixed`는 발화에 시각이 있을 때만 |
| 7-2 | `validate_verbatim_spans` | `title`·`dosage`·`indication`이 **발화 또는 확인된 draft**의 부분 문자열 |
| 7-3 | `create_medication_schedule` | 코스+dose N개 **한 트랜잭션** · 항상 `draft` · `notice_times[]` 필수 필드 |
| 7-4 | `update` · `stop` | update는 `op="update"` 초안(DB 미접촉) · stop은 한 번 묻고(그 run 변경 0) '네' 다음 run 에서 `status='stopped'` · `updated_at`=중단 시각 · 대상 모호하면 역질의 |
| 7-4a | 제출 처리 (백엔드) | create → INSERT 코스 + 기간만큼 회차 행, update → 같은 행 UPDATE + 아직 오지 않은 회차만 재생성 (한 트랜잭션) · `missing` 비지 않으면 거절 · `stopped` 코스 수정 거절 |
| 7-5 | `get_next_dose` · "먹였어" 안내 | 오늘 체크 n/m · 마지막 체크 = 회차 예정 시각 · 쓰기 0 · **반환에 `can_take` 없음** |
| 7-6 | `lookup_prescription_draft` | 낮은 신뢰도 칸 빈값 유지 · 확인 전 등록 차단 |

승인 모달의 실제 시각: 생성·수정 결과에 시각이 없으면 **tool 호출 실패**로 처리합니다.

---

## 10. S8~S9 — 진료 요약 · 출력 채널

`visit_summary`만 모델 1회입니다. 코드가 에피소드로 묶어 건네고, 모델은 문장으로 옮깁니다.

**사후 검사**: 줄마다 `source_ref` · 약명·용량 원문 대조 · 해석 금지어.

**출력 채널**

| 채널 | 종류 |
| --- | --- |
| `readouts` | `checkup_schedule`·`symptom_timeline`·`place_list`·`medication_notice`·`symptom_repeat`·`condition_guidance`·`fever_timeline`·`dose_status`·`med_notice_form`·`emergency_card`·`checkup_context`·`prescription_draft`·`unsupported` |
| `event_requests` | 검진 알림 — 초안 payload, 제출 시 저장 |
| `needs_observation` | 복용 시점 · 대상 약 |
| `suggestions` | **없음** |

hook(`check_symptom_repetition` → `lookup_condition_guidance`)은 pipeline에서 Memory 저장 **직후 동기 실행**합니다.

---

## 11. S10 — Supervisor 안전 정합

**상태** — 10번 PR ① 에서 끝났다. 응급 사전검사는 `app/agents/supervisor/emergency.py` · `reference/emergency_signs.yaml` · `app/rules/emergency.py`, 양방향 검사는 `tests/unit/agents/supervisor/test_emergency.py`, 경계 예시와 라벨 5개 라우팅은 `routing_cases.py` 의 HR01~HR11. 응급 표현 목록은 H-1 의료 검수 전 가안이다.

| 작업 | DoD |
| --- | --- |
| 응급 사전검사 규칙 ↔ `emergency_sign` 행 1:1 | CI에서 양방향 검사 |
| `guarded_diagnosis` 경로 | "이거 폐렴이야?" → Health 미도달, 고정 안내 |
| 경계 예시 투입 | 복약·검진 줄 + "잘 크고 있어?" → growth 확인 |
| 라벨 5개 라우팅 | `care_handoff` 포함 |

---

## 12. S11 — 테스트

| 파일 | 핵심 |
| --- | --- |
| `test_health_gating.py` | 동의 철회 → 전 라벨 0회 |
| `test_medication.py` | anchor 매핑 · **생성·수정이 DB를 건드리지 않음**(초안만) · 제출 전 `medication_schedule` 0건 · `missing` 비지 않으면 제출 잠김 · 제출 시 create=INSERT / update=같은 행 UPDATE · **stop 은 묻는 run 에서 변경 0건, '네' 다음 run 에서 `status='stopped'` · `updated_at`=중단 시각이고 회차 행·체크가 남음 · '아니요' 면 변경 0건** · 발송 0건 · `notice_times` 필수 · span 검증 · 영아는 `fixed` 19:30 |
| `test_dose_status.py` | "먹였어" · "못 먹였어" 쓰기 0 · 오늘 체크 n/m · 마지막 체크 = 회차 예정 시각 · `can_take` 필드 **부재** |
| `test_symptom_episode.py` | 3일 연속 발열 = 에피소드 1건 · 반복은 날 수(최근 7일 3일 · 같은 날 = 1일 · 3일 연속 = 3일) |
| `test_condition_guidance.py` | 질환 사전 매칭 · 행 있을 때만 질환 문장 · 알레르기 제외 · 출처 동봉 · 쓰기 0건 · 미검수 행 로드 거부 · 원인·질환 추정 표현 0건 |
| `test_fever_timeline.py` | 약 계열 단어 0건 · 다음 예정 출처 |
| `test_care_handoff.py` | 서식 불변 · 서명 문구 · 복약 0건 처리 |
| `test_health_wording.py` | **진단명 사전 전체** 회귀 (저체중·비만·장염·폐렴 …) |
| `supervisor/test_emergency.py` | 규칙 ↔ 사전 행 1:1 · 신호마다 걸리는 예시 · 아이 상태가 아닌 말은 안 걸림 |
| 계약 테스트 | 심평원·E-Gen 응답 샘플 |
| 골든 | 라벨 5종 스냅샷 |

### 12-1. 케이스 재실행 (라이브 eval)

tool 을 하나 붙일 때마다 지금까지의 케이스를 다시 돌려 응답을 읽는다.

- 케이스 원본은 `test_input_health.txt` · `test_expected_health.md`. `tests/eval/agents/health/test_health_run.py` 가 Health 케이스를 전부 돌리고 판정을 실패 · 미구현(GAP) · 참고로 남긴다.
- 미구현은 실패가 아니라 GAP — `_result.txt` 에 XFAIL 로 찍힌다. Health 로 오지 않는 입력(응급 · 진단 문의 · 성장 질문 · Memory 기록)은 SKIP 으로 남아 이유가 보인다.
- tool 을 붙이는 이슈가 자기 라벨 케이스를 GAP → 판정으로 바꾸고, 매 이슈마다 전체를 다시 돌린다. 공통 실패 조건(`test_expected_health.md` §0)은 미구현 케이스에도 건다.
- 실행: `cd apps/api && uv run pytest tests/eval/agents/health/test_health_run.py -m live -s`

---

## 13. S12 — 라이브 테스트 (단계 롤아웃)

안전 등급이 달라서 **한 번에 열지 않습니다.**

| 순서 | 여는 것 | 게이트 |
| --- | --- | --- |
| 1 | `medication` (CRUD + 기록) | 확인 모달 시각 표시 100% · 승인 전 발송 0건 |
| 2 | `care_handoff` | 서식 불변 · 서명 문구 |
| 3 | `schedule_check` | "놓쳤어요" 0건 |
| 4 | `visit_summary` (모델 1회) | 해석 표현 0건 · ref 누락 0건 |
| 5 | `place_lookup` | API 실패 시 119 안내 |
| 6 | hook 1종 | 증상 반복 판정 관측 |

### 13-1. 드라이런
복약 쓰기를 **테스트 계정으로 한정**하고 알림 발송은 끕니다. 20건 실행 후 시각 매핑·고지 문구 확인.

### 13-2. 내부 도그푸딩 (2주 이상 — 가장 길게)

| 지표 | 목표 |
| --- | --- |
| **진단명 노출** | **0건** — 롤백 |
| **약명·용량 불일치** | **0건** — 롤백 |
| 알림 시각 오매핑 | 0건 |
| 응급 신호 미탐 | 0건 (시나리오 스크립트로 확인) |
| p95 응답 | < 8초 (0회 경로는 < 2초) |

### 13-3. 롤백
라벨 단위로 끕니다(`tools_for`가 `()` 반환). 복약 데이터는 유지하고 **쓰기만 차단**합니다. 이미 등록된 알림은 계속 울리므로, 롤백 시 보호자에게 알림 상태를 고지합니다.

### 13-4. 관측
`{run_id, child_id, label, model_calls, band, episode_count, blocked_reason, latency_ms}` — **약명·증상·이유 원문은 로그에 남기지 않습니다**(NF-05).

---

## 14. 리스크

| 리스크 | 신호 | 대응 |
| --- | --- | --- |
| 의료 검수자를 못 구한다 | S3 지연 | 응급·`seek_care` 문구를 국가건강정보포털 원문 범위로만 제한하고 그 사실을 화면에 표시 |
| 체온 필드가 안 생긴다 | Memory 일정 | `build_fever_timeline`만 미출시(다른 tool 영향 없음) |
| OCR 처방전 품질 | 빈칸 다수 | 등록 버튼 잠금이 이미 방어. 수기 입력 경로 유지 |
| 복약 알림 오발송 | 시각 오매핑 | 초안 확인 모달(`notice_times`) + `UNIQUE(schedule_id, scheduled_time)` |
| 오인식 중단 | "그 약 끊었어" 대상 착오 | 중단은 약 이름을 넣어 한 번 묻고(10-18) '네' 일 때만 바꾼다. 대상이 모호하면 그 전에 어느 약인지 되묻기 |
| 교차투약 요구가 들어온다 | 사용자 문의 | 기능화하지 않음. 기록 나열까지가 경계 |

---

## 15. 열린 항목

H-1 · H-8 · H-9 · H-11 · H-13 ([`Health_Agent_명세.md`](Health_Agent_명세.md) §13) · M-1 · M-3~M-10 ([`health_agent_own_table.md`](health_agent_own_table.md) §10) · E-2 · E-5 ([`외부연결_계획.md`](../shared/외부연결_계획.md) §9)

---

## 16. 연결 이슈에서 할 일

**상태 (10번 PR ①)** — 끝난 것: Supervisor health 라벨 · `IMPLEMENTED_AGENTS` · `_RUNNERS` · `_WRITING_TASKS` · 추천 없는 Agent 표(`NON_RECOMMENDING_AGENTS`) · pipeline 출력(복약 초안 · 되묻기 · 검진 알림 일정 초안) · 건강 기록 저장 직후 hook(영아 발열 · 증상 반복) · 등록된 약 대조 · 응급 사전검사. 남은 것: **병원 찾기 위치**(PR ②, PM 확인 뒤 — 지금은 `place.preparing` 문구로 닫힌다) · SSE 번역 · 되묻기 이어받기 · 화면 확인(11번).

5번의 BE/FE 작업이 들어온 뒤 한 이슈로 연다(10-20 변경). 그 전에는 Health 가 pipeline 에 붙지 않아서 Supervisor 가 health 로 보내면 `unavailable` 로 끝난다. 그 사이 빠지는 게 없도록 여기 고정한다.

**시작 조건**

- 5번 BE/FE 작업이 develop 에 들어왔다 — 복약 테이블 2개(회차 1행 + 체크 칸, `ends_on` 필수) · 초안 제출 API(게이트 ㉡) · 회차 체크 API · 초안 카드 · 체크 화면. 내용은 5번 이슈의 "BE/FE팀에 부탁드리는 내용"
- develop 을 통합 브랜치(519)로 받았다. Growth 의 Supervisor 연결(#531)이 `entrypoint` · `pipeline` · `supervisor/routing` · `schemas` · `prompt` · `agent_ports` · `runner` 를 같이 고치므로, 그게 develop 에 들어온 뒤에 받는다

**연결 이슈에서 하는 것 (이시하)**

- **Supervisor** — `Segment` 에 health 라벨(`visit_summary` · `schedule_check` · `place_lookup` · `medication` · `care_handoff`), 프롬프트 경계 예시(명세 §3), `routing._task_label`
- **`routing.IMPLEMENTED_AGENTS` · `pipeline._RUNNERS`** 에 health
- **`pipeline.RECOMMENDING_TASKS` 계약** — 키가 `IMPLEMENTED_AGENTS` 와 같아야 한다는 테스트와 `entrypoint.recommendation_task_type` 의 `[label] = …` 가 추천 없는 Agent 를 받지 못한다. 둘 중 무엇을 바꿀지 정한다
- **`pipeline._WRITING_TASKS`** 에 `medication`(확인된 중단 — Agent 가 쓰는 유일한 것)
- **pipeline 출력** — `DomainOutcome` · `AgentResult` 가 `medication_drafts` · `event_requests` · `pending` 을 싣는다 (지금은 조용히 사라진다)
- **`event_requests`**(검진 알림) → Memory 일정 tool 을 코드로 불러 초안 payload
- **hook** — Memory 가 `observation_health` 를 저장한 직후 `check_symptom_repetition` 을 동기 호출
- **복약 DB 어댑터 · 배선** — `domains/health/agent_reader.py`(Agent 포트를 테이블로) · `apps/api/CLAUDE.md` 저장 계약 예외 표 · `entrypoint._domain_contexts` · `api/agent_ports.py`
- **병원 찾기 위치** — pipeline 에서 `find_pediatric_places(location=…)` 로 위치를 넘기는 길. `now` 는 KST 벽시계로 바꿔 넘긴다. PM 확인(위치 약관 목적 추가 · 다시 동의)이 먼저다

**BE/FE 와 같이 하는 것** — pipeline 이벤트가 생겨야 끝까지 확인할 수 있어서 연결 이슈에 둔다

- **SSE 번역** — `api/runs/translate.py` 가 `medication_drafts` 를 화면 이벤트(`medication_draft`, 일정 `event_draft` 와 같은 모양)로. FE 카드는 이 모양으로 목에서 먼저 만든다
- **Health 되묻기 맥락** — 중단 확인 · 대상 약 · 복용 시점(`DomainAgentResult.pending`)을 run 사이에 보관하고, 다음 입력의 `reply_to` 로 같은 Agent 에 돌려준다. Memory 의 `api/runs/pending_reply.py` 와 같은 저장소 · 같은 보관 시간. `agent_result` 에 "답을 받는 질문" 표시, FE 는 그 질문에만 이어서 적기를 연다
- **화면** — 반복 안내 카드(읽기 전용, 출처) · 응급 카드 · 투약의뢰서 복사가 실제 run 에서 그려지는지

