# 급식표 구조화 파이프라인 — 설계 결정

> 상태: 초안 (2026-09-08) · 담당: 박재형 · 관련: [CLAUDE.md](../../CLAUDE.md) §2 §3 · [테크스펙 §7](../overview/tech-spec.md)

이번 주 범위: **급식표 이미지 → 구조화 JSON → DB 저장** 한 방향 파이프라인. (이슈 #16)

---

## 1. 핵심 결정 3가지

### ① 급식표는 아이 데이터가 아니라 기관 데이터다

같은 어린이집에 다니는 아이 30명이 **똑같은 급식표 한 장**을 본다.

| 설계 | OCR 호출 | 저장 |
| --- | --- | --- |
| 아이 단위 (`child_id`) | 30회 | 동일 데이터 30벌 |
| **기관 단위 (`institution_id`)** ✅ | **1회** | **1벌, 30명이 공유** |

→ 모델 선택보다 이 설계 하나가 비용을 30배 가른다.
→ 검수도 한 번만 하면 30명이 검수된 데이터를 본다.
→ 조회는 `WHERE institution_id = ? AND served_on = ?`. `child_id`는 이 경로에 등장하지 않는다.

### ② 알레르기 번호는 LLM이 만지지 않는다

CLAUDE.md §3 — *"100% 맞아야 하나? 그러면 코드."*

```
LLM  →  { name, raw }              자연어 이해 (표 읽기, 메뉴명 분리)
규칙  →  allergen_codes: [1,5,6,16]  정규식 파싱 (100% 결정적)
```

괄호 안 숫자와 원문자를 뽑는 것은 정규식으로 100% 되는 일이다.
LLM에 맡기면 정확도가 떨어지고, **그 오차가 알레르기 아동에게 잘못된 메뉴를 추천하는 경로**가 된다.
`raw`를 보존하므로 사후 검수도 가능하다.

### ③ 못 읽은 것은 빈칸으로 채우지 않는다

CLAUDE.md §10 — *"미정을 기본값으로 채우고 넘어가지 말 것."*

`unparsed`가 비어 있지 않으면 **사람 검수 플래그**. 흐릿해서 못 읽은 칸을 그럴듯하게 지어내는 것이 이 파이프라인의 최악 실패 모드다.

---

## 2. 알레르기 표기 (실측 확인)

식품위생법 표시 대상 **19종**:

```
1.난류  2.우유  3.메밀  4.땅콩  5.대두  6.밀  7.고등어  8.게  9.새우  10.돼지고기
11.복숭아  12.토마토  13.아황산류  14.호두  15.닭고기  16.쇠고기  17.오징어  18.조개류  19.잣
```

**표기 방식이 두 가지로 섞여 있다. 파서는 둘 다 받아야 한다.**

| 방식 | 예시 |
| --- | --- |
| 괄호 + 쉼표 | `쇠고기무국 (1,5,6,16)` |
| 원문자 | `돈까스 ①②⑤⑬` |

```python
# app/rules/allergen.py
CIRCLED = {chr(0x245F + i): i for i in range(1, 21)}   # ① ~ ⑳

def parse_allergens(raw: str) -> list[int]:
    """메뉴 원문에서 알레르기 번호만 추출. LLM 개입 없음."""
    ...
```

---

## 3. 출력 JSON 스키마

급식표 1장 = 한 달치.

```json
{
  "institution_name": "○○어린이집",
  "year_month": "2026-09",
  "days": [
    {
      "day": 1,
      "meals": [
        {
          "meal_type": "lunch",
          "items": [
            { "name": "차수수밥", "raw": "차수수밥" },
            { "name": "돈까스",   "raw": "돈까스 (1,2,5,6,10,15)" }
          ]
        }
      ]
    }
  ],
  "unparsed": [],
  "notes": null
}
```

| 필드 | 규칙 |
| --- | --- |
| `day` | **일자만.** `2026-09` + `1` → `2026-09-01` 변환은 코드가 한다 (§3 날짜 계산은 규칙) |
| `raw` | OCR 원문 보존. 검수와 알레르기 파싱의 유일한 근거 |
| `allergen_codes` | **JSON에 없다.** 규칙이 `raw`에서 뽑아 DB에 넣는다 |
| `meal_type` | `lunch` / `snack_am` / `snack_pm` / `breakfast` — 실제 급식표 확인 후 확정 필요 |
| `unparsed` | 비어 있지 않으면 `meal_plan.status = 'draft'` 유지, 사람 검수 대기 |

---

## 4. 테이블 (데이터 모델 담당 전달용)

테크스펙 §7의 `외부소스 테이블 (미정, 기관 알림/급식표)` 자리를 채운다.

```
institution                          기관
  id, name, kind(daycare|kindergarten|school), created_at

meal_plan                            급식표 1장 = 1행
  id, institution_id (FK), year_month,
  source_image_url, raw_json (jsonb),          ← LLM 출력 원본 보존
  status (draft|verified), created_at

meal_plan_item                       메뉴 1개 = 1행  ★ Food Agent 조회 단위
  id, meal_plan_id (FK), institution_id (FK),
  served_on (date), meal_type,
  name, allergen_codes (smallint[]), raw_text
  INDEX (institution_id, served_on)

child.institution_id                 아이 ↔ 기관 연결
```

**`child_id`가 어디에도 없다.** 이것이 ①번 결정의 구현이다.

---

## 5. 모델 선택은 인터페이스로 미룬다

```
app/providers/meal_ocr/
├── base.py        extract(image_bytes) -> MealPlanJSON
└── <구현체>.py     지금은 1개
```

인터페이스만 고정하면 다음 주 비교 후 모델을 바꿔도 위쪽 코드는 변경 없음.

### 냅킨 계산 (테크스펙 §10-3, 이시하 담당)

급식표는 **기관당 월 1장**이다. 기관 단위 캐싱까지 적용하면 호출량이 사용자 수와 무관해진다.
→ **비용은 이 파이프라인의 결정 변수가 아니다.** 정확도로 고르면 된다.
→ 실제 비용은 매일 발생하는 발화 처리(Supervisor 1 + 도메인 Agent 최대 2)에서 나온다.

---

## 6. 범위

**이번 주**
- [ ] 이미지 → JSON → DB 저장 한 방향 파이프라인
- [ ] `app/rules/allergen.py` (순수 함수)
- [ ] 테스트: 스키마 검증 + 알레르기 파싱 (괄호/원문자 양쪽)

**다음 주로 미룸**
- 모델 비교 (인터페이스로 격리해둠)
- 검수 UI, 재분석/힌트
- 다기관 동시 지원

---

## 7. 열린 질문

- [ ] `meal_type` 값 집합 — 실제 어린이집 급식표 확인 후 확정
- [ ] 검수 전(`draft`) 급식표를 Food Agent가 근거로 쓸 수 있는가 → **불가로 제안** (§2 근거 신뢰성)
- [ ] `source_image_url` 원본 보관 기간 — 테크스펙 리스크 ⑥(삭제 범위)과 연결
- [ ] 기관 정보를 어떻게 등록하는가 (온보딩에서 선택 / 첫 업로드 시 생성)

---

## 8. 보안 주의

레포가 **public**이다 (CLAUDE.md §9).
실제 급식표에는 **기관명이 박혀 있다.** 수집한 원본을 레포에 커밋하지 않는다.
테스트 fixture는 기관명을 지운 1장 또는 합성 급식표를 사용한다.
