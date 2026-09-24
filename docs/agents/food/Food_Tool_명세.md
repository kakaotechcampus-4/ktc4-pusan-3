# Food Agent Tool 명세

> 기준: `food.md`(저장소 밖) · [`Food_Agent_명세.md`](Food_Agent_명세.md) · [`Tool_공통.md`](../shared/Tool_공통.md)
>
> **2026-09-22 갱신** — 식사 후보 3개 · 필터 후 3개 미만 시 재샘플링 + 재호출 1회 · 급식 대체·제외 메뉴 반영 · 권장 열량에 키·몸무게 반영(성별 미사용)

---

## 0. 메뉴 API 연결 — 먼저 읽을 것

### 0-1. 문제: 보호자는 "메뉴"로 말하고, API는 "식품"으로 답한다

| 들어오는 것 | 예 | 출처 |
| --- | --- | --- |
| 섭취 기록 | "카레라이스 반 그릇" | `observation_food.subject` · `amount` |
| 급식 | "현미밥 / 미역국 / 닭갈비(5.6.15.)" | `daycare_meal.menu_keys` (OCR 주경로 · NEIS) + 보호자가 알려준 대체·제외 메뉴 |
| 추천 후보 | "두부달걀덮밥" | `propose_meal_candidates` |

세 경로 모두 **메뉴명 문자열**이다. 영양·알레르기 계산에는 **식품코드 · 재료 · 영양성분**이 필요하다. 이 변환을 모델에게 맡기면 "크림수프에서 우유 누락"(F-1), "단백질 12g" 같은 수치 생성이 생긴다.

→ **메뉴명 → 메뉴 카탈로그 행**으로 바꾸는 일은 전부 코드(`resolve_menu`)가 한다. 모델은 카탈로그에 있는 메뉴만 다룬다.

### 0-2. 연결 구조

```
메뉴명 ──normalize──▶ menu_key ──▶ menu_catalog (로컬 캐시)
                                     │ 미스
                                     ▼
          ① 식약처 식품영양성분DB정보 (음식 분류) ── 1인분 영양
          ② 식약처 조리식품 레시피 DB (COOKRCP01) ── 재료 목록 · 영양
          ③ 둘 다 없음 → status="unresolved" (모델 추정 금지)
                                     │
                                     ▼
          재료 → 알레르기 19종 코드 매핑 (코드 사전)
          식품분류 → 6개 식품군 매핑 (코드 사전)
```

### 0-3. `menu_catalog` (캐시 테이블)

| 필드 | 설명 |
| --- | --- |
| `menu_key` | 정규화 메뉴명 (공백·괄호·알레르기 번호 제거, 동의어 치환) · PK |
| `food_code` | 식약처 식품코드 (없으면 NULL) |
| `source` | `mfds_nutri` · `mfds_recipe` · `manual` |
| `serving_g` | 1인분 중량 — **성인 기준** |
| `nutrients` | jsonb — 에너지·탄수화물·단백질·지방·당류·나트륨·칼슘·철 … (100g 기준 + 1인분) |
| `ingredients` | text[] — 레시피 DB 재료 |
| `allergen_codes` | smallint[] — 19종 코드 (재료 + 메뉴명 사전 **합집합**) |
| `food_groups` | text[] — 곡류 · 고기생선달걀콩 · 채소 · 과일 · 우유유제품 · 유지당류 |
| `stage_min` | 이 메뉴가 가능한 최소 단계 — `LifeStage.stage` 네 값 중 하나 (수동 태깅) |
| `resolved` | bool |
| `source_version` · `synced_at` | 데이터 기준일 |

- 동기화: 야간 배치 + 캐시 미스 시 즉시 1회 호출. 운영 트래픽을 API에 직접 걸지 않는다.
- `unresolved` 메뉴는 **영양 계산에서 빠지고 기록 행 수에서 빠진다**된다. 알레르기 판정은 "확인 못 함".

### 0-4. 수치는 내부, 출력은 구간 — 계산 설계

상세: [`영양소_계산_설계.md`](영양소_계산_설계.md)

1인분 기준이 **성인**이고 먹은 양은 "반 그릇" 같은 텍스트다. 그래서 **수치는 내부 계산에만 쓰고, 화면에는 구간과 식품군으로 나간다.**

