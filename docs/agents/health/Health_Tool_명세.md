# Health Agent Tool 명세

> 기준: [`Health_Agent_명세.md`](Health_Agent_명세.md) · [`health_agent_own_table.md`](health_agent_own_table.md) · [`Tool_공통.md`](../shared/Tool_공통.md)
>
> **2026-09-22 갱신** — `growth_check`·`assess_growth_percentile`·성장도표 상수 제거 · 복약 생성·수정은 **초안 payload**(DB 저장 없음) · 중단 soft delete

## 0. 외부 데이터 소스

**원칙**: 기준표는 **버전 붙은 상수 파일**, 실시간 정보만 API. 출처 문서를 모델이 읽고 답하는 경로(RAG)는 없다.

| 소스 | 제공 | 용도 | 연결 | 갱신 |
| --- | --- | --- | --- | --- |
| 국민건강보험공단 **영유아 건강검진** — nhis.or.kr | 일반 8회 · 구강 4회 주기 | `compute_checkup_schedule` | `reference/checkup_nhis.yaml` | 제도 변경 시 |
| 질병관리청 **2026 국가예방접종 지침** · 표준예방접종일정표 | 백신·차수·권장 월령 | `compute_vaccine_schedule` | `reference/vaccine_2026.yaml` | **매년** |
| 예방접종도우미 — nip.kdca.go.kr | 개인 접종 기록 | 안내 링크만 | 연동 없음 | – |
| 건강보험심사평가원 **병원정보서비스** Open API (data.go.kr) | 병원 목록 · 진료과목 · 좌표 | `find_pediatric_places` (주간) | REST · 24h 캐시 | – |
| 국립중앙의료원 **응급의료정보** (E-Gen, data.go.kr) | 응급의료기관 · 실시간 가용 | `find_pediatric_places` (야간·응급) | REST · **캐시 안 함** | 실시간 |
| 달빛어린이병원 지정 목록 (보건복지부·국립중앙의료원) | 야간·휴일 소아 경증 진료기관 | 야간·휴일 1순위 | 목록 동기화 + 심평원 좌표 조인 | 월 1회 |
| 식약처 알레르기 표시 대상 19종 | 코드표 | `health_safety` 라벨 정규화 | Food `ALLERGEN_CODES` 공유 | 고시 개정 시 |
| 국가건강정보포털 health.kdca.go.kr · 대한소아청소년과학회 | 응급 신호 · 증상 문구 | Supervisor 규칙 · 고정 문구 **검수 근거** | 문서 | 검수 시 |
| **OCR 파이프라인** (처방전·약봉투) | 약품명·용량·용법 추출 텍스트 | `lookup_prescription_draft` | `prescription_draft` 테이블 **읽기만** | – |
| ~~의약품 허가 정보~~ | – | **쓰지 않음**(H-10 닫힘) | – | – |

API 오퍼레이션명·진료과목 코드(소아청소년과)는 각 활용가이드로 확정. **쓰지 않음**: 의약품 정보 API · 증상→질환 자료 · 민간 리뷰.

---

## 1. 게이팅

| 라벨 | 모델 tool | 코드 tool | 모델 호출 |
| --- | --- | --- | --- |
| `visit_summary` | search_health_observations · search_recent_meals · search_medication_schedules · compose_symptom_timeline | load_health_safety · normalize_symptom_term · group_symptom_episodes · build_fever_timeline · get_next_dose · get_health_phrase | 1 (열 타임라인만 요청 시 **0**) |
| `schedule_check` | – | compute_checkup_schedule · compute_vaccine_schedule · summarize_checkup_context · get_health_phrase | 0 |
| `place_lookup` | – | find_pediatric_places | 0 |
| `medication` | search_medication_schedules · create/update/stop_medication_schedule · log_dose_taken | resolve_dose_timing · normalize_symptom_term · get_next_dose · lookup_prescription_draft | 1 (기록·조회는 **0**) |
| **`care_handoff`** | – | search_medication_schedules · load_health_safety · compose_daycare_med_notice · build_emergency_card | **0** |
| (hook) | – | check_symptom_repetition | 0 |

닫힘: 동의 없음 → 전 라벨 · 위치 없음 → `place_lookup` · `health_safety` 실패 → `visit_summary` 진행 + 경고 줄 · 진행 중 복약 0건 → 투약의뢰서 · 체온 기록 0건 → `build_fever_timeline`.

### 연령 — tool을 여닫지 않고 계산 방식을 바꾼다

