# 연령별 Tool 전략 — Food · Growth · Health

> 게이팅의 **정본**. 각 Agent의 Tool 명세는 이 문서를 참조한다.
> 관련: [`Tool_공통.md`](Tool_공통.md) · [`Food_Tool_명세.md`](../food/Food_Tool_명세.md) · [`Growth_Tool_명세.md`](../growth/Growth_Tool_명세.md) · [`Health_Tool_명세.md`](../health/Health_Tool_명세.md) · `activity/`(Activity 담당자 몫 — 아직 없음)

---

## 1. 원칙 다섯 가지

1. **연령은 발화가 아니라 `birth_date`에서 온다.** 보호자가 "우리 애 두 돌"이라고 말해도 게이팅은 `age_months`를 쓴다.
2. **가르는 방식은 도메인이 정한다.** 공통이 주는 것은 `life_stage()`의 월령까지다. `stage` 네 값은 참고값이고, 그걸 배타적 범주로 쓸지 `min_month` 눈금으로 쓸지 아예 tool을 안 여닫을지는 각 Agent가 자기 도메인을 보고 정한다. 아래는 지금까지 정한 세 가지다.
3. **범주에는 enum, 눈금에는 임계값.**
   - Food = **`LifeStage.stage` 범주.** 수유기와 유아식기는 "무엇을 먹을 수 있나"가 질적으로 다르고, `guide_weaning_stage`와 `lookup_daycare_menu`는 서로 배타적이다. (옛 이름 `FeedingStage`는 같은 축의 다른 이름이었다 — [`Tool_공통.md`](Tool_공통.md) §2에서 통일했다.)
   - Growth = **tool별 `min_month` 임계값.** 열리는 시점만 다른 같은 축의 눈금이라 배타적인 tool이 없다.
   - Health = **가르지 않는다.** 연령이 여는 tool이 없고 계산 방식만 바뀐다(§5). 검진 차수·접종 시기가 월령 함수일 뿐이다.
   - Activity = **미정.** 담당자가 정한다.
4. **닫힌 조합은 모델을 부르지 않는다.** `tools_for()`가 빈 튜플이면 코드 readout으로 끝난다(모델 호출 0회).
5. **경계값은 한 곳에만 있다.** `config/age_gates.yaml`. 코드·프롬프트·문서에 숫자를 복제하지 않는다.

```python
tools_for(task_type, gate: Gate) -> tuple[str, ...]
```

`Gate` · `LifeStage` 필드는 [`Tool_공통.md`](Tool_공통.md) §2·§3이 정본이다. 이 문서는 **어느 값에서 무엇이 열리고 닫히는지**만 정한다.

---

## 2. 전체 경계표

| 개월 | Food | Growth | Health | 공통 |
| --- | --- | --- | --- | --- |
| **0** | 수유기 — 추천 없음 | 리듬 안내 · 보드북 · 성장 추이 | 진료 요약 · 복약 · 검진/접종 · 병원 · 전달 서류 | affinity 없음 → 전부 일반 추천 |
| **4** | 이유기 시작 → `guide_weaning_stage` | – | – | |
| **12** | 유아식 → 급식·영양소 분석 전체 개방 / 꿀 금지 해제 | 교육 활동 · 자립·식사·전환 루틴 | – | |
| **18** | – | – | – | **`profile_affinity` 생성 시작** → 개인화 근거가 이때부터 존재 |
| **24** | – | 예절 연습 | – | |
| **36** | 섭취기준 1–2세 → **3–5세** | 습관 교정 · 누리과정 KB | – | 안전 기준(작은 부품) 전환 |
| **48** | 질식 주의 식품 해제(설정값) | – | – | |
| **72** | – | – | 영유아 검진·국가예방접종 대상 종료 → 안내 문구 전환 | v1 범위 상한 |

**18개월이 조용한 경계다.** Curator가 `profile_affinity`를 18개월부터 만들기 때문에 그 아래는 Food·Growth·Activity 모두 티어 1·2 근거가 **구조적으로 0**이다. tool은 열려 있는데 개인화가 안 되는 구간이라, 이 구간의 품질은 **일반 추천 템플릿과 문서 행(`*_doc`)의 품질**로 결정된다.

---

## 3. Food — `LifeStage.stage`

| stage | 개월 | `meal_recommendation` | `nutrient_analysis` |
| --- | --- | --- | --- |
| `infant_milk` | 0–3 | `()` → `unsupported.milk_meal` | `()` → `unsupported.infant_nutrient` |
| `infant_weaning` | 4–11 | `search_food_memory` · `guide_weaning_stage` · `propose_meal_candidates` (3) | `()` → `unsupported.infant_nutrient` |
| `toddler` · `preschool` | 12+ | + `analyze_meal_records` · `check_repeated_menus` · `lookup_daycare_menu` (5) | 7개 |

세 번째 라벨 `daycare_meal`(급식 기록 갱신·삭제)은 **단계가 아니라 데이터로 열린다** — `daycare_meal` 행이 있는 아이만. 기관에 다니면 이유기라도 급식을 먹는다.

