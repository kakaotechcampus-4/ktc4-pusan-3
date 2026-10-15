# Food Agent 소유 테이블

> Food가 쓰거나 전용으로 읽는 테이블의 **정본**. 계산 규칙은 [`영양소_계산_설계.md`](영양소_계산_설계.md), 문서 행 제작 절차는 [`RAG_plan.md`](../shared/RAG_plan.md).
> 관련: [`Food_Agent_명세.md`](Food_Agent_명세.md) · [`Food_Tool_명세.md`](Food_Tool_명세.md) · [`Agent_공통규약.md`](../shared/Agent_공통규약.md) §2
>
> **2026-09-22 갱신** — `child_growth_log` 다시 읽음(권장 열량, 성별 제외) · 급식은 `daycare_meal`(Food 소유, CRUD) · 섭취 파생 테이블은 집에서 먹은 것만(10-05 폐기) · 끼니 슬롯 폐지
>
> **2026-09-27 갱신** — `ingredients`가 빈 배열인 행도 후보 풀에서 제외
>
> **2026-10-03 갱신** — 포트(`app/agents/food/store/ports.py`)와의 차이를 정리했다(#203 리뷰). 포트에는 Food 쓰기가 셋(급식 수정·삭제 · 영양 구간 · 메뉴 카탈로그)인데 이 문서는 급식만 다뤘다. 영양 구간 저장 위치와 메뉴 카탈로그를 채우는 주체는 **TODO: 확정 필요** — 가능한 안만 적었다(§1 · §2-2 · FT-12 · FT-13). 영양 구간 포트(`NutrientBandStore`, 09-28)가 이 문서(09-22) 뒤에 들어오면서 생긴 차이다
>
> **2026-10-04 갱신** — 메뉴 카탈로그를 누가 채우는지 정했다(FT-13 닫힘). 캐시 미스면 Food 요청 안에서 외부 조회를 하고, 결과를 받은 뒤 백엔드 어댑터가 없을 때만 넣는다. DB 권한 방식은 명성 님이 정한다(§0 권한 줄 TODO). 영양 구간 저장 위치(FT-12)는 추후 정리한다.
>
> **2026-10-13 갱신 (#321 파트 2)** — 배치가 영양DB 행을 고르는 함수를 어떻게 부를지(FT-19 "BE 확인 대기") 정함 — `pick_nutrition`/`NutritionFacts` 가 `app.core.nutrition_pick` 으로 내려와 배치는 `nutrition_facts_by_key(rows)` 를 직접 부른다(§1)
>
> **#459 · #420 갱신** — 원재료성 행은 대분류 이름이 비면 코드로 식품군을 찾고, 음식 행은 대응표의 메뉴 이름 · 대분류 줄 또는 이름이 같은 레시피의 주재료 식품군을 빌린다(§1 · §1-1). 재료 정리 표 검수 확정(FT-18 — unknown 28). 운영 적재는 수동 워크플로 `load-food-catalog.yml`(FT-19)
>
> **2026-10-14 갱신 (#442)** — 농식품정보원 이용 조건 답을 받아(상업 이용 · 변경 가능) 정리 표를 저장소에 두고 운영에 적재하기로 했다(§1-1). 두 표(`app/domains/food/`)와 적재 스크립트(`scripts/load_food_catalog.py`)가 생겼다 — 배치는 `mfds_nutri` 행만 고치고 `stage_min` 은 건드리지 않는다(§1 · FT-19)
>
> **2026-10-11 갱신 (#321)** — 요청 중 외부 조회는 식품영양성분DB 하나, 레시피 재료는 농식품정보원 배치로만(COOKRCP01 보류) · 판정의 외부 조회 상한 · 기억 기간을 정함(§1) · `source` 에 `mafra_recipe` · 재료명 정리 표(§1-1) · FT-7 보류 · FT-17 측정 · FT-18 추가
>
> **2026-10-11 갱신 (#326 리뷰)** — 레시피를 `recipe_catalog` 로 뗐다(§1-1). `menu_catalog` 는 급식 · 기록 · 영양 판정용이고 `source` 에서 `mafra_recipe` · `mfds_recipe` 를 뺐다. 영양DB 는 매일 새벽 품목대표 전체 배치 + 요청 중 조회(FT-19 정함). 1인분 중량이 빈 메뉴는 영양 합계에서 뺀다(§1)
>
> **2026-10-05 갱신** — 섭취 파생 테이블을 없앴다(#205). 집에서 먹은 것은 `observation_food`, 기관 급식은 `daycare_meal` 이 원본이고, 영양 합계·구간은 저장하지 않고 판정할 때마다 원본에서 계산한다(§2 · FT-12 닫힘). Food 쓰기는 둘(급식 수정·삭제 · 메뉴 카탈로그)이 됐다. 캐시 미스는 판정 · 집 식사 이름도 외부 조회로 채우되 이름이 정확히 같은 결과만 쌓고, 해석 실패는 남기지 않는다(§1)

---

## 0. 무엇이 Food 소유인가

**Food의 쓰기 포트는 둘입니다** — `daycare_meal` 수정·삭제(`DaycareMealStore`), 메뉴 카탈로그(`MenuCatalogStore.put`). 영양 합계와 영양 구간은 저장하지 않고 판정할 때마다 원본에서 계산합니다(§2, 10-05). 나머지는 참조이고, 아이의 관찰·프로필은 손대지 않습니다.

둘 중 **보호자 말을 반영한 쓰기는 `daycare_meal` 하나**이다. 메뉴 카탈로그는 추천·분석을 계산하는 중에 생기는 내부 값이다. 없을 때만 넣어서 다시 보내도 결과가 같고, `run_writes`로 감싸지 않는다 — 보호자 말을 반영한 쓰기만 감싸서 run의 commit 판정을 가른다([`Agent_공통규약.md`](../shared/Agent_공통규약.md) §2 "쓰는 시점").

`daycare_meal`이 예외인 이유는 Health의 `medication_*`과 같습니다 — **다른 Agent가 읽지 않는 도메인 전용 테이블**이라 공유 테이블의 단일 writer 원칙에 걸리지 않습니다.

| 테이블 | 쓰는 주체 | 읽는 주체 | 성격 |
| --- | --- | --- | --- |
| `menu_catalog` | 동기화 배치 (`integrations`) · 캐시 미스는 Food 요청 중 외부 조회 뒤 백엔드 어댑터가 없을 때만 INSERT (§1) | Food | 메뉴 카탈로그 — 급식 · 기록 · 영양 판정. 영양DB 결과 + 사람이 넣은 행 |
| `recipe_catalog` | 레시피 배치 (`integrations`) (§1-1) | Food (식단 추천 후보 풀) | 레시피 카탈로그 — 추천 후보. 레시피 재료 + 영양 |
| `menu_alias` | 동기화 배치 (`integrations`) | Food | 표기 사전 |
| **`daycare_meal`** | 최초 적재는 OCR·급식 배치(INSERT) · 이후 갱신·삭제는 **Food** | Food | **도메인 전용** — 아무도 안 읽는다 |
| `nutrient_reference` | 마이그레이션 시드 | Food | 참조 상수 |
| `food_doc` | 마이그레이션 시드 | Food | 문서 행 |
| `allergen_term` | 마이그레이션 시드 | Food · **Health** | 공유 참조 |

| 읽기만 하는 공유 테이블 | 소유 |
| --- | --- |
| `observation_food` · `profile_affinity(domain=food)` | Memory |
| `health_safety` | 앱 API (보호자 권한). Agent 는 `domains/safety/agent_reader.py` 의 `DbSafetyReader.food_safety`(active · kind 전부)로 읽는다 — entrypoint 연결은 #405 PR B |
| `child` (생년월일) | 앱 |
| `child_growth_log` (키·몸무게·측정일) | Memory |
| `suggestion` | 주입된 writer가 INSERT (`status='draft'`) |

> `child_growth_log`는 **권장 열량 계산 입력으로 읽습니다**(2026-09-22, 개정 2 번복). 영양소 기준은 연령군 일반값이고, **권장 열량만** 키·몸무게로 조정합니다. 측정이 없으면 연령군 일반값을 씁니다. 계산 결과는 내부 전용이고, 측정값으로 과체중·저체중을 판정하지 않습니다. **성별은 읽지 않습니다.**

DB 권한: Agent role에 **`daycare_meal` UPDATE/DELETE**(INSERT는 비부여) · OCR·배치 role에 `daycare_meal` INSERT 부여 · `menu_catalog` 는 배치와 캐시 미스 저장 어댑터(없을 때만 INSERT)만 쓴다 · `recipe_catalog` 는 레시피 배치만 쓴다 · `nutrient_reference`·`food_doc`·`allergen_term` write **비부여**(배치 role만) · `health_safety` write **비부여**.

> **TODO: 확정 필요** — 캐시 미스 저장 어댑터의 권한을 DB 에서도 좁힐지는 명성 님이 정한다. 계정 하나로 두고 포트로 막는 안(#203 재형 님 코멘트)과, 어댑터 트랜잭션 안에서만 `SET LOCAL ROLE` 로 INSERT 만 가진 역할로 바꾸는 안이 있다. 실제 GRANT 는 백엔드가 만든다.

---

## 1. `menu_catalog` — 메뉴 카탈로그 (메뉴 해석)

메뉴명 문자열을 식품코드·재료·영양으로 바꾸는 유일한 경로. 카탈로그에 없을 때(캐시 미스)만 외부 API를 부르고 결과를 여기 쌓는다 — 급식 · 영양 판정 · 집 식사 이름 모두 같은 흐름이다.

**추천 후보(레시피)는 여기 두지 않는다**(10-11, #326 리뷰). 레시피는 §1-1 의 `recipe_catalog` 에 따로 둔다. 한 표에 두면 레시피 재료가 같은 이름의 급식 메뉴 판정에 쓰여, 어린이집마다 다른 재료(바지락 넣은 된장찌개)가 표준 레시피 재료(멸치 육수)로 판정되고 조개류 알레르기 아이에게 "통과" 가 나간다. 또 영양DB 배치와 레시피 배치가 같은 `menu_key` 를 두고 순서를 다툰다. `menu_catalog` 의 `menu_key` 는 지금처럼 PK 하나다 — 판정이 키 하나에 행 하나로 읽고, 출처 우선순위는 쓸 때 정한다(사람이 넣은 행은 배치가 덮지 않는다).

외부 API 결과를 쌓는다는 점에서는 캐시지만, 사람이 넣은 행(`source`가 `manual` · `center_standard`)과 수동 태깅(`stage_min`)이 있어 지우고 다시 채울 수 있는 순수 캐시는 아니다. 그래서 **메뉴 카탈로그**라고 부르고, 카탈로그에 없는 메뉴를 찾는 경우만 "캐시 미스" 라고 한다.

**정함 (2026-10-04, FT-13)** — 캐시 미스면 Food 요청 안에서 `MenuSource` 로 외부 조회를 하고, 결과를 받은 뒤 **백엔드 어댑터가 제한된 권한으로 짧게 저장**한다([멘토 #196 답변](https://github.com/kakaotechcampus-4/ktc4-pusan-3/pull/196#issuecomment-5976970529)). 10-06 에 안전장치를 붙였다 — 이름이 정확히 같은 결과만 쌓고, 해석 실패는 남기지 않고, 판정 한 번의 외부 조회 수를 묶는다(아래).

- 외부 API 를 기다리는 동안 DB 트랜잭션을 쥐지 않는다. 저장은 결과를 받은 뒤 짧은 트랜잭션 하나다.
- 없을 때만 넣는다 — `INSERT ... ON CONFLICT (menu_key) DO NOTHING`. 같은 메뉴를 두 run 이 동시에 채워도 한 행만 남고, 사람이 넣은 행(`manual` · `center_standard`)과 수동 태깅(`stage_min`)을 덮지 않는다. 있는 행을 고치거나 지우는 길은 포트에 없다. DB 권한까지 좁힐지는 명성 님이 정한다(§0 권한 줄 TODO).
- **요청 중 외부 조회는 식품영양성분DB 하나다**(10-11). 레시피 재료는 찾지 않는다 — 요청 중에 채운 행은 재료가 비어 알레르기가 "확인 못 함" 이다. 레시피 재료는 이 표에 싣지 않는다(추천 후보용 `recipe_catalog` 에만 있다, §1-1). 레시피 DB 를 요청 중에 부르면 호출 한도가 서비스 전체 상한이 된다.
- `MenuSource.nutrition_rows(name)` 는 검색에 걸린 원문 행(`core/mfds_nutri_raw.RawNutriRow`)을 다 주고, 어느 행을 쓸지는 Agent 가 고른다(`nutrition_source.py` — [`Food_Tool_명세.md`](Food_Tool_명세.md) §0-7). 영양DB 검색이 포함 검색이라 이름이 다른 행이 섞여 오기 때문이다.
- 외부 조회에 실패하거나(어떤 예외든 — 클라이언트는 agents 를 import 하지 못해 `MfdsApiError` 를 던진다) 정보가 부족하면 그 run 에서 그 메뉴는 unresolved 다. 알레르기는 "확인 못 함"이고 안전한 것으로 보지 않는다.
- **해석 실패는 카탈로그에 남기지 않는다** — 정말 없음도, 외부 API 장애도(10-06). 남기면 보호자 말에서 나온 이름(예: "맛있는된장찌개")이 `child_id` 없이 전역 테이블에 남는다.
- **이름이 정확히 같은 결과만 쌓는다.** 정규화한 이름과 외부 DB 의 이름이 같을 때만 받는다 — 비슷한 메뉴로 추정하지 않는다(된장찌개 → 부대된장찌개 금지). 그래서 쌓이는 행의 `menu_key` 는 공개 DB 에 실제로 있는 이름이고, 보호자 표현이 그대로 키가 되지 않는다. 밖으로 나가는 것도 정규화한 메뉴 이름뿐이다(아이 식별자 없음).
- **판정의 외부 조회는 묶는다.** 영양 판정은 14일 치를 매번 다시 읽는다([`영양소_계산_설계.md`](영양소_계산_설계.md) §3). 찾은 메뉴는 카탈로그에 쌓여 다음부터 외부를 부르지 않는다. 못 찾은 이름은 판정 한 번 안에서 한 번만 묻고, 다음 판정에서 같은 이름을 다시 묻지 않게 프로세스 메모리에 짧게 기억한다(DB · 로그에는 남기지 않는다). 판정 한 번의 외부 조회 수에는 상한을 두고 동시에 부른다. 장애면 그 판정에서 unresolved 다. **값(10-11, 잠정)** — 판정 한 번에 외부 조회 5회 · 호출당 8초 · 못 찾은 이름 기억 30분 · 최대 2,000개(`menu_resolver.py`). 장애와 상한 때문에 못 부른 이름은 "없음" 이 아니라 기억하지 않는다. ⚠️ **8초가 모자란다** — 이름 검색을 5개 동시에 부르면 중간 6.9초 · 최대 15.5초라, 메뉴명 30개([`Food_Tool_명세.md`](Food_Tool_명세.md) §0-7 의 점검 목록)를 이 값으로 돌리니 찾을 수 있는 21개 중 6개가 시간 초과로 빠졌고 5개 묶음마다 6.8 ~ 8초를 썼다(10-11 16시. 17시에 다시 재니 응답이 4초 안팎이라 시간 초과가 없었다 — 시간대마다 다르다). 20초 예산 때문에 늘릴 수도 없어서, 품목대표 전체를 매일 새벽 배치로 미리 받기로 했다(FT-19 정함).
- **커버리지는 배치가 넓힌다.** [`외부연결_계획.md`](../shared/외부연결_계획.md) §2 의 소스(식약처 영양성분 — 매일 새벽 품목대표 전체(FT-19), 지역 센터 표준식단)를 소스마다 이용 조건을 확인한 뒤 배치가 싣는다. 농식품정보원 레시피는 이 표가 아니라 `recipe_catalog` 에 싣는다(§1-1). 지역 센터 표준식단은 센터별로 조건이 달라 확인 전에는 쓰지 않는다. 사람이 넣는 것(`menu_alias` 의 `seed`, `stage_min` 태깅, `manual` 행)은 검수할 수 있는 시드 파일로 두고 같이 싣는다. 배치는 미리 채워 두는 것이고, 요청 중 캐시 미스도 위 규칙대로 채운다. 그래도 못 찾은 메뉴는 그 판정에서 unresolved 이고, 해석률이 낮으면 결론을 막는다(§2-1).
- **배치가 고치는 범위** — `source='mfds_nutri'` 행만 새 값으로 바꾸고 `stage_min` 은 건드리지 않는다. 사람이 넣은 행(`manual` · `center_standard`)은 덮지 않는다(`domains/food/repository.py` 의 `refresh_nutrition_menus`). 영양DB 행을 만드는 함수는 요청 중 조회와 배치가 하나를 같이 쓴다(`menu_resolver.nutrition_catalog_row`) — 경로마다 칸이 다르면 같은 메뉴가 다르게 판정된다.
- 검토한 안 B(배치만 채움) · C(캐시 미스만 넘기고 백엔드가 채움)는 처음 보는 메뉴가 다음 배치 전까지 그날 "확인 못 함" 이 돼서 접었다. 10-11 에 그 전제가 달라졌다 — 요청 중 조회가 고를 수 있는 행(품목대표)이 전부 8,807행이라 배치가 한 번에 다 받아 둘 수 있다. 그래서 안 B 에 요청 중 조회를 더한 쪽으로 정했다(10-11, #326 리뷰 · FT-19) — 매일 새벽 배치가 품목대표를 다 받고, 요청 중 조회는 배치 뒤 영양DB 에 새로 생긴 행을 위해 남긴다.

지금 코드는 `menu_source` 포트가 없으면(API 키 없음) 카탈로그만 쓰고 캐시 미스는 unresolved로 둔다. 찾아보지 않았으니 카탈로그에 남기지도 않는다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `menu_key` | text | **PK.** 정규화 메뉴명 (공백·괄호·급식 알레르기 번호 제거, 동의어 치환) |
| `display_name` | text | NOT NULL. 화면에 쓰는 이름 |
| `food_code` | text | nullable. 식약처 식품코드 |
| `source` | varchar(16) | `mfds_nutri` / `center_standard` / `manual`. 레시피(농식품정보원 `mafra_recipe` · 보류한 COOKRCP01)는 `recipe_catalog` 에 둔다(§1-1, #326 리뷰). `center_standard` 는 센터별 이용 조건을 확인한 뒤 들어올 때 다시 본다 |
| `serving_g` | numeric(7,1) | nullable. 1인분 중량 — **성인 기준**. 영양DB 원재료성 · 가공식품 품목대표는 대부분 비어 있다(10-11). 비면 모르는 양이다 — 그 메뉴는 영양 합계에서 빼고 식품군 계산에만 쓴다(1차 배포, [`영양소_계산_설계.md`](영양소_계산_설계.md) §4 · N-15) |
| `nutrients` | jsonb | NOT NULL, default `'{}'`. 100g 기준. **식품영양성분DB 에서만 채운다.** 에너지 · 단백질 · 지방 · 탄수화물 · 칼슘 · 철 · 나트륨은 다 차야 하고, 당류 · 포화지방은 있으면 싣는다(10-11). 사람이 넣은 행은 비어 있을 수 있고, 비면 영양 판정은 그 메뉴를 unresolved 로 센다 |
| `ingredients` | text[] | NOT NULL, default `'{}'`. 사람이 확인한 재료(`manual`)만. 영양DB 행은 비어 있다 — 알레르기는 메뉴명 사전으로 보고 나머지는 "확인 못 함". 레시피 재료는 싣지 않는다(§1-1) |
| `allergen_codes` | smallint[] | NOT NULL, default `'{}'`. 19종 — 재료 + 메뉴명 사전의 **합집합** |
| `food_groups` | text[] | NOT NULL, default `'{}'`. 6개 식품군. 영양DB 행은 `reference/food_groups.yaml` 로 메뉴 이름 줄 → 대분류 이름 줄 → (원재료성이고 이름이 비면) 대분류 코드 줄 순서로 찾고, 없으면 적재 배치가 이름이 정확히 같은 레시피의 주재료 식품군을 빌린다(#459 · #420). 그래도 없으면 비운다 — 추정하지 않는다 |
| `resolved` | boolean | NOT NULL, default false |
| `source_version` | varchar(32) | 데이터 기준 버전 |
| `synced_at` | timestamptz | NOT NULL |

**규칙**
- 후보 풀은 이 표가 아니라 `recipe_catalog` 에서 고른다(§1-1). 이 표의 `resolved=false` 행과 `ingredients`가 빈 배열인 행은 급식 표시에서 "알레르기 확인 못 함"이 된다. `ingredients`가 비는 것이 곧 "재료를 모른다"는 뜻이고, 별도 컬럼을 두지 않는다. 재료가 비면 메뉴명 사전만으로 알레르기를 보게 되어 "크림수프"의 우유가 빠질 수 있어서(F-1), 재료를 모르는 행은 안전한 것으로 보지 않는다. 이름으로 걸리는 알레르기는 재료가 없어도 걸러진다 — "새우볶음밥"은 재료가 비어도 이름에서 새우가 걸린다.
- `allergen_codes`는 재료에서 뽑은 것과 메뉴명 사전에서 뽑은 것의 **합집합**이다. 한쪽만 쓰면 "크림수프"의 우유가 빠진다(F-1).
- 레시피 행은 받지 않는다 — DB 는 `source` CHECK, 코드는 `RECIPE_SOURCES`(`store/ports.py`)로 막는다.
- 운영 트래픽을 API에 직접 걸지 않는다. 매일 새벽 배치(FT-19) + 카탈로그에 없을 때(캐시 미스) 1회 — 그 1회는 Food 요청 중에 부르고, 저장은 어댑터가 한다.
- 배치는 요청 중 조회와 **같은 규칙으로 행을 고른다**(`nutrition_source.pick_nutrition` — 품목대표 · 이름 정확히 일치 · 그룹 순서). 다르게 고르면 같은 메뉴가 배치 전후로 다른 영양값을 갖는다. **✅ 정함(10-13, #321 파트 2)** — 이 함수는 `app.core.nutrition_pick` 으로 내려왔다(배치·Agent 공용, apps/api/CLAUDE.md 레이어 경계). 배치는 받은 품목대표 행을 그대로 `nutrition_facts_by_key(rows)` 에 넘겨 `dict[menu_key, NutritionFacts]` 를 받는다 — 메뉴마다 직접 `pick_nutrition_row` 를 부를 필요가 없다. (#326 코멘트로 BE 에 전달 예정)

### 1-1. `recipe_catalog` — 레시피 카탈로그 (추천 후보) · 재료명 정리 표

**식단 추천 후보 풀은 이 표에서만 고른다. `menu_catalog`(§1)와 떼어 둔다**(10-11, #326 리뷰). 급식 · 기록 · 영양 판정은 이 표를 읽지 않는다 — 레시피 재료로 급식 메뉴를 판정하면, 어린이집이 실제로 넣은 재료(바지락)를 표준 레시피(멸치 육수)가 "없음" 으로 덮는다. 떼어 두면 영양DB 배치와 레시피 배치가 같은 `menu_key` 를 두고 순서를 다툴 일도, 있는 행을 고치는 UPDATE 도 필요 없다.

후보 풀은 재료가 있는 행만 쓴다. 그 재료를 농식품정보원 레시피에서 **배치로만** 채운다 — 요청 중에는 부르지 않는다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `source` | varchar(16) | **PK (`source`, `source_id`).** `mafra_recipe`. COOKRCP01 을 들이면 `mfds_recipe` 도 여기다 |
| `source_id` | text | 소스의 레시피 번호(농식품정보원 `RECIPE_ID`) |
| `menu_key` | text | NOT NULL, **UNIQUE 아님**. 레시피 이름을 `normalize_menu_key` 로 바꾼 것. 이름이 같은 레시피도 따로 행을 갖는다(아래) |
| `display_name` | text | NOT NULL. 레시피 이름 |
| `food_code` · `serving_g` · `nutrients` | | 레시피 이름이 영양DB 와 정확히 같을 때만 채운다(§1 규칙). 아니면 비우고, 영양 가중에 쓰지 않는다 |
| `ingredients` | text[] | NOT NULL, default `'{}'`. 재료명 정리 표로 정리한 이름 (아래) |
| `allergen_codes` | smallint[] | NOT NULL, default `'{}'`. 표의 코드 ∪ 메뉴명 · 정리한 재료명 사전의 **합집합** |
| `food_groups` | text[] | NOT NULL, default `'{}'`. 6개 식품군. 주재료의 `food_group` 합집합 — 주재료가 없거나 하나라도 칸이 비면 빈 집합(일부만 적으면 빠진 식품군이 "0일" 이 된다, #420) |
| `main_ingredients` | text[] | NOT NULL, default `'{}'`. 원문 재료 역할(`IRDNT_TY_NM`)이 주재료인 재료의 정리한 이름. 비면 "같은 주재료 2개" 제약만 건너뛴다 |
| `stage_min` | varchar(20) | 이 메뉴가 가능한 최소 단계. `LifeStage.stage` 네 값 — [`Tool_공통.md`](../shared/Tool_공통.md) §2 (수동 태깅, FT-1). 비면 `toddler` 로 본다 — **보수적인 쪽**(이유기 아이에게 나가지 않는다) |
| `resolved` | boolean | NOT NULL, default false. 재료가 다 정리됐을 때만 true |
| `source_version` · `synced_at` | | 데이터 기준 버전 · 적재 시각 |

지금 `build_recipe_row`(`app/agents/food/recipe_catalog.py`)는 `MenuCatalogRow` 모양으로 행을 만든다 — 두 표가 같은 모양을 쓰고 `source` 로 가른다(`store/ports.py` 의 `RECIPE_SOURCES`). 표 · 어댑터는 `app/domains/food/`(`models.py` · `repository.py` · `agent_reader.py`)에 있고, 적재는 `scripts/load_food_catalog.py` 다.

- **받기** — 레시피 기본정보(537건)와 재료정보(6,104행)를 1,000행씩 받아 `RECIPE_ID` 로 잇는다(`app/integrations/mafra/client.py`, 전체 동기화 8회). 받은 행 수가 `totalCnt` 와 다르거나 응답 모양이 다르면 적재하지 않는다 — 일부만 실으면 재료를 덜 아는 레시피가 생긴다. 실패하면 이전 적재본을 그대로 둔다. 다시 받으면 같은 (`source`, `source_id`) 행을 새 값으로 바꾼다.
- **버리는 행** — 기본정보가 없는 재료 행만 버린다(10-11: 171행, RECIPE_ID 501 ~ 540 의 38개). 재료 이름이 빈 행은 버리지 않고 "모르는 재료" 로 남긴다 — 버리면 그 레시피가 재료를 다 아는 것처럼 보인다(10-11 기준 0행).
- **이름이 같은 레시피** — 4쌍이다(샤브샤브 · 깻잎장아찌 · 고추장아찌 · 보쌈김치, 쌍마다 재료 목록이 다르다. 10-11 라이브 eval `test_recipe_sync`). 이 표는 (`source`, `source_id`) 가 키라 두 레시피가 따로 행으로 남는다. 후보 풀에서 같은 `menu_key` 를 어떻게 보여 줄지는 식단 추천(issue3)에서 정한다 — 하나로 묶는다면 알레르기 코드는 합집합이 안전 쪽이다.
- **재료는 정리 표로만 읽는다** (`reference/recipe_ingredients.yaml`, 사람 검수 · `app/agents/food/recipe_catalog.py`). 원문을 그대로 사전으로 훑으면 오타("돼기고기", 6행)가 돼지고기 알레르기에 안 걸리고, 묶음 표기("식용유/소금/참기름/잣가루")에 잣이 숨는다. 섹션 머리("[쇠고기양념] 간장")는 재료가 아니다. 부위명(안심 · 선지 · 곱창 · 장조림 …)은 10-11 에 안전 사전으로 옮겨 소 · 돼지 둘 다로 막는다(#326 리뷰, Food_Tool_명세 §3).
  - 표 한 줄 = 원문 이름 → 정리한 이름 · 확실한 19종 코드 · `status`(`ok` 검수함 / `not_food` 음식 아님 / `unknown` 구성을 알 수 없음 / `draft` 검수 전) · `food_group`(주재료의 식품군 한 개, 식품군이 없으면 `none` — 비면 아직 안 정함. 영양DB 음식 메뉴와 이름이 같은 레시피의 주재료만 채웠다).
  - `ok` · `not_food` 만 정리된 것으로 센다. 재료가 하나라도 표에 없거나 `draft` · `unknown` 이면 그 레시피는 **`resolved=false`** — 후보 풀에 들어가지 않고 필터에서도 "확인 못 함" 이다.
  - `allergen_codes` = 표의 코드 ∪ 메뉴명 · 정리한 재료명을 사전으로 훑은 코드. 제품마다 다른 성분(간장의 밀)은 표 코드에 넣지 않는다 — 이름이 남아 있으면 필터가 따로 본다.
  - 합집합이라 **표에서 코드를 좁힐 수 없다.** 정리한 이름이 "안심" 이면 사전이 소 · 돼지를 둘 다 붙인다 — 쇠고기 안심이면 정리한 이름을 "쇠고기 안심" 처럼 고기를 밝혀 적는다(그래도 돼지 코드는 남는다, 막는 쪽). 정리 스크립트의 코드 후보 표(`EXTRA_CODES`)에는 사전에 넣을 수 없는 한 글자 이름("양" — 소의 위)만 남았다.
  - 초안은 `scripts/draft_recipe_ingredients.py` 가 만든다 — 원문 715개 표기가 696개 항목으로 모였고 전부 `draft` 다(10-11). 다시 돌려도 있는 항목은 덮지 않는다. 많이 나온 이름부터 검수하면 100개 → 레시피 64개, 200개 → 183개, 300개 → 278개가 풀린다.
- `nutrients` 는 레시피 이름이 영양DB 와 정확히 같을 때만(§1 규칙) · `food_groups` 는 주재료 `food_group` 합집합(다 정했을 때만) · `stage_min` 은 사람이 태깅한 값만(FT-1), 비면 toddler.
- ~~**이미 있는 행 채우기**(#321 합의 3)~~ — 표를 떼면서 필요 없어졌다(10-11, #326 리뷰). 레시피 배치는 `menu_catalog` 를 고치지 않는다.
- ~~**급식 판정과 충돌**~~ — 표를 떼어 해결했다(10-11, #326 리뷰). 레시피 행이 `menu_catalog` 에 실려 "된장찌개" 급식이 레시피 재료로 판정되던 경로가 없어졌다. 운영 적재 전에 남은 조건은 정리 표 검수(FT-18)다.
- **이용 조건** — 답을 받았다(상업 이용 · 변경 가능). 정리 표(재료 이름 · 코드)를 저장소에 두고 운영 DB 에 적재한다(FT-18).

### `menu_alias` — 표기 흔들림 흡수

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `alias` | text | PK. 정규화된 표기 ("카레", "카레라이스", "커리") |
| `menu_key` | text | NOT NULL. FK → `menu_catalog.menu_key`, CASCADE |
| `created_by` | varchar(16) | `seed` / `batch` / `manual` |

---

## 2. 영양 판정 — 저장하지 않는다

집에서 먹은 것은 `observation_food`(Memory 소유), 기관에서 먹은 것은 `daycare_meal`(§5)이 원본이다. **영양 합계와 영양 구간은 저장하지 않는다**(10-05, #205). 판정할 때마다(식단 추천 직전 · 영양소 분석) 원본에서 계산하고, 결과는 그 task 의 run state 에만 둔다. DB · 로그에 남기지 않는다. 계산 흐름은 [`영양소_계산_설계.md`](영양소_계산_설계.md) §3.

그래서 Food 에는 집 식사를 옮겨 담는 파생 테이블이 없다. 보호자가 기록을 고치면 다음 판정에 바로 반영되고, 파생 행의 upsert 기준 키 · 보관 기간 · 마이그레이션을 정할 일도 없다.

### 2-1. 기록이 충분한가 — 행 수

끼니 비율(`기록 끼니 / 기대 끼니`)로 세던 커버리지를 버렸다. 끼니 슬롯이 없으니 분모가 성립하지 않는다.

**판정 기간 7일 안에 `observation_food` + `daycare_meal`(오늘 이전) 합쳐 10행 미만이면 수치 기반 결론을 내지 않는다**(`insufficient`). 설정값이다.

- 하루 평균 1.5행이다. 급식이 있는 아이는 평일만으로 채워지고, 집에서만 먹는 아이도 하루 2건이면 닿는다.
- 메뉴 해석률은 따로 본다 — 행은 있는데 메뉴를 못 읽으면 그것도 결론을 막는다. 해석률도 판정할 때 센다.

### 2-2. 영양 구간 — 저장하지 않고 다시 판정 (FT-12 닫힘, 10-05)

구간 판정에는 히스테리시스가 있다 — 한 번 `low`로 표시한 항목은 EAR의 1.1배를 넘어야 `ok`로 돌아간다([`영양소_계산_설계.md`](영양소_계산_설계.md) §6). 그래서 전날 구간을 알아야 하는데, 저장하지 않고 다시 판정해서 얻는다. 판정 창 앞 7일 전부터 하루씩 다시 판정하면 전날 구간이 나온다 — 원본은 판정 창 7일 + 7일 = 14일 치를 읽는다(`daycare_meal` 과거 30일 보존 안).

- 저장한 판정과 갈리는 것은 일평균이 7일 내내 EAR과 1.1배 사이에 머문 경우뿐이고, 그때는 기본 기준으로 돌아간다. 히스테리시스는 그 사이에 머무는 동안만 과거를 기억하고, 벗어나는 날 판정이 새로 정해진다.
- 검토한 안 A(전용 테이블 · 항목당 한 행) · B(아이당 jsonb 한 행)는 아이 데이터 테이블이 늘어 보관 기간 · 동의 철회 범위를 정해야 해서 접었다.
- 영양 구간을 저장하는 포트는 두지 않는다. 구간 값 타입(`NutrientBand`)은 판정 결과에만 쓴다.

---

## 3. `nutrient_reference` — 섭취기준

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK |
| `age_group` | varchar(8) | `0-5m` / `6-11m` / `1-2y` / `3-5y` |
| `sex` | varchar(8) | nullable = 구분 없음 |
| `nutrient` | varchar(32) | `energy` · `protein` · `sodium` · `calcium` · `iron` … |
| `ref_type` | varchar(8) | `EAR` / `RNI` / `AI` / `UL` / `AMDR` |
| `value_min` · `value_max` | numeric(10,3) | `AMDR`만 범위 |
| `unit` | varchar(16) | `kcal` · `g` · `mg` · `%kcal` |
| `source_version` | varchar(16) | `kdri_2025` |
| `note` | text | 기준의 단서 |

- `UNIQUE (age_group, sex, nutrient, ref_type, source_version)`
- 개정 시 **새 버전을 넣고 조회 기본값만 바꾼다.** 기존 행을 덮어쓰지 않는다.
- 부족은 EAR, 과잉은 UL, 탄단지는 AMDR. **손으로 옮긴 값은 2인 대조**를 거친다 — 숫자 오타가 곧 판정 오류다.

---

## 4. `food_doc` — 문서 행

공통 컬럼은 [`RAG_plan.md`](../shared/RAG_plan.md) §1. Food 전용 컬럼만 적는다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `row_type` | varchar(24) | `weaning_stage` / `weaning_ingredient` / `meal_pattern` / `nutrient_note` / `feeding_tip` |
| `food_groups` | text[] | 6개 식품군 — `grains` / `meat_fish_egg_legumes` / `vegetables` / `fruits` / `dairy` / `fats_sugars` (#337 잠정) |
| `nutrients` | text[] | `nutrient_note`만 |
| `texture` | varchar(16) | 이유기 행만. `puree` / `mashed` / `minced` / `soft_pieces` / `regular` |
| `allergen_codes` | smallint[] | 제목 · 본문에 나오는 19종 코드를 다 적는다 — 조회 후 `filter_food_safety` 통과 |

- 시드는 `apps/api/reference/food_doc.yaml`, 로더는 `app/agents/food/doc_seed.py`, 내용 검사는 `tests/unit/agents/food/test_food_doc_seed.py` 다. 소스 기준은 RAG_plan §3 (#337).
- 조회는 `status='approved'`만. 단계·`row_type` 필터 → 의미 검색 top-3.
- 이유기(4–11개월)는 `profile_affinity`가 구조적으로 0행이라 **이 테이블이 곧 추천 품질**이다.

---

## 5. `daycare_meal` — 기관에서 먹는 것 (Food가 고치는 테이블)

**Food가 보호자 말을 반영해 고치는 유일한 테이블이다**(메뉴 카탈로그는 계산 중에 생기는 내부 값이다 — §0). 단 **INSERT는 못 한다** — 행을 만드는 것은 OCR·급식 배치뿐이고, Food는 이미 있는 행을 고치거나 지울 뿐이다. 없는 급식을 지어내는 경로를 애초에 열지 않는다. 다른 Agent가 읽지 않는 도메인 전용 테이블이라 "공유 테이블의 단일 writer는 Memory" 원칙에 걸리지 않는다 — Health의 `medication_*`과 같은 자리다. 기준은 누가 쓰느냐가 아니라 **누가 읽느냐**다.

어린이집 급식은 전국 단일 API가 없다. **급식표 읽기(사진 OCR · 센터 배포 엑셀)가 유일한 경로**이고, 그 외에는 지역 센터 표준식단을 참고로 쓴다. NEIS 는 쓰지 않는다(10-09).

**급식표에 인쇄된 알레르기 번호는 저장하지 않는다**(10-09). 기관이 아이의 알레르기를 관리하고 보호자에게 미리 알린다. 위험 식품 표시는 메뉴 이름 · 재료로 `filter_food_safety` 가 한다. 영양 계산은 `menu_keys` 만 쓰므로 번호가 없어도 그대로 된다 — 메뉴 이름에서 번호를 떼는 것(`menu_key` 정규화)은 읽는 것이 아니라 지우는 것이다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK, `uuidv7()` |
| `child_id` | uuid | NOT NULL. FK → `child.id`, CASCADE |
| `serve_date` | date | NOT NULL. **미래 날짜를 갖는 유일한 식사 테이블** |
| `meal_slot` | varchar(16) | `lunch` / `snack_am` / `snack_pm`. **기관이 정한 구분이라 그대로 둔다** |
| `menu_keys` | text[] | NOT NULL. 정규화 메뉴. 대체식이면 **교체**한다 |
| `amount_factor` | numeric(3,2) | NOT NULL, default 1.00. "계란말이를 엄청 많이 먹었대" → 큰 값 |
| `amount_known` | boolean | NOT NULL, **default false**. 급식은 보통 얼마나 먹었는지 모른다. 보호자가 말하면 true |
| `caregiver_checked` | boolean | NOT NULL, default false. 대체·제외를 확인했는가 ("없어요"도 true) |
| `origin` | varchar(16) | `ocr`(급식표 읽기 — 사진 · 엑셀) / `center_standard` |
| `source_notice_id` | uuid | nullable |
| `ocr_confidence` | numeric(3,2) | nullable |
| `created_at` · `updated_at` | timestamptz | NOT NULL, default now() |

**규칙**
- `UNIQUE (child_id, serve_date, meal_slot)`
- **오늘 이전 행은 "먹었다"로 간주한다.** 그래서 영양 분석의 입력이 된다. 별도의 상태 컬럼을 두지 않는다 — 날짜가 그 일을 한다.
- **Food는 UPDATE·DELETE만 한다.** 고칠 행이 없으면 `daycare.not_found`("급식 정보를 못 찾았어요")로 끝낸다 — "급식이 이렇게 나왔대"만으로 새 행을 만들지 않는다.
- **결석은 행 삭제다.** "내일모레는 어린이집 빠져서"는 그날 행을 지운다. `menu_keys`를 비우면 "결석"인지 "아직 모름"인지 구별되지 않는다.
- **대체·제외는 `menu_keys`를 교체한다.** 제외 메뉴를 빼고 대체 메뉴를 넣는다. 별도 배열을 두지 않는다 — 두 벌이면 무엇이 실제로 나갔는지가 흐려진다.
- 대체·제외 값은 **보호자가 알려준 것만.** 알레르기 등록 정보로 추정해 채우지 않는다. 보호자의 "우유 알레르기라 두유로 받아요"는 **식단 갱신이지 `health_safety` 등록이 아니다.**
- `origin='center_standard'`는 **아이의 실제 급식이 아니다.** 화면에 참고 식단임을 표시한다.
- 보존: **과거 30일 + 미래 전부.** 급식표는 아직 안 온 날짜라 30일 컷에 걸리면 안 된다.

### 무엇이 이 테이블로 오나

기관 신호(어린이집 · 유치원 · 급식 · 반찬으로 나온 …)가 있으면 여기, 없으면 `observation_food`다. 판정은 **Supervisor가 라우팅에서** 한다.

```
"어린이집에서 계란말이 엄청 많이 먹었대"   → food / daycare_meal   (amount_factor 갱신)
"오늘 급식 대신 두유 받았대"               → food / daycare_meal   (menu_keys 교체)
"내일모레는 어린이집 빠져"                 → food / daycare_meal   (행 삭제)
"집에서 계란말이 먹였어"                   → memory                (observation_food)
```

### 쓰기 tool

`update_daycare_meal` · `delete_daycare_meal` 둘뿐이다. **모델에게 보인다** — 발화에서 메뉴명·양·날짜를 뽑아야 해서다. 대신 복약과 같은 장치를 건다.

- 메뉴명·양은 **발화 원문의 부분 문자열**이어야 한다(span 검증, 코드)
- 날짜 해석은 코드가 한다. 모델은 "내일모레" 같은 표현을 복사만 한다
- **승인 게이트를 두지 않는다.** 급식 메뉴는 알림이 울리지 않고, 틀려도 다시 말하면 된다. 게이트는 캘린더 쓰기와 건강·알레르기 확정 두 곳뿐이다(CLAUDE.md §2)
- 삭제는 되돌릴 수 없지만(OCR 재적재 전까지) 확인 없이 즉시 반영한다. 대상 날짜가 모호하면 **되묻는다**
- 모델은 tool 을 꼭 불러야 끝나서(`required`) 묻는 말 · 가정도 쓰기로 올 수 있다. 원문을 코드가 본다(`daycare_intent`) — 묻는 말이면 쓰지 않고, 가정 · 짐작이거나 빠졌다는 말이 없으면 지우지 않는다. 바뀐 게 없으면 모델은 쓰지 않는 `keep_daycare_meal` 로 끝낸다
- 적재(INSERT)는 백엔드 · OCR, 발화로 고치기(UPDATE · DELETE)는 Food 다. 메뉴는 집합 연산(`menu_keys − removed + added`)이라 같은 갱신을 두 번 해도 결과가 같다. 메뉴를 고치면 `caregiver_checked=true`, 양만 말하면 그대로다. 양은 양 사전(`amount_terms.yaml`)에 있는 말일 때만 바꾼다
- "바꿨어요" 문구는 쓰기가 commit 된 뒤에만 낸다. 넣은 메뉴가 알레르기와 겹치면 저장은 하고 경고를 함께 낸다

---

## 6. `allergen_term` — 19종 코드 사전 (Food · Health 공유)

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `code` | smallint | PK. 1~19 (식약처 표시 대상 순서) |
| `label` | text | NOT NULL. "알류" · "우유" · "메밀" … |
| `aliases` | text[] | NOT NULL. "계란" · "달걀" · "난류" … (F-3 동의어표) |
| `guards` | text[] | NOT NULL, default `'{}'`. 오탐 취소 목록 ("밀크티"는 밀 아님) |
| `source_version` | varchar(16) | 고시 개정 시 버전업 |

매칭은 공통 매처(`app/rules/term_match.py`, Activity와 동일)를 쓴다 — 공백 제거 부분 일치 + guards + **가장 엄한 판정 우선**.

---

## 7. DDL (발췌)

```sql
CREATE TABLE menu_catalog (
    menu_key       text        PRIMARY KEY,
    display_name   text        NOT NULL,
    food_code      text,
    source         varchar(16) NOT NULL
                   -- 레시피(mafra_recipe · mfds_recipe)는 recipe_catalog 에 둔다 (#326 리뷰)
                   CHECK (source IN ('mfds_nutri','center_standard','manual')),
    serving_g      numeric(7,1),
    nutrients      jsonb       NOT NULL DEFAULT '{}',
    ingredients    text[]      NOT NULL DEFAULT '{}',
    allergen_codes smallint[]  NOT NULL DEFAULT '{}',
    food_groups    text[]      NOT NULL DEFAULT '{}',
    resolved       boolean     NOT NULL DEFAULT false,
    source_version varchar(32),
    synced_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (allergen_codes <@ ARRAY(SELECT generate_series(1,19))::smallint[])
);
CREATE INDEX menu_catalog_allergen_idx ON menu_catalog USING gin (allergen_codes);
-- 캐시 미스 저장(백엔드 어댑터): INSERT ... ON CONFLICT (menu_key) DO NOTHING — 권한 방식은 §0 TODO
-- 매일 새벽 배치(FT-19): 새 menu_key 는 넣고, mfds_nutri 행만 새 값으로 바꾼다. manual · center_standard 는 덮지 않는다

CREATE TABLE recipe_catalog (
    source         varchar(16) NOT NULL CHECK (source IN ('mafra_recipe')),
    source_id      text        NOT NULL,
    menu_key       text        NOT NULL,   -- UNIQUE 아님 — 이름이 같은 레시피가 따로 행을 갖는다
    display_name   text        NOT NULL,
    food_code      text,
    serving_g      numeric(7,1),
    nutrients      jsonb       NOT NULL DEFAULT '{}',
    ingredients    text[]      NOT NULL DEFAULT '{}',
    allergen_codes smallint[]  NOT NULL DEFAULT '{}',
    food_groups    text[]      NOT NULL DEFAULT '{}',
    stage_min      varchar(20)
                   CHECK (stage_min IN ('infant_milk','infant_weaning','toddler','preschool')),
    resolved       boolean     NOT NULL DEFAULT false,
    source_version varchar(32),
    synced_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, source_id),
    CHECK (allergen_codes <@ ARRAY(SELECT generate_series(1,19))::smallint[])
);
CREATE INDEX recipe_catalog_menu_key_idx ON recipe_catalog (menu_key);
CREATE INDEX recipe_catalog_stage_idx ON recipe_catalog (stage_min) WHERE resolved;
CREATE INDEX recipe_catalog_allergen_idx ON recipe_catalog USING gin (allergen_codes);
-- 레시피 배치만 쓴다. 다시 받으면 (source, source_id) 로 갱신 — 일부만 받으면 적재하지 않는다(§1-1)

CREATE TABLE menu_alias (
    alias      text        PRIMARY KEY,
    menu_key   text        NOT NULL REFERENCES menu_catalog(menu_key) ON DELETE CASCADE,
    created_by varchar(16) NOT NULL CHECK (created_by IN ('seed','batch','manual'))
);

CREATE TABLE daycare_meal (
    id                uuid        PRIMARY KEY DEFAULT uuidv7(),
    child_id          uuid        NOT NULL REFERENCES child(id) ON DELETE CASCADE,
    serve_date        date        NOT NULL,   -- 미래 허용
    meal_slot         varchar(16) NOT NULL
                      CHECK (meal_slot IN ('lunch','snack_am','snack_pm')),
    menu_keys         text[]      NOT NULL,
    amount_factor     numeric(3,2) NOT NULL DEFAULT 1.00
                      CHECK (amount_factor BETWEEN 0 AND 2),
    amount_known      boolean     NOT NULL DEFAULT false,
    caregiver_checked boolean     NOT NULL DEFAULT false,
    origin            varchar(16) NOT NULL
                      CHECK (origin IN ('ocr','center_standard')),
    source_notice_id  uuid,
    ocr_confidence    numeric(3,2),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (child_id, serve_date, meal_slot)
);
CREATE INDEX daycare_meal_window_idx ON daycare_meal (child_id, serve_date DESC);
CREATE TABLE nutrient_reference (
    id             uuid         PRIMARY KEY DEFAULT uuidv7(),
    age_group      varchar(8)   NOT NULL
                   CHECK (age_group IN ('0-5m','6-11m','1-2y','3-5y')),
    sex            varchar(8)   CHECK (sex IN ('male','female')),
    nutrient       varchar(32)  NOT NULL,
    ref_type       varchar(8)   NOT NULL
                   CHECK (ref_type IN ('EAR','RNI','AI','UL','AMDR')),
    value_min      numeric(10,3) NOT NULL,
    value_max      numeric(10,3),
    unit           varchar(16)  NOT NULL,
    source_version varchar(16)  NOT NULL,
    note           text,
    UNIQUE (age_group, sex, nutrient, ref_type, source_version),
    CHECK (value_max IS NULL OR value_max >= value_min),
    CHECK (ref_type <> 'AMDR' OR unit = '%kcal')
);

```

---

## 8. 공유 테이블 변경 요청 (Food 소유 아님)

| 테이블 | 요청 | 왜 |
| --- | --- | --- |
| `health_safety` | **`child` 생성 시 `kind='allergy'` 19행을 `unknown` 으로 생성** · `state` 에 `none`·`unknown` 추가 — ✅ 확정(09-23) → 10-04 철회 | F-4 닫힘. "아직 안 물어봤다"가 `unknown` 값으로 남아 "없다고 확인함"과 갈린다. `child.allergy_status` 는 뺐다. **10-04 변경** — `unknown` 은 저장하지 않고 '행 없음' 이다. 19행을 미리 깔지 않는다 |
| `suggestion` | **`kind`만** 추가 · `source_refs` jsonb 제거 → `suggestion_evidence` 테이블 | 개인화/일반 표시 · 근거를 행으로 세기 위해 |
| `observation_food` | `amount` 값 집합 확정 | 가중치 사전(F-18)의 입력 |
| `notice` | 테이블 신설 | `daycare_meal.source_notice_id` FK |

소유자는 Memory·앱이다. Food는 읽기만 한다.

---

## 9. 구현 메모

- PK 기본값 `uuidv7()`, enum은 VARCHAR + CHECK — 다른 도메인과 통일
- `menu_catalog`는 아이 데이터가 아니다(개인정보 아님). 해석 실패는 남기지 않고 외부 결과는 이름이 정확히 같을 때만 쌓아서, 보호자 말에서 나온 이름이 그대로 남지 않는다(§1). 영양 합계·구간은 저장하지 않는다(§2)
- 판정 중의 영양 수치는 **내부 전용**이다. 그 task 의 run state 밖으로 나가지 않고 DB·로그에 남기지 않는다
- 카탈로그가 갱신되면 지난 기간의 판정도 새 값으로 바뀐다. 영양 구간은 최신 카탈로그 기준이 맞다고 본다 — 지난 분석 결과를 그대로 다시 보여 줘야 하는 화면이 생기면 다시 본다
- 로그에는 `{kind, id}`만. **메뉴명·알레르기 라벨 원문을 남기지 않는다**(NF-05)

---

## 10. 미결

| # | 쟁점 | 메모 |
| --- | --- | --- |
| FT-1 | `menu_catalog.stage_min` 수동 태깅 범위 | 초기 몇 백 행만. 나머지는 보수적 기본값 |
| FT-2 | ✅ **닫힘(10-05)** — 섭취 파생 테이블의 30일 보관이 동의·보관 정책과 맞는지였다. 테이블을 없애 보관할 파생 값이 없다 (#205) |
| FT-3 | 유아 1인분 보정 계수 (`portion_scale.yaml`) | 섭취기준 기준 체위·1회 섭취참고량으로 산출 |
| FT-4 | 급식 `center_standard` 이용 조건 | 센터별로 다름 |
| FT-5 | ✅ **닫힘(10-09)** — NEIS 알레르기 번호와 19종 코드의 순서가 같은지였다. NEIS 를 쓰지 않고, 급식표의 알레르기 번호도 읽지 않는다(§5) |
| FT-6 | `menu_alias` 초기 사전 규모 | 급식 표기 흔들림이 가장 크다 |
| FT-7 | **보류(10-11)** — 레시피 재료 텍스트 파서 정확도 기준. COOKRCP01 을 도입할 때만 다시 본다. 농식품정보원은 재료가 행 단위라 파서 대신 재료명 정리 표(§1-1)를 쓴다 | 실제 형식(10-06 CSV): `[1인분]` 머리, `●양념장 :` · `고명` 같은 소제목 줄, `재료 무게g(분량)` 쉼표 나열, 줄바꿈이 섞인다 |
| FT-8 | ✅ **닫힘(09-22)** — 급식은 `daycare_meal`로 분리하고 **Food 소유(CRUD)**로 뒀다. 대체·제외는 Food가 tool로 `menu_keys`를 교체한다 |
| FT-9 | **성별 없는 권장 열량 계산식** | 흔히 쓰는 필요추정량 공식 중 유아기부터 성별로 나뉘는 것이 있어 선정 필요 |
| FT-10 | `nutrient_reference.sex` | 성별 미사용 확정 → 조회는 `sex IS NULL` 행만. 영유아 구간에 성별 구분 행이 있는 지표의 처리 |
| FT-11 | **급식 행의 보존 기간** | 과거 30일 + 미래 전부로 시작. 지난 급식표를 언제까지 둘지는 보관 정책 확정 후 |
| FT-12 | ✅ **닫힘(10-05)** — 영양 구간은 저장하지 않고 판정할 때마다 원본에서 다시 판정한다(안 C). 전날 구간은 7일 전부터 하루씩 다시 판정해서 얻는다 — §2-2 (#205) |
| FT-13 | ✅ **닫힘(10-04)** — 캐시 미스는 Food 요청 중 외부 조회, 저장은 백엔드 어댑터가 없을 때만(`ON CONFLICT DO NOTHING`). DB 권한 방식은 명성 님 결정 대기(§0). 멘토 #196 답변 — §1 |
| FT-14 | ✅ **닫힘(10-06)** — 해석 실패를 카탈로그에 남기지 않고, 외부 결과는 이름이 정확히 같을 때만 쌓아서 보호자 말에서 나온 이름이 전역 테이블에 남지 않는다(§1) |
| FT-15 | ✅ **닫힘(10-06)** — 요청 경로에서 `resolved=false` 행을 만들지 않아 정할 값이 없다(§1) |
| FT-16 | ✅ **닫힘(10-06)** — 요청 경로가 실패 행을 남기지 않는다. 배치가 넣은 행은 배치가 다시 싣는다(§1) |
| FT-17 | **레시피 DB 이름이 일상 메뉴명과 거의 안 겹친다** | 10-06 받은 CSV(1,156건)에 된장찌개 · 김치찌개 · 미역국 · 김치볶음밥과 정확히 같은 이름이 없다 — 부대된장찌개 · 황태미역국 같은 변형 레시피 이름이다. 메뉴명이 정확히 맞을 때만 재료를 가져오면 일상 메뉴 대부분이 "재료 모름" 이 되어 후보 풀에서 빠지고, 재료와 영양이 둘 다 있는 행도 적을 수 있다. 부분 일치로 붙이면 다른 메뉴가 된다(된장찌개 → 부대된장찌개 — 추정 금지와 부딪힌다). **10-11 측정** — 메뉴명 30개([`Food_Tool_명세.md`](Food_Tool_명세.md) §0-7 의 점검 목록) 중 식품영양성분DB 21개 해석, 레시피 이름과 정확히 같은 것은 농식품정보원 16개 · COOKRCP01 4개(잡채 · 닭곰탕 · 호박죽 · 닭강정). 그래서 기록 · 급식 메뉴 해석은 영양DB 로 하고, 레시피는 후보 풀의 재료로만 쓴다 — 후보 풀은 선호로 검색하지 않고 카탈로그 전체에서 거르므로 레시피 이름이 일상 메뉴명과 달라도 추천에는 문제가 작다 |
| FT-18 | **재료명 정리 표 검수** (§1-1) | 696개 항목 전부 `draft` 라 지금은 후보 풀에 들어가는 레시피가 0개다. 담당 · 순서(많이 나온 이름부터)를 정한다. 저장소에 올리는 것은 농식품정보원 이용 조건 답(상업 이용 · 변경 가능)을 받아 정했다. 초안에서 볼 것 — 아황산류(13) 후보가 없음(와인 · 건포도 · 곶감), 이름에 성분이 안 드러나는 가공품(우스터소스 · 두반장 · 액젓)은 코드가 비어 있음, 부위명(안심 · 선지 · 곱창)은 안전 사전이 소 · 돼지 둘 다로 막아서 표에서 좁힐 수 없다(10-11, #326 리뷰). **검수 확정(#459 · #420 PR)** — 696항목 전부 사람이 봤다. 초안이 unknown 후보로 표시한 28항목(육수 · 상표 양념 · 소스)은 unknown — 그 재료가 든 레시피 57개는 후보 풀에서 빠진다 |
| FT-19 | ✅ **정함(10-11, #326 리뷰)** — 요청 중 영양DB 조회가 20초 예산에 안 맞는다 (§1). **매일 새벽 배치로 품목대표 전체를 받고, 요청 중 조회는 배치 뒤 새로 생긴 행을 위해 남긴다**(값 5회 · 8초 그대로). 배치 인프라(크론잡 · 관리자 API)는 BE 가 맡는다. 품목대표 전체 받기는 `MfdsNutriClient.representative_rows`, 적재는 `scripts/load_food_catalog.py --nutrition` 이다(#442). 크론 · 관리자 API 는 이 스크립트를 **프로세스로 실행**한다 — 행 만들기가 `app/agents/food/` 에 있어서 `run()` 을 workers · api 에서 import 하면 레이어 경계를 넘는다(import 로 부르려면 행 만들기를 core 로 내려야 하고 두 Owner 협의다). 배치와 요청 중 조회는 같은 행 고르기 규칙(`pick_nutrition`)을 쓴다 — 배치는 `app.core.nutrition_pick.nutrition_facts_by_key(rows)` 를 직접 부른다(✅ 정함, 10-13 #321 파트 2 — 위 §1). 레시피는 `recipe_catalog` 로 떼어 아래의 "합의 3 과 같이 본다" 는 없어졌다. 아래는 정하기 전 기록 | 이름 검색이 느리다 — 하나씩 4.5 ~ 7.8초, 5개 동시면 중간 6.9초 · 최대 15.5초(10-11 오후 재측정, 처음 쟀을 때는 3 ~ 4초). 지금 값(5개 동시 · 8초)으로 메뉴명 30개(Food_Tool_명세 §0-7)를 돌리니 데이터로는 21개를 찾을 수 있는데 15개만 찾았다. 17시에 라이브 eval(`test_menu_resolution`)로 판정을 거듭해 보니 시간 초과는 없었지만, 판정 한 번에 5개씩이라 21개를 다 찾는 데 판정 5번이 걸렸다 — 시간 초과가 없어도 처음 보는 이름이 많으면 몇 번의 판정 동안 "확인 못 함" 이 남는다. 이름 없이 품목대표만 받으면 빠르다 — 100행 1 ~ 4초 · 한 번에 최대 500행 · 전체 8,807행이라 18번이면 다 받는다. 요청 중 조회가 고를 수 있는 행이 전부 이 안에 있으므로, 배치로 다 받아 두면 요청 중에는 카탈로그만 읽어도 해석률이 같다 — §1 에서 안 B 를 접은 이유(처음 보는 메뉴)는 배치 뒤 영양DB 에 새로 생긴 행에만 남는다. 정할 것 — 요청 중 조회를 남길지(영양DB 갱신분 대비) · 배치 주기 · 누가 만드는지. 미리 받은 행이 레시피 배치(§1-1)보다 먼저 `menu_key` 를 차지하므로 #321 합의 3(있는 행 채우기)과 같이 본다 |
