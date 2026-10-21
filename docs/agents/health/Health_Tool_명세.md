# Health Agent Tool 명세

> 기준: [`Health_Agent_명세.md`](Health_Agent_명세.md) · [`health_agent_own_table.md`](health_agent_own_table.md) · [`Tool_공통.md`](../shared/Tool_공통.md)
>
> **2026-10-20 갱신** — 복용 기록 tool(`log_dose_taken` · `log_dose_skipped`)과 출력 상수(`dose.logged` · `dose.skipped` · `dose.duplicate`) 삭제 — 복용 체크는 보호자가 화면에서 하고(`medication_dose.checked_at`), Agent 는 읽기만 · "먹였어" · "못 먹였어" 는 쓰기 0 · 모델 0 · 화면 안내
>
> **2026-10-18 갱신** — 복약 중단은 **한 번 묻고**(`med.stop_confirm`) '네' 면 `status='stopped'`, 중단 시각은 `updated_at` · 증상 반복은 최근 7일 안 **기록된 날 3일** · 만성질환 × 증상 안내(`lookup_condition_guidance`) · 검진 맥락 요약에서 측정값 제외 · 응급 카드에서 연락처 제외
>
> **2026-09-22 갱신** — `growth_check`·`assess_growth_percentile`·성장도표 상수 제거 · 복약 생성·수정은 **초안 payload**(DB 저장 없음) · 중단 soft delete

## 0. 외부 데이터 소스

**원칙**: 기준표는 **버전 붙은 상수 파일**, 실시간 정보만 API. 출처 문서를 모델이 읽고 답하는 경로(RAG)는 없다.

| 소스 | 제공 | 용도 | 연결 | 갱신 |
| --- | --- | --- | --- | --- |
| 국민건강보험공단 **영유아 건강검진** — nhis.or.kr | 일반 8회 · 구강 4회 주기 | `compute_checkup_schedule` | `reference/checkup_nhis.yaml` | 제도 변경 시 |
| 질병관리청 고시 「예방접종의 실시기준 및 방법」 **[별표 1] 국가예방접종 대상 및 표준접종시기** | 백신·차수·표준접종 월령 | `compute_vaccine_schedule` | `reference/vaccine_2026.yaml` | **고시 개정 시** |
| 예방접종도우미 — nip.kdca.go.kr | 개인 접종 기록 | 안내 링크만 | 연동 없음 | – |
| 국립중앙의료원 **전국 병·의원 찾기 서비스** (data.go.kr/15000736) | 병원 목록 · 진료과목 · 좌표 · 요일별 진료시간 | `find_pediatric_places` (주간 · 야간 소아청소년과) | REST · 시도별 24h 캐시 | – |
| 국립중앙의료원 **전국 응급의료기관 정보 조회 서비스** (data.go.kr/15000563) | 응급의료기관 목록 · 좌표 (실시간 병상은 아직 안 씀) | `find_pediatric_places` (야간·응급) | REST · 시도별 10분 캐시 | 실시간 |
| ~~달빛어린이병원 지정 목록~~ | – | **쓰지 않음** — 병·의원 진료시간으로 "지금 진료 중" 을 골라 달빛어린이병원도 잡힌다 (10-18) | – | – |
| 식약처 알레르기 표시 대상 19종 | 코드표 | `health_safety` 라벨 정규화 | Food `ALLERGEN_CODES` 공유 | 고시 개정 시 |
| 국가건강정보포털 health.kdca.go.kr · 대한소아청소년과학회 | 응급 신호 · 증상 문구 | Supervisor 규칙 · 고정 문구 **검수 근거** | 문서 | 검수 시 |
| 공공 출처 (**공공누리 0·1 · 고시 · CC BY만**) | 만성질환별 관리 안내 | `lookup_condition_guidance` | `reference/condition_codes.yaml` · `reference/condition_guidance.yaml` | 검수 시 (**원문 대조 + 연결 적절성 확인** — 의료인 검수는 필수 아님, 10-19) |
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
| `medication` | search_medication_schedules · create/update/stop_medication_schedule | resolve_dose_timing · normalize_symptom_term · get_next_dose · lookup_prescription_draft | 1 (조회 · "먹였어" 안내는 **0**) |
| **`care_handoff`** | – | search_medication_schedules · load_health_safety · compose_daycare_med_notice · build_emergency_card | **0** |
| (hook) | – | check_symptom_repetition · lookup_condition_guidance | 0 |