`toddler`(12–35)와 `preschool`(36+)은 tool 묶음이 같다. 갈리는 것은 섭취기준 연령군(`1-2y` / `3-5y`)과 질식 주의 해제(48개월)뿐이다.

### 단계 안에서 다시 갈리는 것 (코드가 인자로 주입, 모델은 못 고른다)

| 무엇 | 4–5 | 6–8 | 9–11 | 12–23 | 24–35 | 36+ |
| --- | --- | --- | --- | --- | --- | --- |
| `guide_weaning_stage.texture` | `puree` | `puree`·`mashed` | `minced`·`soft_pieces` | – | – | – |
| 후보 풀 `stage_min` | 이유 초기 | 중기 | 후기 | 유아식 | 유아식 | 유아식 |
| 섭취기준 연령군 | 영아 0–5 · 6–11 (분석 미지원) | | | **1–2세** | **1–2세** | **3–5세** |
| `filter_food_safety` 연령 규칙 | 꿀 금지 · 생우유 금지 | | | 질식 주의 식품 | 질식 주의 식품 | 해제(48개월+) |
| 급식 조회 | 대상 아님 | | | 어린이집 영아반 | 어린이집 | 어린이집·유치원 |

- 이유기 시작(4개월)·질식 주의 해제(48개월)는 **설정값**이다. 소아청소년과학회 자료로 확정한다.
- 이유기는 affinity가 없으므로(§2) 후보 풀을 `food_doc`의 `weaning_*` 행에서 만든다. **이 구간에서 문서 행이 곧 추천 품질이다.**
- 12개월에 한꺼번에 5개 tool이 열리는 게 아니라, **급식 데이터가 있는 아이만** `lookup_daycare_menu`가 열린다(`daycare_menu` 0행이면 제외).

---

## 4. Growth — tool별 `min_month`

| tool | `min_month` | 닫혔을 때 |
| --- | --- | --- |
| `search_routine_memory` · `search_affinity` · `search_activity_memory` | 0 | – |
| `compute_growth_delta` (코드) | 0 | 측정 1건 이하일 때만 readout |
| `search_books` · `propose_books` | 0 | 도서 API 장애 시 readout |
| `propose_routine_plan` | 0 | – (모드가 연령으로 갈림) |
| `search_education_memory` · `lookup_notice` · `propose_learning_activity` | **12** | `closed.infant_learning` → Activity 안내 |

### 라벨 안에서 갈리는 것

| 무엇 | 0–11 | 12–23 | 24–35 | 36+ |
| --- | --- | --- | --- | --- |
| `propose_routine_plan.mode` | `rhythm_info` (안내만) | `next_step` | `next_step` | `next_step` · `habit_fix` |
| 허용 `routine_category` | – | `self_care` · `mealtime` · `transition` · `household_task` | + `social_manner` | + `habit` |
| `search_growth_doc` 교육 행 | – | 표준보육과정에서 쓴 행 | 표준보육과정에서 쓴 행 | 누리과정에서 쓴 행 |
| `search_books` 연령 필터 | 보드북·촉감책 | 그림책 | 그림책 | 그림책·지식책 |
| 근거 모드 | 일반 (affinity 없음) | 18개월부터 개인화 가능 | 개인화 | 개인화 |

- 습관 교정을 36개월로 잡은 이유는 그 전 행동을 문제로 규정하지 않기 위해서다. 36개월 미만 요청은 `closed.habit_under36` readout으로 간다.
- **성장폭에는 연령 축이 없다.** 최소 간격을 두지 않는다 — 간격이 짧으면 변화가 작게 나올 뿐이고, 그 작은 값도 사실이다. `compute_growth_delta`는 측정 로그 **전부**를 시간순으로 읽어 실제 수치를 그대로 보여준다. 막는 것은 측정이 1건 이하일 때뿐이다.
- `growth_doc` 행에도 `min_month`·`max_month`가 있어 **조회 단계에서 한 번 더 걸린다.** tool 게이트를 통과해도 그 월령의 행이 없으면 일반 템플릿으로 간다.

---

## 5. Health — tool별 `min_month` + 지표 전환

| tool | `min_month` | 비고 |
| --- | --- | --- |
| 진료 요약 tool 전체 · `medication` tool 전체 · `find_pediatric_places` | 0 | 연령 무관 |
| `compute_checkup_schedule` · `compute_vaccine_schedule` | 0 | 72개월 이상은 "대상 종료" 문구로 전환 |

연령이 여는 tool은 없고, **연령이 계산 방식을 바꾼다.**

| 무엇 | 0–23 | 24–35 | 36–71 | 72+ |
| --- | --- | --- | --- | --- |
| 검진 차수 | 1–4차 | 5차 | 6–8차 | 대상 종료 안내 |
| 접종 | 기초 접종 집중 | 추가 접종 | 만 4–6세 추가 접종 | 대상 종료 안내 |
| 복약 기준 시각 | `bedtime` 대신 `fixed` 19:30 (코드가 넣음) | 공통 상수 | 공통 상수 | 공통 상수 |