> 정본은 [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §5.

| | 0–23 | 24–35 | 36–71 | 72+ |
| --- | --- | --- | --- | --- |
| 검진·접종 | 1–4차 · 기초 접종 | 5차 | 6–8차 · 만 4–6세 추가 | 대상 종료 안내 |
| 복약 기준 시각 | 영아 취침 상수 (미정 M-3) | 공통 | 공통 | 공통 |

**접종·검진 시기는 출생일 기준**이다.

---

## 2. 모델 tool

| Tool | 입력 | 출력 | 규칙 |
| --- | --- | --- | --- |
| `search_health_observations` | `period: "3d"\|"7d"\|"14d"\|"30d"`, `symptom?` | 날짜·증상·severity·조치·trigger + ref | – |
| `search_recent_meals` | `period: "3d"` | 날짜별 `amount` 분포 (평소 대비 상·중·하) | 메뉴 영양 없음 |
| `search_medication_schedules` | `status="active"` | 코스 + dose(`scheduled_time`) | – |
| `compose_symptom_timeline` | `lines[]: {date, section, text, ref}` | `Readout(model, kind="symptom_timeline")` | ref 없는 줄 삭제 · 약명·용량 원문 대조 · 해석 금지어 필터 |
| `create_medication_schedule` | `title_span`, `dosage_span?`, `timing_spans[]`, `period_span?` | **초안 payload**(`op="create"`) — 코스 + dose N · **`notice_times[]` 필수**(확인 모달용) · `missing[]` | **DB를 건드리지 않는다.** span은 발화 원문 부분 문자열이어야 함(코드 검증) · 시각은 `resolve_dose_timing` 결과만 |
| `update_medication_schedule` | `schedule_id`, 변경 span들 | **초안 payload**(`op="update"`, `schedule_id`, `before`) · 변경 후 `notice_times[]` | 동일. 기존 코스는 제출 전까지 그대로. 제출 시 같은 행 UPDATE — 삭제 후 재생성이 아니라 복용 기록이 남는다 |
| `stop_medication_schedule` | `schedule_id` | `status='stopped'` (soft delete) · dose·복용 기록 보존 | 대상 약 2개 이상 후보면 `needs_observation` — 엉뚱한 약의 알림이 끊기므로 모호하면 되묻기 |
| `log_dose_taken` | `schedule_id`, `taken_at?`(생략 시 now), `dose_id?` | `medication_dose_log` 1행(`status='taken'`) + `Readout(code, "dose_status")` | 오늘 예정 회차가 모두 기록돼 있으면 중복 확인 되묻기 · 미래 시각 거절 |
| `log_dose_skipped` | `schedule_id`, `dose_id?` | `medication_dose_log` 1행(`status='skipped'`) + `Readout(code, "dose.skipped")` | "못 먹였어"처럼 **보호자가 명시할 때만**. 기록이 없는 것은 `skipped`가 아니라 모름이라, 이 tool을 부르지 않은 회차를 코드가 `skipped`로 채우지 않는다 |

**복약 생성·수정은 DB에 쓰지 않습니다.** 두 tool의 산출물은 `medication_drafts` 채널로 나가는 JSON 초안이고, 보호자가 확인 모달에서 제출해야 백엔드가 씁니다. `missing[]`(비어 있는 NOT NULL 칸)이 비지 않으면 화면이 제출을 막습니다. Memory의 `event` 초안과 같은 방식이고, 만들자마자 행을 쓰는 `suggestion`과는 다릅니다.

**복약 생성·수정의 입력은 span입니다.** `title_span` · `dosage_span` · `timing_spans[]` · `period_span` · **`indication_span`**(복약 이유)은 전부 원문의 부분 문자열이어야 하고, 코드가 검증합니다. 출처는 **보호자 발화 또는 `prescription_draft`의 추출 텍스트** 둘 중 하나입니다. 발화에 이유가 없으면 `indication_span`은 비웁니다 — 추정 금지.

**해석 금지어** (코드): 때문 · 의심 · 같아요 · 가능성 · ~염 · ~증 · 진단 · 괜찮아요 · 위험

## 3. 코드 tool

### `compute_checkup_schedule` · `compute_vaccine_schedule`
| 입력 | `birth_date`, `today` |
| --- | --- |
| 출력 | 현재 기간 차수 · 다음 차수 시작일 · 이 시기 권장 백신 목록 → `Readout(code, kind="checkup_schedule")` |
| 옵션 | 보호자 "알림 받기" → `EventRequest(all_day, starts_at=다음 차수 시작일, category="health")` |
| 금지 | 접종 여부 판단 · "놓쳤어요" |

검진 주기 상수: 일반 14–35일 · 4–6 · 9–12 · 18–24 · 30–36 · 42–48 · 54–60 · 66–71개월 / 구강 18–29 · 30–41 · 42–53 · 54–65개월

### `find_pediatric_places`
| 입력 | 좌표(기기, 비저장), `now`, `mode: "normal"\|"emergency"` |
| --- | --- |
| 분기 | 평일 주간 → 심평원 소아청소년과 · 야간·휴일 → 달빛어린이병원 → 응급의료기관 · emergency → 응급의료기관만 |
| 출력 | 거리순 ≤5 `{name, distance, phone, open_now}` → `Readout(code, kind="place_list")` |
| 실패 | "병원 정보를 불러오지 못했어요. 응급 시 119" |

### `resolve_dose_timing`
| 입력 | `timing_span` (발화 원문) |
| --- | --- |
| 출력 | `[(timing_anchor, offset_minutes, fixed_time?)]` \| `Unresolved` |
| 사전 | 식후 +30 · 먹고 바로 0 · 식전 −30 · 자기 전 bedtime · 일어나서 wake_up · "N시" fixed · "하루 세 번 식후" → 3행 |
| 미해결 | `needs_observation`: "식사 전후 언제 먹이면 되나요?" — 저장 안 함 |

### `normalize_symptom_term` · `group_symptom_episodes`
| | 내용 |
| --- | --- |
| `normalize_symptom_term` | "열나" · "미열" · "38도" → `fever` 표준 코드. 사전은 `reference/symptom_codes.yaml`, `health_phrases.yaml`의 `seek_care` 키와 **같은 어휘** |
| `group_symptom_episodes` | 같은 코드가 **48시간 이내**로 이어지면 한 에피소드 → `{code, from, to, days, peak_severity?, actions[]}` |
| 왜 코드인가 | 증상 반복(3회)과 타임라인이 모두 이 어휘 위에 선다. 3일 연속 발열이 3회로 세어지면 규칙이 무너진다 |
| 금지 | 원인 추정 · 중증도 판정 · "심해지고 있어요" |

### `build_fever_timeline`
| 입력 | `observation_health`(`temperature`·`measured_at`·`measure_site`) 24–48시간 · `medication_dose_log` · `medication_schedule` |
| --- | --- |
| 출력 | 시간순 나열 + 최고·최저 체온과 시각 + 마지막 복약(약 이름 **원문**) + **등록된 일정상의** 다음 예정 시각 → `Readout(code, kind="fever_timeline")` |
| 금지 | 약 성분·계열 판정(아세트아미노펜/이부프로펜) · 계열 최소 간격으로 계산한 "투약 가능 시각" · 경과 해석 |
| 선행 | `observation_health` 체온 세 칸 (공유 테이블 변경 요청) |
| 부위 | 값 옆에 **잰 부위를 함께 보여준다.** 부위마다 정상 범위가 달라 숫자만 나열하면 들쭉날쭉해 보인다. 부위별 기준값은 **코드 상수**다 — 모델이 정상 범위를 지어내지 않는다. `measure_site` 가 비면 부위 비교 없이 숫자만 낸다 |
| 우선순위 | 응급 신호(3개월 미만 발열 등)에 걸리면 타임라인보다 병원 안내가 먼저 |

### `get_next_dose`
| 입력 | `schedule_id?`(없으면 active 전부), `now` |
| --- | --- |
| 출력 | `{last_taken_at, next_scheduled_time, taken_today, planned_today}` → `Readout(code, kind="dose_status")` |
| 금지 | **`can_take` 같은 필드를 두지 않는다.** 허가·간격 판단 없음 |

### `lookup_prescription_draft`
| 입력 | `child_id`, `since` |
| --- | --- |
| 출처 | **OCR 파이프라인이 만든 `prescription_draft`.** Health는 읽기만, write 권한 없음 |
| 출력 | `{title, dosage, frequency_text, timing_text, days, confidence{field: 0~1}}` → `Readout(code, kind="prescription_draft")` |
| 규칙 | 신뢰도 낮은 칸은 **빈값 유지**(추측 금지) · 보호자가 확인·수정해야 등록 버튼 활성 · 등록 시 span 출처가 발화 대신 이 draft 텍스트 |
| 약품명 | **대조하지 않는다.** 일치 여부 표시도, 치환·보완도 없다 (H-10) |

### `get_health_phrase` — 고정 문구 조회

| 입력 | `kind`, `key`, `age_months` |
| --- | --- |
| 출처 | **`reference/health_phrases.yaml`** (테이블 아님). 코드와 함께 배포된다 |
| 출력 | 문구 1건 → 호출부의 `Readout(code)` 본문에 **글자 그대로** |
| 규칙 | 키 정확 일치만. 의미 검색·임베딩 없음 — 비슷한 문구가 잘못 끌려오면 안 된다 |
| 없으면 | 해당 안내를 **생략한다.** 모델에게 채우게 하지 않는다 |
| 부팅 검사 | `seek_care`·`emergency_sign`은 `medical_reviewed: true`가 아니면 로드하지 않는다 · `emergency_sign` 키와 Supervisor 규칙이 1:1인지 CI가 확인한다 |

### `compose_daycare_med_notice` · `build_emergency_card`
| | 내용 |
| --- | --- |
| 입력 | active 복약(코스+dose+`storage`·`med_form`·`indication_text`) · `health_safety` · `child` · 보호자 연락처 |
| 출력 | **고정 서식** 문자열 → `Readout(code, kind="med_notice_form" \| "emergency_card")` |
| 모델 | **부르지 않는다.** 서식은 템플릿, 특이사항은 보호자 입력 원문 그대로 |
| 고정 문구 | 투약의뢰서 끝에 "보호자 확인 후 서명해 주세요" 항상 |
| 저장 | 없음. 복사·공유만 |

### `summarize_checkup_context`
| 입력 | 다음 검진 차수 · 최근 식사 경향 · active 복약 · 최근 증상 에피소드 · `health_safety` · 마지막 측정값 |
| --- | --- |
| 출력 | `Readout(kind="checkup_context")` — 항목별 사실 나열 |
| 금지 | **발달 문항 답변·체크·점수** · 문진표 대필 · 수면 시간(기록 구조 없음) |
| 문구 | 문진 항목 설명은 `reference/health_phrases.yaml`에서. "문진표는 직접 작성해 주세요" 고정 |

### hook (Memory가 `observation_health` 저장 직후, pipeline이 실행)
| Tool | 조건 | 출력 |
| --- | --- | --- |
| `check_symptom_repetition` | 같은 `symptom` 14일 내 ≥3회 (설정값) | `Readout(code, kind="symptom_repeat")` + 병원 찾기 버튼 |

(알레르기 후보 감지 hook은 v1에서 뺐다.)

## 4. 출력 상수 (code readout)

| key | 문구 |
| --- | --- |
| `symptom.repeat` | 최근 2주 동안 '{symptom}'이(가) {n}번 기록됐어요. 병원에서 확인해 보시는 게 좋겠어요. |
| `vaccine.disclaimer` | 이 시기에 권장되는 접종이에요. 실제 접종 기록은 예방접종도우미에서 확인할 수 있어요. |
| `med.confirm` | {title} — {times}에 알려드릴까요? ({starts_on} ~ {ends_on}) |
| `med.update_confirm` | {title} 알림을 {times}로 바꿀까요? 확인 전까지는 기존 시각에 알려드려요. |
| `med.notice` | {title} — {times}에 알려드릴게요. ({starts_on} ~ {ends_on}) |
| `med.stop_confirm` | {title} 복약 알림을 멈출까요? 먹인 기록은 그대로 남아요. |
| `emergency` | 지금 바로 119에 연락하거나 가까운 응급실로 가세요. |
| `dose.status` | 마지막 복용 {last} · 다음 예정 {next} · 오늘 {taken}/{planned}회 |
| `dose.logged` | {title} 먹인 걸로 기록했어요. ({taken_at}) |
| `dose.duplicate` | 오늘 예정된 복용이 이미 모두 기록돼 있어요. 추가로 남길까요? |
| `fever.no_record` | 체온 기록이 없어요. 재실 때 남겨 두시면 정리해 드릴게요. |
| `notice.signature` | 보호자 확인 후 서명해 주세요. |
| `notice.no_active_med` | 지금 등록된 복약 일정이 없어요. |
| `prescription.low_confidence` | 흐리게 읽힌 칸이 있어요. 확인하고 채워 주세요. |
| `checkup.context_footer` | 문진표는 보호자가 직접 작성해 주세요. 이 요약은 참고용이에요. |
| `checkup.aged_out` | 영유아 건강검진과 국가예방접종은 만 6세까지예요. 지금은 대상 기간이 끝났어요. |
| `blocked.consent` | 건강 기록을 보려면 건강정보 동의가 필요해요. |
| `visit.no_record` | 최근 2주 동안 건강 기록이 없어요. |
| `place.unavailable` | 병원 정보를 불러오지 못했어요. 응급 시 119에 연락하세요. |
| `prescription.not_found` | 처방전 정보를 찾지 못했어요. |
| `safety.unavailable` | 알레르기 정보를 불러오지 못했어요. 아래 정리는 그것 없이 만든 거예요. |
| `dose.skipped` | {title} 못 먹인 걸로 기록했어요. ({slot}) |
