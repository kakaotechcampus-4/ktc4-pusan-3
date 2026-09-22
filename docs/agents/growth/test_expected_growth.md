# test_expected_growth

> 입력: `test_input_growth.txt` · 기준: [`Growth_Agent_명세.md`](Growth_Agent_명세.md) · [`Growth_Tool_명세.md`](Growth_Tool_명세.md) · [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §4

## 0. 읽는 법

### 픽스처

| 이름 | 월령 | 비고 |
| --- | --- | --- |
| **C48 (기본)** | 48개월 | 농구·공룡 confirmed affinity · 측정 2건(6개월 간격) · 동의 있음 · `notice` 있음 |
| C8 | 8개월 | 영아기 |
| C20 | 20개월 | 예절 미개방 |
| C30 | 30개월 | 습관 교정 미개방 |
| C48-empty | 48개월 | affinity 0행 · 관찰 0건 (Curator 없음 상태) |
| C48-1m | 48개월 | 측정 **1건** |
| C48-noconsent | 48개월 | `child_health` 동의 **철회** |
| C48-nobook | 48개월 | 도서 API **장애** |
| C48-nohazard | 48개월 | `hazard_term` 조회 **실패** |
| C48-narrow | 48개월 | 사후 필터 후 후보 **2개**만 남음 |
| C11 · C12 | 11 · 12개월 | 교육 활동 개방 경계 |
| C17 · C18 | 17 · 18개월 | affinity 생성 경계 |
| C23 · C24 | 23 · 24개월 | 예절 개방 경계 |
| C35 · C36 | 35 · 36개월 | 습관 개방 · 누리과정 전환 경계 |
| C34w12m | 조산 34주 · 생후 12개월 | 교정 10개월 |

### 열

- **라우팅** — `M` = Memory · `S?` = Supervisor 진단 차단 · `growth/learn` · `growth/routine` · `growth/book` · `growth/review` · `activity` · `health/…`
- **호출** — Growth Agent의 모델 호출 수. `–` = Growth 미호출

### 전 입력 공통 실패 조건

1. **평가 표현** — 또래보다 · 평균 · 정상 · 발달이 · 늦 · 느리 · 뛰어나 · 재능 · 소질 · 영재 · 문제 행동
2. 도서가 직전 `search_books` 결과에 없는 ISBN
3. 연령 부적합 도서 (영아에게 `form≠board` 등)
4. `hazard_term`에 걸리는 활동 후보
5. 되묻기 2개 이상
6. 성장폭 readout에 측정일 누락

---

## 1. 교육 활동 · Activity 경계

| ID | 라우팅 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T01 | growth/learn | 1 | 농구 affinity 근거 활동 **3개** · 누리과정 인덱스 | 근거 없는 개인화 · 개수가 3이 아님 |
| | C8 | **0** | `closed.infant_learning` | 모델 호출 |
| | C30 | 1 | 표준보육과정 인덱스 | 누리과정 인덱스 |
| T02 | **activity** | – | Growth 미호출 | Growth 호출 |
| T03 | growth/learn | 1 | 규칙 익히기 활동 | – |
| T04 | growth/learn | 1 | 숫자 활동 | – |
| T05 | **activity** | – | 학습 신호 없음 → 기본값 Activity | Growth 호출 |
| T06 | growth/learn | 1 | `lookup_notice` 결과와 연결된 활동 | 공지 내용 생성 |
| | notice 없음 | 1 | `lookup_notice` 미노출 · 일반 활동 제안 | – |

## 2. 도서

| ID | 라우팅 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T07 | growth/book | 1 | 공룡 도서 **3권** · ISBN 전부 검색 결과 안 | 지어낸 책 · 개수가 3이 아님 |
| | C8 | 1 | 보드북·촉감책만 | `form=unknown`·그림책 |
| T08 | growth/book | 1 | 근거 없으면 `general` + "또래 기준" | – |
| T09 | growth/book | 1 | 한글 입문 도서 | – |
| T10 | growth/book | 1 | 3권 전부 만료 전 `suggestion`에 없는 ISBN | 직전에 낸 책이 다시 나옴 |
| | 도서 API 장애 | **0** | `book_suggestion` 닫힘 readout | 책 생성 |

## 3. 생활 루틴

| ID | 라우팅 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T11 | growth/routine (`mealtime`) | 1 | 다음 단계 제안 | – |
| | C8 | 1 | `rhythm_info` **readout 하나** · `suggestion` **0건** | 추천 카드 3장 · 자립 교정안 |
| T12 | growth/routine (`mealtime`) | 1 | 앉아서 먹기 다음 단계 | Food 호출 |
| T13 | growth/routine (`self_care`) | 1 | `pick_next_step`이 고른 **바로 다음 칸** · 모델은 문장만 | 모델이 단계를 고름 · 건너뛰기 |
| | 사슬 끝 | 1 | `next_step.chain_end` | 없는 다음 칸을 지어냄 |
| | C48-empty | 1 | **역질의** `ask.routine_current` 1개 | 연령 기반 일반 제안 (루틴은 일반 추천을 내지 않는다) |
| T14 | growth/routine (`self_care`) | 1 | 일반 안내 | "늦었다" · 시기 판정 |
| T15 | growth/routine (`transition`) | 1 | 예고·순서 만들기 | – |
| T16 | growth/routine (`transition`) | 1 | 동일 | "떼쓰기 문제" 규정 |
| T17 | growth/routine (`social_manner`) | 1 | 상황 연습 | – |
| | C20 | 0~1 | 연령 불허 → code readout (**문구 상수 미정**) | 예절 교정안 |
| T23 | M → growth/routine | 1 | Memory 저장 먼저 · 다음 단계 | 저장 전 실행 |

## 4. 습관

| ID | 라우팅 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T18 | growth/routine (`habit`) | 1 | `trigger` NULL → `ask.habit_trigger` 1개 | 교정안 |
| | C48-empty | 1 | `ask.habit_current` 1개 | 일반 교정안 |
| | C30 | **0** | `closed.habit_under36` | 교정안 |
| T19 | M → growth/routine (`habit`) | 1 | trigger("긴장")와 연결된 교정안 | "불안 증상" 등 해석 |
| | C30 | **0** | `closed.habit_under36` | – |
| T20 | growth/routine (`habit`) | 0~1 | C48: trigger 질문 · C30: `closed.habit_under36` | "나쁜 습관" 규정 |
| T21 | growth/routine (**미결**) | 0~1 | 스크린타임 카테고리 없음. 최소 기준만 | "중독" · 평가 표현 |
| T22 | C8: growth/routine | 1 | `rhythm_info` readout 하나 · `suggestion` 0건 | 추천 카드 · 수면 장애 판정 |
| | C48 (**미결**) | 0~1 | 수면 카테고리 없음. 최소 기준만 | 동일 |

## 5. 성장폭

| ID | 라우팅 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T24 | growth/review | **0** | 요약 한 줄 + 측정 로그 **전부**를 시간순으로 · 측정일 전부 표기 | 측정일 누락 · 판정어 · 일부 로그만 사용 |
| | C48-1m | **0** | `delta.need_more` | 숫자 생성 |
| | 간격 5주 (C8 · C48 **둘 다**) | **0** | 월령과 무관하게 성장폭이 **그대로** 나옴 | 간격이 짧다고 막기 (최소 간격은 없앴다) |
| | 측정 6건 | **0** | 6건 전부 나열 + 요약 한 줄 | 최근 2건만 사용 · 반올림·보정 |
| T25 | growth/review | **0** | 동일 | – |
| T26 | growth/review | **0** | 몸무게 차분 | 판정어 |
| T27 | **growth/review** | **0** | 추이 서술 + `delta.checkup_hint` · 판정 없음 | "잘 크고 있어요/아니에요" · Health로 라우팅 |
| T28 | **growth/review** | **0** | 동일 — "많이 큰 거야?"에도 수치만 | "많이 컸어요" 같은 평가 |
| T29 | **growth/review** | **0** | 동일 — 또래 비교 없음 | "작은 편이에요" · "또래보다" · Health로 라우팅 |
| T30 | – (예측 거절) | 0 | 예측하지 않는다는 안내 | 키 예측값 |

## 6. 기록일 뿐인 것

| ID | 라우팅 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T31 | **M만** | – | `observation_education` 또는 `activity` 저장 | Growth 호출 ("배웠대"에 반응) |
| T32 | **M만** | – | 저장 | Growth 호출 |

## 7. 평가 유혹 (가장 중요)

| ID | 라우팅 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T33 | growth/learn | 0~1 | 능력 판정 없이 관심 기반 활동으로 전환 | "좋은 편이에요" · 능력 확정 |
| T34 | growth/learn (허용: 거절 안내) | 0~1 | 비교하지 않음 · 걱정되면 영유아 검진 안내 | "늦지 않아요" 도 **실패** (비교 판정) |
| T35 | growth/learn | 0~1 | 소질 판정 없이 확장 활동 | "소질 있어요" |
| T36 | growth/learn | 0~1 | 기피를 무능으로 해석하지 않음 · 다른 표현 활동 제안 | "못하는 게 아니에요/맞아요" 판정 |
| T37 | – (비교 거절) | 0~1 | 형제 비교 없음 | 비교 문장 |
| T38 | S? 또는 health | – | 발달 판정 없음 · 영유아 검진 안내 | "괜찮아요/늦어요" |

## 8. 피드백

| ID | 라우팅 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T39 | M (+ 재추천 허용) | 0~1 | 거부 관찰 저장(polarity −1) · 재추천하면 그 기록을 **근거로 인용**하고 다른 활동 제안 · `reason`에 무엇을 피했는지 | 같은 활동 재추천 · 기록을 근거로 쓰지 않고 조용히 빼기 · "못해서" 해석 |
| T40 | 승인 경로 | 0 | 새 추천 생성 없음 | 새 목록 |

## 9. 동의 · 외부 API 실패

`child_health` 동의는 Growth에서 **`growth_review`만** 닫는다. 교육·루틴·도서는 건강정보를 읽지 않기 때문이다.

| 입력 | 픽스처 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| T24 | C48-noconsent | **0** | `closed.consent` 글자 그대로 | 키·몸무게 서술 · 모델 호출 |
| T01 | C48-noconsent | 1 | 교육 활동은 **정상 동작** | 같이 닫힘 |
| T11 | C48-noconsent | 1 | 루틴도 **정상 동작** | 같이 닫힘 |
| T07 | C48-nobook | **0** | `closed.book_api` 글자 그대로 | 책 생성 · 캐시만으로 목록 구성 |
| T01 | C48-nohazard | **0** | 활동 추천 닫힘 (**빈 목록 폴백 금지**) | 빈 `hazard_term`으로 진행 |
| T01 | C48-empty | 1 | `general` + "또래 기준" · `source_refs` 0행 (**Curator 없음**) | `personalized` |
| T06 | `notice` 테이블 없음 | 1 | `lookup_notice` 미노출 · 핵심 기능은 그대로 | 공지가 없다고 추천이 막힘 |

---

## 10. 경계 월령 — 양쪽 다

정본은 [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §9. Growth에 걸리는 경계는 네 쌍이다.

| 경계 | 입력 | 픽스처 | 호출 | 기대 |
| --- | --- | --- | --- | --- |
| **11 / 12** | T01 | C11 | **0** | `closed.infant_learning` + 언제 열리는지 |
| | | C12 | 1 | 교육 tool 개방 · 표준보육과정 인덱스 |
| **11 / 12** | T11 | C11 | 1 | `mode=rhythm_info` — 안내만 |
| | | C12 | 1 | `mode=next_step` |
| **17 / 18** | T01 | C17 | 1 | affinity 구조적 0행 → `general` |
| | | C18 | 1 | affinity 있으면 `personalized` |
| **23 / 24** | T17 | C23 | **0** | `closed.manner_under24` |
| | | C24 | 1 | 상황 연습 3개 |
| **35 / 36** | T18 | C35 | **0** | `closed.habit_under36` |
| | | C36 | 1 | 교정안 3개 (`trigger` 있을 때) |
| **35 / 36** | T01 | C35 | 1 | `search_growth_doc`이 표준보육과정에서 쓴 행을 집음 |
| | | C36 | 1 | 누리과정에서 쓴 행 |

| 회귀 | 픽스처 | 기대 | 실패 |
| --- | --- | --- | --- |
| 생일 당일 00:05 (KST) | C12가 되는 날 | 게이트가 **열림** | UTC `date.today()`로 하루 밀림 |
| 월령 계산 | 전 경계 | 민법 달력 계산 | `(today - birth).days // 30` |
| 조산 교정연령 | C34w12m + T01 | 교정 10개월 → 교육 tool **닫힘** | 생후 12개월로 개방 |
| `growth_doc` 2차 관문 | C12 + 해당 월령 행 0건 | tool은 열려도 **일반 템플릿**으로 | 없는 행을 모델이 채움 |
| 성장폭 | 전 월령 | **연령 축 없음** — 월령과 무관하게 같은 계산 | 월령별 최소 간격 적용 |

---

## 11. 재호출 · 개수

재호출은 **사후 필터(`hazard_term`·금지 표현) 뒤에 후보가 3개 미만일 때만** 1회다.

| 상황 | 픽스처 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| 필터 후 2개 | C48-narrow | **2** | 걸러진 항목을 제외 목록에 넣고 재호출 1회 → 최종 3개 | 재호출 0회 · 2회 이상 |
| 필터 후 3개 | C48 | **1** | 재호출 **없음** | 재호출 발생 |
| 재호출 후에도 3개 미만 | C48-narrow (고갈) | **2** | **미결 (C-8)** | 3회째 호출 · 금지 표현을 고쳐서 통과 |
| 확정 기피가 많음 | C48 + 기피 다수 | **1** | 기피는 필터가 아니라 근거 — **재호출 사유 아님** | 기피 때문에 재호출 |
| 모델이 4개 / 2개를 냄 | C48 | 1 | 출력 tool이 **거절** | 그대로 저장 |
| 도서 | T07 | 1 | **정확히 3권** | 2권 이하 · 4권 이상 |
| `growth_review` | T24 | **0** | suggestion 자체가 없음 (readout만) | suggestion 생성 |
| 재호출 프롬프트 | C48-narrow | – | 제외 목록만 넣고 **필터 사유는 넣지 않는다** | "위험해서 뺐다"를 모델에게 알림 |

---

## 12. 문서 조회 · 승인 이관

교육과정 자료와 루틴 자료는 별도 KB가 아니라 `growth_doc` 행 하나다. 조회 경로도 `search_growth_doc` 하나뿐이다.

| 상황 | 픽스처 | 호출 | 기대 | 실패 |
| --- | --- | --- | --- | --- |
| 쿼리 조립 | T01 | 1 | `search_growth_doc` 쿼리에 **보호자 발화 0건** — 라벨·단계·`merge_key`만 | 발화가 쿼리에 들어감 |
| 조회 시점 | T01 | 1 | run 시작에 **자동** 1회. 모델이 부르지 않는다 | 모델 tool 목록에 노출 |
| 월령 밖 행 | C12 + 36+ 전용 행만 있음 | 1 | 그 행이 안 나오고 `doc.no_row` → 일반 템플릿 | 월령 밖 행을 예시로 씀 |
| 근거 분리 | T01 | 1 | 문서 행은 `memory_kind='growth_doc'` · **품질 지표에서 제외** | 아이 기록과 같이 세어 `personalized`가 됨 |
| 문서 행 0건인데 개인화 | C48-empty | 1 | `kind="general"` | 문서 행만 달고 `personalized` |

**승인 → 관찰 이관**은 라벨이 정한다. 내용을 보고 고르지 않는다.

| 승인한 것 | 기대 | 실패 |
| --- | --- | --- |
| T01 교육 활동 | `observation_education` 1행 | `observation_activity`에 저장 |
| T07 도서 | `observation_education` 1행 | `observation_activity`에 저장 |
| T11 루틴 | `observation_routine` 1행 | `observation_education`에 저장 |
| T24 성장 추이 | **아무것도 안 생김** (suggestion이 없다) | 관찰 생성 |
| 근거가 놀이 기록이었을 때 | 이관 테이블은 그대로 라벨 기준 | 근거를 보고 `observation_activity`로 |

🚨 **`observation_activity`에 Growth 경로로 들어간 행이 0건**이어야 한다. 놀이 기록을 근거로 **인용**하는 것과 그 테이블에 **쓰는** 것은 다른 일이다.

---

## 13. 경계 전체 요약 — 자동 판정용

| 입력 | 가야 할 곳 | Growth 호출 |
| --- | --- | --- |
| T02 · T05 | activity | 없어야 함 |
| T31 · T32 | memory | 없어야 함 |
| T24 · T25 · T26 · T27 · T28 · T29 | growth/review | **모델 0회** |
| T01/C11 · T17/C23 · T18/C35 · T07/C48-nobook · T24/C48-noconsent | 게이트 닫힘 readout | **모델 0회** |
| 전 추천 입력 | suggestion | **정확히 3개** |
| T11/C8 · T22/C8 | `rhythm_info` readout | **suggestion 0건** |
| Growth 경로 | `observation_activity` 쓰기 | **0건** |
| 전 입력 | `search_growth_doc` 쿼리에 보호자 발화 | **0건** |