| 계산 | 양·결측에 민감? | 내부 | 출력 |
| --- | --- | --- | --- |
| 에너지 구성 비율 (탄:단:지) vs AMDR | 낮음 | ✓ | ✓ 구간 |
| 식품군 출현일 (7일 중 채소 나온 날) | 낮음 | ✓ | ✓ 그대로 |
| 나트륨·당류·포화지방 vs UL | 중간 | ✓ | ✓ 구간 |
| EAR·RNI 대비 충족률 | **높음** | ✓ 판정 근거 | **✗ 숫자 노출 금지** |
| 하루 섭취 g · kcal | 높음 | ✗ | ✗ |

- **판정 기간**: 오늘(기록된 끼니) = 남은 끼니 구성용 · **최근 3~7일 = 부족·과잉 판정** · 30일 = 반복·다양성
- **끼니를 정의하지 않는다.** 기록이 충분한지는 기간 안의 행 수로 본다 — 7일에 `intake_daily` + `daycare_meal` 합쳐 10행 미만이면 수치 기반 결론 없음(설정값)
- **급식은 "양 미상"** 등급으로 따로 센다. 메뉴는 알지만 먹은 양을 모른다
- `amount` 가중치: `다 먹음 1.0 · 반 0.5 · 조금 0.25 · 뱉음/안 먹음 0 · 미상 → 1.0 + "양 미상"`
- 기준표: `nutrient_reference` (EAR · RNI · AI · UL · AMDR, 연령군 `1-2` / `3-5`). **성별 구분 없는 행만** 쓴다
- **권장 열량**: 연령군 일반값을 `child_growth_log`의 최근 키·몸무게로 조정한다(`compute_energy_target`, 코드). 측정이 없으면 연령군 일반값. 성별은 넣지 않는다. 결과 kcal은 내부 전용

### 0-5. 식단 추천에서의 연결 — 후보 풀은 코드가 만든다

```
menu_catalog
  ∩ stage_min ≤ 아이 단계
  − 알레르기 코드 교집합 ≠ ∅          (filter_food_safety)
  − 연령 금지 재료 (12개월 미만 꿀 …)
  − 최근 3일 반복 메뉴
  − 오늘 급식과 같은 주재료 (선택)
  → 후보 풀 (최대 30)
        │  sample_candidates: 점수 가중 샘플링 + 식품군 다양성 제약 (seed=run_id)
        │  가중치: 부족 식품군 ↑ (영양 우선) · 선호 ↑ · 기피 ↓ (0이 되지는 않는다)
        ▼
  모델에게 보여줄 후보 10~15  ──▶ 모델이 3개 선택 + 이유 작성
                                         │ 사후 filter_food_safety 후 3개 미만
                                         ▼
  걸러진 menu_key를 풀에서 빼고 재샘플링 → 모델 재호출 1회 (이 경우에만)
```

- **기피는 풀에서 빼지 않는다.** 기피는 가중치를 낮추고 `reason`을 쓰는 값이지 후보를 지우는 값이 아니다. 풀에서 빼는 것은 알레르기·연령 금지 재료뿐이다.
- **부족 식품군은 기피보다 세다.** `evaluate_nutrient_bands`가 `low`로 판정한 식품군은 기피 가중치를 상쇄하고 풀 상단에 남는다. 채소 기피 아이에게 채소가 부족하면 채소가 든 메뉴가 후보에 올라와야 하고, 모델은 그중 선호(계란 등)와 겹치는 형태를 고른다 — [`Food_Agent_명세.md`](Food_Agent_명세.md) §6.
- 모델은 **풀 밖 메뉴를 낼 수 없다** (`menu_key` 검증). F-1 해소: 재료는 모델이 아니라 카탈로그에서 온다.
- 풀 자체가 3개 미만이면 재호출해도 채울 수 없으므로 **재호출하지 않는다.** 이때의 처리는 미정(재호출 후 3개 미만과 같은 쟁점).
- 이유기는 카탈로그 대신 **이유식 재료 KB**(단계별 질감 · 도입 재료)에서 풀을 만든다. 공식 API가 없어 코드 상수.

### 0-6. 외부 데이터 소스

