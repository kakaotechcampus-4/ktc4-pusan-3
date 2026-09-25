# Health Agent 구현 계획 (`health_plan.md`)

> 테이블 생성부터 라이브 테스트까지.
> 통합 대상: [`Health_Agent_명세.md`](Health_Agent_명세.md) · [`Health_Tool_명세.md`](Health_Tool_명세.md) · [`health_agent_own_table.md`](health_agent_own_table.md) · [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §5 · [`Agent_공통규약.md`](../shared/Agent_공통규약.md) · [`외부연결_계획.md`](../shared/외부연결_계획.md) §5 · [`RAG_plan.md`](../shared/RAG_plan.md) §5

---

## 0. 이 Agent의 특이점

1. **마지막에 붙입니다.** 앞의 셋으로 뼈대가 굳은 뒤에 시작합니다. 안전에 가장 민감하고 외부 의존이 가장 많습니다.
2. **유일하게 아이 데이터를 직접 씁니다** — 복약 3테이블.
3. **라벨 6개 중 4개가 모델 호출 0회**입니다. 판단은 코드와 공식 기준표가 합니다.
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
| `reference/symptom_codes.yaml` | 증상 표준 코드 + 별칭 | `health_phrases.yaml`의 `seek_care` 키와 **동일 어휘** |
| `allergen_term` | Food와 공유 | 이미 적재돼 있으면 재사용 |


---

## 4. S2 — 스키마 · 권한

```
1) prescription_draft        (OCR 팀 소유 · Health는 SELECT)
2) medication_schedule       (+ indication_text · symptom_codes · storage · med_form · source · source_draft_id. status는 active/completed 둘뿐)
3) medication_dose           (generated column)
4) medication_dose_log
5) observation_health.temperature · measured_at · measure_site   ← Memory 소유, 변경 요청
```

DDL은 [`health_agent_own_table.md`](health_agent_own_table.md) §7.

**권한**

| role | 부여 |
| --- | --- |
| agent | `medication_schedule` · `medication_dose` · `medication_dose_log` CRUD. `medication_schedule` DELETE는 **중단·수정 승인 경로만** |
| agent | `health_safety` write **비부여** · `prescription_draft` write **비부여** |
| app (보호자 세션) | `health_safety` INSERT (`created_by = parent.id`) |

DoD: 왕복 마이그레이션 · `make_interval` IMMUTABLE 확인 · agent role로 `health_safety` INSERT → 거부 · 발송 쿼리가 `status='active'`와 조인함 · DELETE는 중단·수정 승인 경로에서만 호출됨.

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
| 6-4 | `group_symptom_episodes` | 48시간 병합 · **3일 연속 발열 = 1건** |
| 6-5 | `check_symptom_repetition` | 에피소드 기준 14일 3회 |
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
| 7-4 | `update` · `stop` | update는 `op="update"` 초안(DB 미접촉) · stop은 `status='stopped'` 즉시 반영 · 대상 모호하면 역질의 |
| 7-4a | 제출 처리 (백엔드) | create → INSERT, update → 같은 행 UPDATE + dose 재생성 (한 트랜잭션) · `missing` 비지 않으면 거절 |
| 7-5 | `log_dose_taken` · `get_next_dose` | 미래 기록 거절 · 중복 확인 · **반환에 `can_take` 없음** |
| 7-6 | `lookup_prescription_draft` | 낮은 신뢰도 칸 빈값 유지 · 확인 전 등록 차단 |

승인 모달의 실제 시각: 생성·수정 결과에 시각이 없으면 **tool 호출 실패**로 처리합니다.

---

## 10. S8~S9 — 진료 요약 · 출력 채널

`visit_summary`만 모델 1회입니다. 코드가 에피소드로 묶어 건네고, 모델은 문장으로 옮깁니다.

**사후 검사**: 줄마다 `source_ref` · 약명·용량 원문 대조 · 해석 금지어.

**출력 채널**

| 채널 | 종류 |
| --- | --- |
| `readouts` | `checkup_schedule`·`symptom_timeline`·`place_list`·`medication_notice`·`symptom_repeat`·`fever_timeline`·`dose_status`·`med_notice_form`·`emergency_card`·`checkup_context`·`prescription_draft`·`unsupported` |
| `event_requests` | 검진 알림 — 초안 payload, 제출 시 저장 |
| `needs_observation` | 복용 시점 · 대상 약 |
| `suggestions` | **없음** |

hook 2개는 pipeline에서 Memory 저장 **직후 동기 실행**합니다.

---

## 11. S10 — Supervisor 안전 정합

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
| `test_medication.py` | anchor 매핑 · **생성·수정이 DB를 건드리지 않음**(초안만) · 제출 전 `medication_schedule` 0건 · `missing` 비지 않으면 제출 잠김 · 제출 시 create=INSERT / update=같은 행 UPDATE · **stop은 `status='stopped'`이고 `medication_dose_log`가 남음** · 발송 0건 · `notice_times` 필수 · span 검증 · 영아는 `fixed` 19:30 |
| `test_dose_log.py` | 미래 기록 거절 · 중복 · `can_take` 필드 **부재** |
| `test_symptom_episode.py` | 3일 연속 발열 = 1건 · 14일 3회 판정 |
| `test_fever_timeline.py` | 약 계열 단어 0건 · 다음 예정 출처 |
| `test_care_handoff.py` | 서식 불변 · 서명 문구 · 복약 0건 처리 |
| `test_health_wording.py` | **진단명 사전 전체** 회귀 (저체중·비만·장염·폐렴 …) |
| `test_emergency_parity.py` | 규칙 ↔ 문서 행 1:1 |
| 계약 테스트 | 심평원·E-Gen 응답 샘플 |
| 골든 | 라벨 5종 스냅샷 |

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
| 오인식 중단 | "그 약 끊었어" 대상 착오 | soft delete라 되돌릴 수 있지만 그 사이 알림이 빠짐 → 대상 모호하면 반드시 되묻기 |
| 교차투약 요구가 들어온다 | 사용자 문의 | 기능화하지 않음. 기록 나열까지가 경계 |

---

## 15. 열린 항목

H-1 · H-2 · H-5 · H-6 · H-8~H-12 ([`Health_Agent_명세.md`](Health_Agent_명세.md) §13) · M-1 · M-3~M-10 ([`health_agent_own_table.md`](health_agent_own_table.md) §10) · E-2 · E-5 ([`외부연결_계획.md`](../shared/외부연결_계획.md) §9)