닫힘: 동의 없음 → 전 라벨 · 위치 없음 → `place_lookup` · `health_safety` 실패 → `visit_summary` 진행 + 경고 줄 · 진행 중 복약 0건 → 투약의뢰서 · 체온 기록 0건 → `build_fever_timeline`.

### 연령 — tool을 여닫지 않고 계산 방식을 바꾼다

> 정본은 [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §5.

| | 0–23 | 24–35 | 36–71 | 72+ |
| --- | --- | --- | --- | --- |
| 검진·접종 | 1–4차 · 기초 접종 | 5차 | 6–8차 · 만 4–6세 추가 | 검진 종료 안내 · 접종은 만 4–6세 · 6세 · 11–12세 안내 |
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
| `create_medication_schedule` | `title_span?`, `dosage_span?`, `timing_spans[]`, `period_span?`, `indication_span?` | **초안 payload**(`op="create"`) — 코스 + dose N · **`notice_times[]` 필수**(확인 모달용) · `missing[]` · `schedule.symptom_codes`(`indication_span` 을 증상 사전으로 맞춘 코드 — 코드가 정하고 모델은 고르지 않는다) | **DB를 건드리지 않는다.** span은 발화 원문 부분 문자열이어야 함(코드 검증) · 시각은 `resolve_dose_timing` 결과만 |
| `update_medication_schedule` | `title_span`(바꿀 약), `dosage_span?`, `timing_spans?[]`, `period_span?` — **`schedule_id` 는 모델에게 받지 않는다**(코드가 `title_span` 으로 진행 중 코스에서 찾는다) | **초안 payload**(`op="update"`, `schedule_id`, `before`) · 변경 후 `notice_times[]`. 값은 일정처럼 **전체 값**(지금 값에 바뀐 값을 얹은 것)이고 바뀐 부분만 따로 싣지 않는다 — 화면이 `before` 와 비교한다. tool 결과(모델)에는 바뀐 필드 이름 `changed[]` 가 실리고 payload 에는 없다 | 동일. 같은 약을 한 run 에서 또 고치면 앞 초안 위에 쌓여 **한 장**으로 나가고(같은 `draft_id`) 확인 문구도 마지막 것 하나만 남는다 — 일정의 `DraftBook` 과 같다. 지금 값과 같으면(처음 값으로 되돌려도) 초안을 만들지 않고 **성공으로** 끝낸다(`draft=false` · `changed=[]` · 안내 `med.no_change`) — 출력 tool 이 실패를 돌려주면 모델이 같은 호출을 되풀이한다. 이 약의 카드가 이미 나가 있으면 안내 없이 그 카드가 답이다. 되묻기를 이어받는 run 은 실패 코드 `NO_CHANGE` 로 같은 안내를 고른다. 같은 약의 카드와 되묻기(시점 · 중단 확인)는 한 run 에 같이 내지 않는다(`med.one_at_a_time`) — 질문 뒤 다음 run 이 새 카드를 또 만들어 한 약의 카드가 둘이 된다. 진행 중인 복약이 없으면 `notice.no_active_med` 로 성공 종료한다(실패로 돌리면 모델이 되풀이한다 — stop 과 같다). 말한 기간을 못 읽으면("다음 주 월요일까지") 지금 값 그대로 두고 `med.period_unclear` 를 붙인다 — `med.no_change` 로 뭉개지 않는다. 대상 회차를 anchor 로 못 찾으면 그 시각 둘레(±150분)의 **`fixed` 회차만** 같은 회차로 본다 — 저녁(19:00)과 자기 전(20:30)은 90분 차이라, 기준점이 있는 회차까지 가깝다고 옮기면 말하지 않은 회차가 사라진다. 기존 코스는 제출 전까지 그대로. 제출 시 같은 행 UPDATE + 아직 오지 않은 회차 행만 지우고 다시 만들기 — 지난 회차와 그 체크는 그대로 남는다 |
| `stop_medication_schedule` | `title_span?` — 약 이름을 말하지 않았으면 비운다(약이 하나면 그 약, 둘 이상이면 되묻는다) | 묻는 run: `med.stop_confirm` 되묻기 + 되묻기 맥락(대상 `schedule_id` · 약 이름) / '네' 다음 run: `status='stopped'` · `updated_at`=중단 시각 · `med.stopped` / '아니요' · 애매함: `med.stop_kept` | 확인 모달 · 초안 없음(10-18). **묻는 run 에서는 DB 를 건드리지 않는다.** soft delete — 회차 행·체크 보존. 대상 약 2개 이상 후보면 먼저 어느 약인지 `needs_observation` — 엉뚱한 약의 알림이 끊기므로 모호하면 되묻기 |

**복약 생성·수정은 DB에 쓰지 않습니다.** Agent가 직접 쓰는 것은 한 번 물어 보호자가 확인한 중단뿐입니다. 생성·수정 두 tool의 산출물은 `medication_drafts` 채널로 나가는 JSON 초안이고, 보호자가 확인 모달에서 제출해야 백엔드가 씁니다. `missing[]`(비어 있는 NOT NULL 칸 — `title` · `starts_on` · `ends_on`)이 비지 않으면 화면이 제출을 막습니다. Memory의 `event` 초안과 같은 방식이고, 만들자마자 행을 쓰는 `suggestion`과는 다릅니다.

**복용 체크는 tool이 아닙니다.** 보호자가 복약 화면에서 회차(`medication_dose` 행)를 체크하면 그 행의 `checked_at` 이 찹니다. "먹였어" · "못 먹였어" 에는 아무것도 쓰지 않고 `dose.check_on_screen` 안내만 냅니다(모델 0회). 체크가 없는 회차는 "안 먹였다"가 아니라 "체크 안 됨"입니다.

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
| 입력 | 흐린 좌표(`CoarseLocation`, 약 1km · 비저장), `now`(KST), `mode: "normal"\|"emergency"` |
| --- | --- |
| 위치 | 🚨 **보호자 좌표를 병원 API 에 보내지 않는다** — 좌표 → 5km 격자만 어댑터(`FacilitySource`)에 넘기고, 어댑터가 시도로 바꿔 조회한다. 거리는 받은 기관 좌표로 서버 안에서 계산한다 (Activity 와 같은 원칙 · activity-agent-v1 D9 · #166 위치 약관) |
| 분기 | 평일 09–18시 → 소아청소년과 · 그 밖(토 · 일 · 공휴일 · 야간) → **진료시간상 지금 진료 중인 소아청소년과**(달빛어린이병원 포함) → 응급의료기관 · emergency → 응급의료기관만. 종류 순서가 거리보다 먼저다. 달빛어린이병원은 API 에 표시 칸이 없어(자유 글에만 있음) 지정 목록 대신 진료시간(`dutyTime1~8`)으로 고른다 (10-18) |
| 출력 | 종류 순 · 같은 종류는 거리순 ≤5 `{name, tier, distance_m, phone, open_now}` → `Readout(kind="place_list")`. 문을 닫은 게 확실한 곳(`open_now=False`)은 뺀다 |
| 실패 | 조회 실패 → "병원 정보를 불러오지 못했어요. 응급 시 119에 연락하세요." · 0곳은 실패와 따로 → "근처에서 지금 찾을 수 있는 병원이 없어요. …" · 위치 없음 → "위치를 허용하시면 가까운 병원을 찾아드려요." |
| 코드 | `app/agents/health/places/` — 시간대 분기 · 지금 진료 중 판정 · 거리순 · readout. 어댑터는 `app/integrations/hospital/`(`NmcHospitals`) — 국립중앙의료원 병·의원 찾기(`QD=D002` 소아청소년과) · 응급의료기관 목록을 **시도 이름**으로 묻는다(이 API 는 시군구를 이름으로만 받는다). 소아청소년과는 시도마다 24시간, 응급실은 10분 기억한다. 원문 계약은 `core/hospital_raw.py` |

### `resolve_dose_timing`
| 입력 | `timing_span` (발화 원문) |
| --- | --- |
| 출력 | `[(timing_anchor, offset_minutes, fixed_time?)]` \| `Unresolved` |
| 사전 | 식후 +30 · 먹고 바로 0 · 식전 −30 · 자기 전 bedtime · 일어나서 wake_up · "N시" fixed · "하루 세 번 식후" → 3행 |
| 미해결 | `needs_observation`: "식사 전후 언제 먹이면 되나요?" — 저장 안 함. 식전 · 식후를 둘 다 말했거나 따로 온 말이 서로 다르면 하나를 고르지 않고 이 질문으로 되묻는다. "자기 전 30분" · "일어나서 30분 뒤" 처럼 오프셋을 못 받는 기준점에 분을 붙인 말은 풀지 않고 몇 시인지 되묻는다(분이 **바로 붙은** 경우만 — "아침 식후 30분이랑 자기 전에" 는 푼다). 식사 오프셋만 있고 끼니가 없으면("식사 30분 전, 자기 전") 풀지 않는다. 엇갈린 말("아침은 식전 저녁은 식후")에 "식후요" 처럼 답하면 원래 말의 식전 · 식후를 걷어 내고 답의 방향으로 푼다 |

### `normalize_symptom_term` · `group_symptom_episodes`
| | 내용 |
| --- | --- |
| `normalize_symptom_term` | "열나" · "미열" · "38도" → `fever` 표준 코드. 사전은 `reference/symptom_codes.yaml`, `health_phrases.yaml`의 `seek_care` 키와 **같은 어휘** |
| `group_symptom_episodes` | 같은 코드가 **48시간 이내**로 이어지면 한 에피소드 → `{code, from, to, days, peak_severity?, actions[]}` |
| 왜 코드인가 | 증상 반복과 타임라인이 모두 이 어휘 위에 선다. 반복은 **기록된 날 수**로 세고(같은 날 = 1일), 에피소드는 타임라인 표시에만 쓴다(10-18) |
| 금지 | 원인 추정 · 중증도 판정 · "심해지고 있어요" |

### `build_fever_timeline`
| 입력 | `observation_health`(`temperature`·`measured_at`·`measure_site`) 24–48시간 · `medication_dose`(체크된 회차) · `medication_schedule` |
| --- | --- |
| 출력 | 시간순 나열 + 최고·최저 체온과 시각 + 마지막 복약(약 이름 **원문**) + **등록된 일정상의** 다음 예정 시각 → `Readout(code, kind="fever_timeline")` |
| 금지 | 약 성분·계열 판정(아세트아미노펜/이부프로펜) · 계열 최소 간격으로 계산한 "투약 가능 시각" · 경과 해석 |
| 선행 | `observation_health` 체온 세 칸 (공유 테이블 변경 요청) |
| 부위 | 값 옆에 **잰 부위를 함께 보여준다.** 부위마다 정상 범위가 달라 숫자만 나열하면 들쭉날쭉해 보인다. 부위별 기준값은 **코드 상수**다 — 모델이 정상 범위를 지어내지 않는다. `measure_site` 가 비면 부위 비교 없이 숫자만 낸다 |
| 우선순위 | 응급 신호(3개월 미만 발열 등)에 걸리면 타임라인보다 병원 안내가 먼저 |

### `get_next_dose`
| 입력 | `schedule_id?`(없으면 active 전부), `now` |
| --- | --- |
| 출력 | `{last_checked, next_scheduled, checked_today, planned_today}` → `Readout(code, kind="dose_status")` — `last_checked` 는 체크된 회차 중 가장 늦은 것의 **회차 예정 시각**(`dose_date` + `scheduled_time`)이다. 체크를 누른 시각(`checked_at`)은 몰아서 누르면 실제와 어긋난다 |
| 금지 | **`can_take` 같은 필드를 두지 않는다.** 허가·간격 판단 없음 · 체크 안 된 회차를 "안 먹였다"로 쓰지 않는다 |

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
| 입력 | active 복약(코스+dose+`storage`·`med_form`·`indication_text`) · `health_safety` · `child`. 보호자 연락처는 넣지 않는다(10-18) |
| 출력 | **고정 서식** 문자열 → `Readout(code, kind="med_notice_form" \| "emergency_card")` |
| 모델 | **부르지 않는다.** 서식은 템플릿, 특이사항은 보호자 입력 원문 그대로 |
| 고정 문구 | 투약의뢰서 끝에 "보호자 확인 후 서명해 주세요" 항상 |
| 저장 | 없음. 복사·공유만 |

### `summarize_checkup_context`
| 입력 | 다음 검진 차수 · 최근 식사 경향 · active 복약 · 최근 증상 에피소드 · `health_safety` |
| --- | --- |
| 출력 | `Readout(kind="checkup_context")` — 항목별 사실 나열 |
| 금지 | **발달 문항 답변·체크·점수** · 문진표 대필 · 수면 시간(기록 구조 없음) · 키·몸무게(`child_growth_log`를 읽지 않는다, 10-18) |
| 문구 | 문진 항목 설명은 `reference/health_phrases.yaml`에서. "문진표는 직접 작성해 주세요" 고정 |

### hook (Memory가 `observation_health` 저장 직후, pipeline이 실행)
| Tool | 조건 | 출력 |
| --- | --- | --- |
| `check_symptom_repetition` | 같은 증상 코드가 **최근 7일 안 3일 이상** 기록 (설정값, 10-18). 단위는 날 — 같은 날 여러 번은 1일, 사흘 내리면 3일. 에피소드로 세지 않는다 | `Readout(code, kind="symptom_repeat")` + 병원 찾기 버튼. 질환 연결이 서면 `symptom.repeat_condition`, 아니면 `symptom.repeat` |

세는 기준: 아이 타임존의 달력 날짜 · `observed_time`이 있으면 그 날, 없으면 `observed_range`의 날들(여러 날이면 7일 창 안의 날을 다 센다) · 정정으로 내린 기록(`wrong`)은 세지 않음 · 증상은 `normalize_symptom_term` 표준 코드로 묶음.

(알레르기 후보 감지 hook은 v1에서 뺐다.)

### `lookup_condition_guidance` (10-18)

반복 판정이 섰을 때 `check_symptom_repetition`이 부른다. 모델은 이 tool을 보지 못한다.

| | 내용 |
| --- | --- |
| 입력 | 증상 코드 · `health_safety` active 중 `chronic_disease` · `other_medical` 항목 |
| 사전 | `reference/condition_codes.yaml` — 표준 질환 코드 + 별칭 + guards. 보호자가 적은 `label`을 공통 매처(`app/rules/term_match.py`)로 맞춘다 |
| 안내 | `reference/condition_guidance.yaml` — **(질환 코드 × 증상 코드) 키 정확 일치.** 행 = 고정 문구 · 출처(제목 · 기관 · URL · 이용 조건) · `medical_reviewed` · `reviewed_by` |
| 출력 | 맞는 행마다 `Readout(code, kind="condition_guidance", source_refs=출처)` — 문구 글자 그대로. 반복 문장은 `symptom.repeat_condition`이고 `{condition}`은 등록된 `label` 원문 |
| 여럿이 맞으면 | 등록 순으로 전부. 문장의 `{condition}`은 `'A' · 'B'`로 잇는다 |
| 없으면 | 사전에 없는 이름 · 행 없음 · `health_safety` 조회 실패 → 질환 문장도 카드도 없이 `symptom.repeat`만. 조회 실패를 "등록 질환 없음"으로 말하지 않는다 |
| 쓰기 | **없음.** `health_safety.management`에 쓰지 않고 승인도 받지 않는다 — 보호자가 설정 화면에서 직접 고친다 |
| 검수 (10-19) | 이 안내는 **우리가 문장을 쓰지 않고 공공 출처 문장을 원문 그대로 연결**한다. 그래서 검수는 의료 판단이 아니라 두 가지다 — ① 문구가 출처 원문과 글자 단위로 같은가 ② 이 질환 × 증상에 이 문장을 붙이는 게 출처의 맥락에 맞는가(오래된 자료 · 시기가 한정된 자료를 연중 보여 주지 않는가). 작성자가 해도 되고 의료인이어야 하는 건 아니다. `medical_reviewed: true` 는 이 확인을 마쳤다는 뜻이며(필드 이름은 `seek_care` 와 맞춰 유지), 확인한 사람의 GitHub 아이디를 `reviewed_by` 에 적는다. **응급 신호 · `seek_care` 문구는 이 완화의 대상이 아니다 — 그쪽은 의료 검수 필수.** ⚠️ 정책 변경이라 PM 확인 대기 |
| 부팅 검사 | `medical_reviewed: true`가 아닌 행은 로드하지 않는다 · `true` 인데 `reviewed_by` 가 비면 거절한다 · 출처 이용 조건은 공공누리 0·1 · 고시 · CC BY만 · 증상 키는 `symptom_codes.yaml`에 있어야 한다 · 해석 금지어 lint(문구만, `{condition}` 자리의 보호자 원문은 대상 아님) |
| 금지 | 알레르기를 질환 문장에 붙이기 · 원인·질환 추정("~로 인해", "~에 걸린") · 모델이 출처를 읽고 문장 만들기 · 증상에서 질환 찾기 |

## 4. 출력 상수 (code readout)

| key | 문구 |
| --- | --- |
| `symptom.repeat` | 최근 1주 동안 '{symptom}'이(가) {n}번 기록됐어요. 병원에서 확인해 보시는 게 좋겠어요. |
| `symptom.repeat_condition` | 최근 1주 동안 '{symptom}'이(가) {n}번 기록됐어요. 등록하신 '{condition}'이(가) 있어 병원에서 확인해 보시는 게 좋겠어요. |
| `vaccine.disclaimer` | 이 시기에 권장되는 접종이에요. 실제 접종 기록은 예방접종도우미에서 확인할 수 있어요. |
| `med.confirm` | {title} — {times}에 알려드릴까요? ({starts_on} ~ {ends_on}) |
| `med.update_confirm` | {title} 알림을 {times}로 바꿀까요? 확인 전까지는 기존 시각에 알려드려요. |
| `med.notice` | {title} — {times}에 알려드릴게요. ({starts_on} ~ {ends_on}) |
| `med.stop_confirm` | {title} 복약 알림을 멈출까요? 먹인 기록은 그대로 남아요. |
| `med.stopped` | {title} 복약 알림을 멈췄어요. 먹인 기록은 그대로 남아요. |
| `med.stop_kept` | {title} 복약 알림은 그대로 둘게요. |
| `ask.dose_timing` | 식사 전후 언제 먹이면 되나요? |
| `ask.which_med` | 어느 약 말씀이세요? {titles} |
| `ask.dose_clock` | 몇 시인지 오전 · 오후까지 숫자로 말씀해 주세요. 예: 저녁 8시 |
| `med.which_unclear` | 어느 약인지 알아듣지 못했어요. 약 이름을 넣어 다시 말씀해 주세요. |
| `med.timing_unclear` | 언제 먹일지 알아듣지 못해서 등록하지 않았어요. 시간을 넣어 다시 말씀해 주세요. |
| `med.no_change` | 바꿀 내용이 없어서 수정 카드를 만들지 않았어요. |
| `med.period_unclear` | 기간을 알아듣지 못해서 바꾸지 않았어요. 며칠인지(예: 5일) 말씀해 주세요. |
| `med.one_at_a_time` | 약은 한 번에 하나씩 확인해요. 나머지는 이어서 말씀해 주세요. |
| `emergency` | 지금 바로 119에 연락하거나 가까운 응급실로 가세요. |
| `dose.status` | 마지막 체크 {last} 회차 · 다음 예정 {next} · 오늘 체크 {checked}/{planned}회 |
| `dose.check_on_screen` | 복용 체크는 복약 화면에서 해 주세요. |
| `fever.no_record` | 체온 기록이 없어요. 재실 때 남겨 두시면 정리해 드릴게요. |
| `notice.signature` | 보호자 확인 후 서명해 주세요. |
| `notice.header` | [투약 의뢰서]  {date} |
| `notice.line_title` | • 약 종류 : {title} ({dosage}) |
| `notice.line_time` | • 투약 시각 : {times} |
| `notice.line_storage` | • 보관 : {storage} |
| `notice.line_indication` | • 복용 이유 : {indication} |
| `notice.line_note` | • 특이사항 : {note} |
| `card.header` | [응급 카드]  {date} |
| `card.child` | • 아이 : {nickname} ({birth}생, {age}) |
| `card.allergy` | • 알레르기 : {items} |
| `card.chronic` | • 만성질환 : {items} |
| `card.meds` | • 복용 중인 약 : {items} |
| `card.empty` | 등록된 내용이 없어요 |
| `card.safety_unavailable` | • 알레르기 · 만성질환 : 불러오지 못했어요. 직접 확인해 주세요. |
| `notice.no_active_med` | 지금 등록된 복약 일정이 없어요. |
| `prescription.low_confidence` | 흐리게 읽힌 칸이 있어요. 확인하고 채워 주세요. |
| `checkup.context_footer` | 문진표는 보호자가 직접 작성해 주세요. 이 요약은 참고용이에요. |
| `checkup.aged_out` | 영유아 건강검진은 만 6세 미만까지예요. 지금은 대상 기간이 끝났어요. |
| `blocked.consent` | 건강 기록을 보려면 건강정보 동의가 필요해요. |
| `visit.no_record` | 최근 2주 동안 건강 기록이 없어요. |
| `place.unavailable` | 병원 정보를 불러오지 못했어요. 응급 시 119에 연락하세요. |
| `prescription.not_found` | 처방전 정보를 찾지 못했어요. |
| `safety.unavailable` | 알레르기 정보를 불러오지 못했어요. 아래 정리는 그것 없이 만든 거예요. |
| `checkup.current` | 지금은 {round}차 영유아 건강검진 기간이에요. ({starts_on} ~ {ends_on}) |
| `checkup.next` | 다음 {round}차 영유아 건강검진은 {starts_on}부터예요. |
| `checkup.oral` | 같은 시기에 구강검진도 받을 수 있어요. ({starts_on} ~ {ends_on}) |
| `vaccine.due` | 이 시기에 권장되는 접종: {vaccines} |
| `vaccine.none_now` | 월령으로 정해진 접종은 지금 없어요. 인플루엔자 같은 절기 접종은 예방접종도우미에서 확인해 보세요. |
| `checkup.oral_only` | 지금은 구강검진 {round}차 기간이에요. ({starts_on} ~ {ends_on}) |
| `vaccine.next` | 다음 접종은 {starts_on}부터예요: {vaccines} |