| 소스 | 제공 | 용도 | 비고 |
| --- | --- | --- | --- |
| 식약처 **식품영양성분DB정보** — data.go.kr/data/15127578 | 식품코드·분류·영양성분·1회 섭취참고량 | `menu_catalog.nutrients` | REST, 무료. 구 API 폐기 후 통합본 |
| 식약처 **K-FIND** 식품영양성분 DB | 동일 DB 검색 서비스 (130여 종 성분) | 수동 검수 | 2026-07 출범 |
| 식약처 **조리식품 레시피 DB** (COOKRCP01) — foodsafetykorea.go.kr | 메뉴명·재료·1인분 영양 | `ingredients` · 알레르기 | 재료가 텍스트라 파싱 필요 |
| 식약처 알레르기 표시 대상 19종 | 알류·우유·메밀·땅콩·대두·밀·고등어·게·새우·돼지고기·복숭아·토마토·아황산류·호두·닭고기·쇠고기·오징어·조개류·잣 | `ALLERGEN_CODES` 상수 | 번호 = 이 순서 (급식 표기와 동일 여부 확인) |
| NEIS 교육정보 개방포털 급식식단정보 — open.neis.go.kr | 학교 급식 + 알레르기 번호 | `daycare_meal` (학교·일부 유치원) | **어린이집 미포함** |
| 기관 공지 OCR | 식단표 이미지 | `daycare_meal` (어린이집 주경로) | 파이프라인 산출물 읽기만. 저장 직후 대체·제외 메뉴 확인 안내 |
| 지역 어린이급식관리지원센터 표준식단 | 연령별 월간 식단·레시피 | 참고 식단 · 카탈로그 보강 | API 아님. 이용 조건 확인 |
| 보건복지부 「2025 한국인 영양소 섭취기준」 | 연령군별 EAR·RNI·AI·UL·AMDR | `nutrient_reference` 시드 | 문서 → 상수 → 테이블 |

---

## 1. 게이팅