- 성장 백분위·BMI 판정은 제거됐다(2026-09-22). 24개월 누운키→선키 전환의 측정 차이(최대 0.7cm)는 Growth `growth_review`의 추이 서술에서 고려할 쟁점으로 남는다(H-5).
- `medication`의 기준 시각은 연령대별로 두는 순간 DB 생성 컬럼을 포기해야 한다. 영아 취침 시각 문제는 M-3으로 열려 있다.

---

## 6. 교정연령을 쓰는 곳 / 안 쓰는 곳

`gestational_weeks < 37`이면 24개월까지 `corrected_months`를 계산한다.

| 쓴다 (교정연령) | 안 쓴다 (출생 후 개월) |
| --- | --- |
| Food `LifeStage.stage` — 이유식 시작 | Health 예방접종 시기 (출생일 기준이 표준) |
| Growth 루틴·교육 tool 개방 | Health 영유아 검진 차수 (제도가 출생일 기준) |
| Growth `compute_growth_delta` 간격 | |

**안전 필터는 둘 중 작은 값을 쓴다.** 꿀·질식 주의·위험 용어 승격은 `min(age_months, corrected_months)` 기준이다. 안전에서는 더 어린 쪽이 보수적이다.

---

## 7. 경계 처리

- `age_months`는 **민법 기준 달력 계산**이다. `(today - birth).days // 30` 금지 — 6년이면 두 달 앞서 게이트가 열린다.
- 말일 경계는 민법 §160③(해당일이 없으면 그 달 말일). 1월 31일생은 평년 2월 28일에 1개월.
- `date` 자리에 `datetime`이 들어가지 못하게 타입으로 막는다. UTC 서버에서 `date.today()`를 부르면 KST 00:00–09:00에 하루가 어긋나 **그 시간대에만 게이트가 안 열린다.**
- 생일 당일 전환이다. 12개월이 되는 날 유아식 tool이 열린다.
- 구현은 `app/rules/age.py` 하나. Food·Growth·Health·Activity가 같은 함수를 쓴다.

---

## 8. 닫힘 → 무엇이 나가나

| 조합 | readout key | 모델 호출 |
| --- | --- | --- |
| Food 0–3 × 식단 추천 | `unsupported.milk_meal` | 0 |
| Food 0–11 × 영양소 분석 | `unsupported.infant_nutrient` | 0 |
| Growth 0–11 × 교육 활동 | `closed.infant_learning` | 0 |
| Growth 0–35 × 습관 교정 | `closed.habit_under36` | 0 |
| Growth 측정 1건 이하 | `delta.need_more` | 0 |
| Health 72개월+ × 검진·접종 | `checkup.aged_out` | 0 |
| `child_health` 동의 없음 | Food 전 라벨 · Health 전 라벨 · Growth는 `growth_review`만 → `blocked.consent` · `closed.consent` | 0 |

연령 때문에 닫힌 경우에는 **언제 열리는지 함께 알린다.** "돌이 지나면 영양 균형도 봐드릴게요."

---

## 9. 테스트 — 경계마다 양쪽

필수 월령: **3/4 · 11/12 · 17/18 · 23/24 · 35/36 · 47/48 · 71/72**

| 케이스 | 기대 |
| --- | --- |
| 3개월 식단 추천 | tool `()`, 모델 0회, 수유기 문구 |
| 4개월 식단 추천 | tool 3개, `texture=puree` |
| 11개월 / 12개월 영양소 분석 | 각각 0회 안내 / tool 7개 + 섭취기준 1–2세 |
| 17개월 / 18개월 | 둘 다 추천은 나가고, 17은 `general` · 18은 affinity 있으면 `personalized` |
| 35개월 / 36개월 | 섭취기준 1–2 → 3–5, 표준보육과정 → 누리과정 |
| 35개월 / 36개월 습관 교정 | 안내 readout / 교정안 (단, `trigger` 필요) |
| 조산 34주, 생후 6개월 | 교정 4개월 → 이유 초기. 접종 일정은 생후 6개월 기준 |
| 조산아 안전 필터 | 더 어린 쪽(교정) 기준으로 차단 |
| 생일 당일 00:05 (KST) | 게이트가 열림 — UTC 날짜 버그 회귀 |

---

## 10. 미결

| # | 내용 |
| --- | --- |
| A-1 | 이유기 시작 4개월 · 질식 주의 해제 48개월 — 소아청소년과학회 자료로 확정 |
| A-3 | Curator의 affinity 생성 최소 월령이 정말 18개월인지 (Activity 문서 기준) · 도메인별로 다른지 |
| A-4 | 예절 24개월 · 습관 36개월 경계 근거 |
| A-5 | ✅ 닫힘 — 영아는 `resolve_dose_timing`이 `bedtime` 대신 **`fixed` 19:30**을 돌려준다. 상수를 2벌로 두지 않으므로 `scheduled_time` 생성 컬럼을 유지한다 |
| A-6 | ✅ 닫힘 — **아이 등록에 상한을 걸지 않는다.** 검진·접종만 "대상 종료"로 안내하고(`checkup.aged_out`) 식단·놀이·도서는 그대로 동작한다 |