> 게이팅 정본은 [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §3, enum 정본은 [`Tool_공통.md`](../shared/Tool_공통.md) §2. Food는 **`LifeStage.stage` 범주**로 가른다 (배타적 tool이 있어 임계값이 아니라 범주다).

| 라벨 \ 단계 | `infant_milk` 0–3 | `infant_weaning` 4–11 | `toddler` · `preschool` 12+ |
| --- | --- | --- | --- |
| `meal_recommendation` | `()` → readout | search · weaning · propose | search · analyze · repeated · daycare · propose |
| `nutrient_analysis` | `()` → readout | `()` → readout | search · analyze · repeated · daycare · nutrition · balance · report |
| **`daycare_meal`** | `()` | `daycare` · update · delete | `daycare` · update · delete |

`daycare_meal` 라벨은 **`daycare_meal` 행이 있는 아이만** 열린다. 단계로 닫지 않는다 — 기관에 다니면 이유기라도 급식을 먹는다. 행이 없으면 갱신할 대상이 없으니 자연히 `()`다.

`meal_recommendation`에는 영양 tool이 열려 있지 않지만, **영양 구간은 코드가 따로 구한다.** `build_candidate_pool` 직전에 `evaluate_nutrient_bands`를 불러 부족 식품군을 받아 가중치에 쓴다(§0-5). 모델에게는 구간 자체가 아니라 이미 가중된 후보 목록이 간다 — 수치가 모델 입력에 들어가지 않는다.

단계 안에서 코드가 주입하는 값: `texture`(4–5 `puree` / 6–8 `puree`·`mashed` / 9–11 `minced`·`soft_pieces`) · 섭취기준 연령군(12–35 `1-2y` / 36+ `3-5y`) · 연령 식품 규칙(12개월 미만 꿀·생우유 금지, 48개월 미만 질식 주의) · 급식 조회는 `daycare_meal` 행이 있는 아이만.

닫힘 조건 추가: `safety_ok=False` → `meal_recommendation`만 `()`.

**`consent_child_health=False` 는 Food 를 닫지 않는다** (2026-09-24). 동의가 없으면 `health_safety` 와 `child_growth_log` 를 읽지 못할 뿐이고, 그건 조회 실패가 아니라 **읽을 것이 없는 상태**다 — `allergy_states` 가 빈 튜플로 오고 권장 열량은 연령군 일반값으로 간다. 동의를 안 했다고 식단을 못 받으면 안 된다.

Health 는 다르다 — 거기는 건강 그 자체라 동의 없이 전 라벨이 닫힌다. Growth 는 `growth_review` 만 readout 으로 내려간다.

`kind='allergy'` 행들의 `state`(F-4 확정)별 동작 —

| 행 상태 | 식단 추천 |
| --- | --- |
| 조회 실패 | `()` — 빈 목록으로 숨기지 않는다. **막는 것은 이것뿐** |
| 0행 | **연다** — 건강정보 동의를 안 해 행이 없다. 알레르기 없는 아이 기준 일반 식단 |
| `unknown` 이 하나라도 | **연다** + 확인 안내 — 추천은 내보내고 그 항목만 되묻는다 |
| 전부 `none` | **연다.** 없다고 확인한 아이다 |
| `active` 있음 | 연다 (그 행들로 필터) |

`child` 를 만들 때 19종이 전부 `unknown` 으로 들어가고 보호자 답에 따라 `none`·`active` 가 된다. 그래서 **"아직 안 물어봤다"가 `unknown` 이라는 값으로 남아** "없다고 확인함"과 갈린다 — `child.allergy_status` 는 필요 없다.

**거르는 것은 `active` 뿐이다.** `unknown` 은 게이트가 아니라 안내 신호다 — 모르는 항목 때문에 추천을 닫으면 답을 미룬 보호자가 서비스를 못 쓴다. 19종 밖은 추가할 때 `active` 로 들어가고, `retracted` 는 필터에서 `none` 과 같다.

`daycare_meal` 라벨은 이 표의 적용을 받지 않는다 — 급식 기록을 고치는 일이라 알레르기 필터가 걸릴 자리가 아니다. 다만 `consent_child_health=False`면 다른 라벨과 같이 닫힌다.

---

## 2. 모델 tool

| Tool | 입력 | 출력 | 규칙 |
| --- | --- | --- | --- |
| `search_food_memory` | `query?`, `period: "7d"\|"14d"\|"30d"` | 섭취 기록 + food affinity (`rank_evidence` 정렬 결과) | 기간 라벨만, 날짜 직접 지정 불가 |
| `analyze_meal_records` | `period: "3d"\|"7d"\|"30d"` | 메뉴별 횟수 · `amount` 분포 · **기록 행 수** | `intake_daily` + `daycare_meal`(오늘 이전)을 합쳐 읽는다. 끼니 슬롯이 없으므로 "결측 끼니"를 세지 않는다 |
| `check_repeated_menus` | `period`, `min_count=3` | 반복 메뉴 · 반복 주재료 | 두 테이블을 합쳐 센다. 메뉴마다 집·기관 어느 쪽이었는지 표시 |
| `lookup_daycare_menu` | `day: "today"\|"tomorrow"\|"this_week"` | `daycare_meal`의 `menu_keys` · `allergen_codes` · **`allergy_hits`** · `allergy_checked: bool` · `caregiver_checked: bool` · `origin` | 내부에서 `filter_food_safety` 호출. 매핑 실패 → `allergy_checked=False`. 대체·제외는 이미 `menu_keys`에 반영돼 있다. `caregiver_checked=False`면 "대체식 확인 전" · `origin='center_standard'`면 "참고 식단" 표시. **미래를 보는 유일한 읽기 tool** |
| `lookup_nutrition` | `menu_keys[] (≤10)` | 카탈로그 행 요약 (에너지 구성비·식품군) | g 단위 절대값·충족률 반환 안 함 |
| `compare_diet_balance` | `period: "3d"\|"7d"` | 항목별 **구간**(`low`/`ok`/`high`) · 탄:단:지 vs AMDR · 식품군 출현일 · **기록 행 수** | 계산 전부 코드. 충족률 원값은 결과에 넣지 않는다. 행 수 < 임계 → `insufficient=True` |
| `guide_weaning_stage` | `topic: WeaningTopic` | 단계별 질감 · 도입 가능 재료 · 이미 도입한 재료 | infant_weaning 전용 |
| `update_daycare_meal` | `date_span`, `slot?`, `menu_spans[]?`, `amount_span?` | 갱신된 `daycare_meal` 행 + `Readout(code, "daycare_updated")` | **span은 발화 원문의 부분 문자열**(코드 검증) · 날짜 해석은 코드 · 대상 행 없으면 `NOT_FOUND`, 여럿이면 `needs_observation` · 승인 게이트 없음 |
| `delete_daycare_meal` | `date_span`, `slot?` | 행 삭제 + `Readout(code, "daycare_absent")` | 결석 처리. 대상 날짜가 모호하면 **반드시 되묻는다** · 되돌리려면 OCR 재적재가 필요하다 |
| `propose_meal_candidates` | `candidates[3]: {menu_key, evidence_ids[], reason}`, `summary?` | `SuggestionDraft[]` + `summary`가 있으면 `Readout(model, kind="meal_note")` | **샘플링된 후보 목록 밖** `menu_key` 거절 · 사후 `filter_food_safety` · 안전 필터 후 3개 미만 → 재호출 1회 · 근거 0 → general · **기피 근거(`polarity=-1`)를 인용했으면 `reason`에 무엇을 피했는지가 있어야 한다** (없으면 거절) |
| `report_nutrient_analysis` | `findings[]: {band: "low"\|"ok"\|"high", target, food_group_action, basis_ref}`, `summary` | `Readout(model)` | `basis_ref`는 `compare_diet_balance` 결과 항목 필수 · **숫자·%·단위를 쓰면 거절** · `insufficient=True`면 `findings` 비워야 함 |

`WeaningTopic`: `texture` · `new_ingredient` · `portion_rhythm` · `refusal`

## 3. 코드 tool

| Tool | 호출 시점 | 입력 → 출력 | 실패 |
| --- | --- | --- | --- |
| `resolve_meal_date` | `update`·`delete_daycare_meal` 인자 검증 | `date_span` + `today` → date | 사전에 없으면 `needs_observation` · 과거 30일 밖이면 거절 |
| `resolve_menu` | 모든 메뉴 문자열 처리 전 | `name` → `MenuCatalogRow \| Unresolved` | API 실패 → 캐시만 사용, 미스는 `unresolved` |
| `filter_food_safety` | 모델 호출 전(풀) · 후(출력) · 급식 조회 | `menus`, `health_safety`, `stage` → `(passed, blocked, unchecked)` | `health_safety` 조회 실패 → `SAFETY_UNAVAILABLE` (식단 추천 중단) |
| `rank_evidence` | `search_food_memory` 내부 | [`Tool_공통.md`](../shared/Tool_공통.md) §4 | – |
| `build_candidate_pool` | 식단 추천 모델 호출 전 | §0-5 | 풀 0개 → 일반 추천 템플릿 없이 "조건에 맞는 메뉴가 없어요". **기피로는 아무것도 빼지 않는다** — 빠지는 것은 알레르기·연령 금지 재료뿐 |
| `select_kdri_group` | `compare_diet_balance` 내부 | `age_months` → `"1-2y"\|"3-5y"` (`nutrient_reference.age_group`과 같은 값) | – |
| `compute_energy_target` | `compare_diet_balance` 내부 | `age_months` + 최근 `height`·`weight` → 권장 열량(내부 전용) | 측정 없음 → 연령군 일반값. 성별 입력 없음. 계산식 미정(성별 없는 식 선정 필요) |
| `compute_intake_daily` | Memory 저장 후 배치 · 분석 직전 | `observation_food` + `menu_catalog` → `intake_daily` upsert | 미해석 메뉴는 `unresolved_count`로. `daycare_meal`은 건드리지 않는다 — 그건 Food가 tool로 직접 쓰는 테이블이다 |
| `evaluate_nutrient_bands` | `compare_diet_balance` 내부 · **식단 추천에서도 `build_candidate_pool` 직전** | `intake_daily` + `daycare_meal`(오늘 이전) + `nutrient_reference` → 항목별 구간 | **7일에 10행 미만이면** 구간 없이 `insufficient`(설정값). 식단 추천에서는 가중치를 주지 않고 평소 순위로 간다 |
| `search_food_doc` | run 시작 시 자동 | 단계·`row_type` 필터 → 의미 검색 top-3 → 프롬프트 `[예시]` 구획 | 쿼리는 **코드가 조립**한다(라벨·단계·관심사 키). 보호자 발화를 넣지 않는다. 결과는 근거가 아니라 참고라 `memory_kind='food_doc'`으로 담는다 |
| `sample_candidates` | 후보 풀 직후 · 재호출 전 | 풀 → 10~15개 (가중 샘플링 + 식품군 다양성, `seed=run_id`). 가중치는 **부족 식품군 > 선호 > 기피** 순 — 기피는 가중치를 낮출 뿐 0으로 만들지 않는다 | 풀 < 3 → 재호출 없음 (처리 미정) |

### 급식 갱신의 입력은 span이다

`update_daycare_meal` · `delete_daycare_meal`의 인자는 전부 **발화 원문의 부분 문자열**이어야 하고 코드가 검증한다. 복약(`create_medication_schedule`)과 같은 규칙이다 — 모델은 어디를 가리키는지만 말하고, 뜻을 정하는 것은 코드다.

| span | 무엇 | 코드가 하는 일 |
| --- | --- | --- |
| `date_span` | "내일모레" · "지난 화요일" | `resolve_meal_date` → 날짜 |
| `slot` | "점심" · "오전 간식" | 닫힌 enum. 모델이 고르되 값 집합 밖이면 거절 |
| `menu_spans[]` | "두유" · "계란말이" | `resolve_menu` → `menu_key` |
| `amount_span` | "엄청 많이" · "반만" | 아래 사전 → `amount_factor` |

- 모델이 **날짜를 직접 계산하지 않는다.** "내일모레"를 2026-09-24로 바꾸는 순간 기준일을 모델이 정하게 된다
- `menu_spans`가 `unresolved`여도 저장한다. `menu_catalog`에 `resolved=false` 행이 생기고 그 키가 들어가며, 영양 계산에서만 빠진다 (§1)

### `resolve_meal_date` — 코드

| 표현 | 해석 |
| --- | --- |
| 오늘 · 오늘자 | `today` |
| 어제 | `today − 1` |
| 내일 | `today + 1` |
| 모레 · 내일모레 | `today + 2` |
| 이번 주 {요일} | 이번 주(월 시작)의 그 요일 |
| 다음 주 {요일} | 다음 주의 그 요일 |
| 지난 {요일} · 저번 {요일} | 직전에 지나간 그 요일 |
| {N}월 {N}일 | 그 날짜. 연도는 오늘 기준 가장 가까운 쪽 |

- 사전에 없으면 **추측하지 않고 되묻는다** — `needs_observation`: `daycare.ambiguous`
- 기준일은 `Gate`가 들고 있는 `today`(KST)다. `date.today()`를 tool 안에서 부르지 않는다
- 해석 결과가 **과거 30일 밖**이면 거절한다. 보존 범위 밖이라 고칠 행이 없다

### 양 표현 사전 — 코드

[`영양소_계산_설계.md`](영양소_계산_설계.md) §4와 같은 사전이고, 급식에만 쓰는 "많이" 쪽이 더해진다.

| 표현 | `amount_factor` | `amount_known` |
| --- | --- | --- |
| 엄청 많이 · 많이 · 더 달라고 했대 | 1.5 | true |
| 다 먹음 · 한 그릇 | 1.0 | true |
| 반 · 절반 | 0.5 | true |
| 조금 · 몇 입 | 0.25 | true |
| 안 먹음 · 뱉음 · 남겼대 | 0.0 | true |
| (양에 대한 말 없음) | 그대로 | 그대로 |

### slot 을 특정하지 못할 때

**갱신과 삭제가 다르다.**

| | slot 생략 |
| --- | --- |
| `update_daycare_meal` | 그날 행이 하나면 그 행, 여럿이면 **되묻는다**. 엉뚱한 끼니를 고치면 영양 계산이 틀어진다 |
| `delete_daycare_meal` | **그날 전부 삭제.** "어린이집 빠져"는 하루를 통째로 안 가는 것이라 끼니를 물을 이유가 없다 |

### 갱신 뒤 안전 재검사

갱신한 메뉴는 `filter_food_safety`를 다시 거친다. 등록된 알레르기와 부딪혀도 **저장은 막지 않는다** — 보호자가 실제로 있었던 일을 말한 것이고, 기록을 막으면 사실이 사라진다. 대신 경고를 함께 낸다.

> "기록해 뒀어요. 그런데 등록된 우유 알레르기와 겹쳐요 — 기관에 확인해 보시겠어요?"

이것이 추천 경로와 다른 점이다. 추천은 위험한 후보를 **지우고**, 기록은 위험해 보여도 **남기고 알린다.**

### `filter_food_safety` 매칭 규칙
1. `health_safety.label` + `aliases` → **동의어표**로 19종 코드 정규화 (계란/달걀/난류 → 1)
2. 메뉴 `allergen_codes` ∩ 아이 코드 ≠ ∅ → `blocked`
3. 코드 매핑 안 되는 알레르기(예: "키위") → 공통 매처(`app/rules/term_match.py`, Activity와 동일: 공백 제거 부분 일치 + guards + 최엄격 우선)
4. `unresolved` 메뉴 → `unchecked` (추천 풀에서는 제외, 급식 표시에는 "확인 못 함")
5. 연령 규칙: `infant_*` 단계에서 꿀 포함 → `blocked`

---

## 4. 출력 상수 (code readout)

| key | 문구 |
| --- | --- |
| `unsupported.milk_meal` | 이 시기에는 모유나 분유만 먹어요. 이유식은 보통 생후 4~6개월에 시작해요. |
| `unsupported.infant_nutrient` | 돌 전에는 영양소 분석을 지원하지 않아요. |
| `blocked.safety` | 알레르기 정보를 확인할 수 없어서 추천을 드릴 수 없어요. |

| `notice.allergy_unconfirmed` | 아직 확인하지 않은 알레르기 항목이 있다는 안내. **추천과 함께 나간다** |
| `blocked.allergy_unknown` | 알레르기가 있는지 먼저 알려주시면 걸러서 추천해드릴게요. |
| `general.reason.toddler` | 또래 아이들이 많이 먹는 메뉴예요. |
| `general.reason.weaning` | 이 시기 아기들이 많이 먹는 재료예요. |
| `unsupported.milk_amount` | 먹이는 양은 아이마다 달라서 알려드리기 어려워요. 소아과에서 확인해 보세요. |
| `unsupported.weaning_over12` | 지금은 이유식 시기가 지나서 일반식으로 알려드릴게요. |
| `unsupported.weight_diet` | 체중을 목표로 한 식단은 만들지 않아요. 몸무게 이야기는 영유아 건강검진에서 확인해 보세요. |
| `guide.choking_caution` | 통째로는 목에 걸릴 수 있어요. 잘라서 주세요. |
| `nutrient.insufficient` | 최근 식사 기록이 적어서 영양 이야기는 정확하지 않아요. ({recorded}건 기록됨) |
| `nutrient.today_empty` | 오늘 먹은 기록이 아직 없어서 최근 며칠을 기준으로 골랐어요. |
| `nutrient.daycare_amount_unknown` | 급식은 메뉴만 알고 얼마나 먹었는지는 몰라서 참고만 했어요. |
| `daycare.ask_substitute` | 대체식이나 알레르기 때문에 빼놓은 메뉴가 있으면 알려주세요. |
| `daycare.unchecked` | 대체식 확인 전 |
| `daycare.reference_menu` | 지역 표준식단이라 우리 아이가 실제로 먹은 것과 다를 수 있어요. |
| `daycare.updated` | {date} {slot} 급식을 {menu}(으)로 바꿔 뒀어요. |
| `daycare.absent` | {date}은(는) 기관 식사 없는 걸로 해 뒀어요. |
| `daycare.ambiguous` | 어느 날 급식을 말씀하시는 걸까요? |
| `daycare.slot_ambiguous` | 그날은 점심과 간식이 다 있어요. 어느 쪽일까요? |
| `daycare.allergy_conflict` | 기록해 뒀어요. 그런데 등록된 {label} 알레르기와 겹쳐요 — 기관에 확인해 보시겠어요? |
| `daycare.out_of_range` | 너무 지난 날이라 급식 기록이 남아 있지 않아요. |
