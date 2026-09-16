# 급식표 구조화 파이프라인 v1

문서 목적: 급식표 입력(사진 · 엑셀 · 한글)이 모이는 공통 JSON 과 읽기 인터페이스를 고정하고, 저장 이전 단계의 결정을 남긴다.

기준 브랜치: `feat/be-16-meal-plan-schema`
작성일: 2026-09-16
담당: 박재형
선행 문서: [`docs/overview/tech-spec.md`](../overview/tech-spec.md#7-데이터-모델) (§7 외부소스 테이블 자리) · 루트 [`CLAUDE.md`](../../CLAUDE.md) §2 §3

이슈 #16 (파이프라인) · #63 (엑셀 · 한글 입력). 2026-09-08 초안(#21)을 다섯 PR 로 나누며 다시 썼다.

---

## 1. 흐름

```
사진(OCR)  ┐
엑셀       ├─→  MealPlanJSON  ─→  parse_allergens(raw)  ─→  저장 (테이블 미정, 회의안건 1-2)
한글       ┘     §3                app/rules/allergen.py
```

입력이 무엇이든 **읽기 코드는 `MealPlanJSON` 까지만** 낸다. 그 뒤(알레르기 번호 · 저장 · 검수)는 입력원과 무관하다.

| 입력 | 어디서 오나 | 읽는 방법 | 못 읽는 칸 |
| --- | --- | --- | --- |
| `image` | 보호자가 찍은 사진 | OCR (Gemini, 실측 99~100%) | 생긴다 → 검수 |
| `xlsx` | 급식관리지원센터 배포 엑셀 (6종 시트) | 셀을 그대로 읽는다 (openpyxl) → [`xlsx.py`](../../apps/api/app/providers/meal_plan/xlsx.py) | 거의 없다 (실측: 6시트 · 868항목 · 0) |
| `hwp` | 센터 배포 한글 | 표 안 텍스트를 읽는다 (라이브러리 미정, §6) | 거의 없다 |

센터 배포본은 표 안이 전부 텍스트라 OCR 이 필요 없다 (실측: 원문자 769개가 텍스트로 들어 있음).

**실제 대상 기관은 부산대 병설유치원이고, 급식표를 아직 받지 못했다.** 위 표의 `xlsx` · `hwp` 와 §3 의 `meal_type` 은 급식관리지원센터 배포본으로 실측한 것이다. 유치원 급식표 형식을 확인하면 입력 종류와 끼니 칸을 조정한다.

---

## 2. 결정

### ① 저장 기준(아이 / 기관)은 미정 — 우선 아이 기준으로 구현한다

같은 급식표를 보는 아이가 많아 기관 기준이 호출 · 저장 · 검수 모두 유리하지만, 기관 정보를 다루는 것이 개인정보 범위(루트 §2 "프로필 질문을 늘리지 말 것")와 부딪힌다. 결정은 회의안건 1-2. 그 전까지는 아이 기준으로 만들고, JSON 의 `institution_name` 은 급식표에 찍혀 있을 때만 채우는 **선택값**으로 둔다 — 기관 기준으로 바꿀 때 쓸 수 있게 버리지 않는다.

### ② 알레르기 번호는 모델이 만지지 않는다

루트 §3 — *"100% 맞아야 하나? 그러면 코드."*

```
읽기 코드  →  { name, raw }              표 읽기 · 메뉴명 분리
규칙      →  codes: (1, 5, 6, 16)       app/rules/allergen.py — 정규식, 100% 결정적
```

`allergen_codes` 는 JSON 에 **없다.** 모델이 그 키를 내면 검증에서 거절된다 (`extra="forbid"`). `raw` 를 보존하므로 사후 검수가 가능하다. 19종 밖 번호는 `unknown` 으로 따로 돌려준다 (#64).

### ③ 못 읽은 칸은 지어내지 않고, 그 칸만 비운다

`unparsed` 가 비어 있지 않으면 사람이 봐야 하는 급식표다. 검수는 **사진을 올린 보호자가 그 자리에서** 한다 (종이 원본을 가진 사람이 그 보호자뿐이다). 보호자가 확인을 안 눌러도 급식표 전체를 버리지 않고 **못 읽은 칸만 "확인 안 됨"** 으로 두고 나머지는 쓴다 — 급식표는 아이 알레르기 정보 자체가 아니라 "그날 뭐 나오나" 참고 자료라 한 칸 때문에 한 달치를 버리는 것은 과하다. "확인 안 됨" 은 빈칸(메뉴 없음)과 구분되는 별도 상태여야 하며, 이 표시는 저장 단계(칸 단위)의 몫이다.

이 확인은 되돌릴 수 없는 행동의 승인이 아니라 "읽은 게 맞나" 확인이라, 루트 §2 의 승인 게이트 2곳에 더하는 것이 아니다.

---

## 3. `MealPlanJSON` — 정본은 [`apps/api/app/providers/meal_plan/schema.py`](../../apps/api/app/providers/meal_plan/schema.py)

급식표 1장 = 한 달치.

```json
{
  "source": "xlsx",
  "year_month": "2026-09",
  "institution_name": null,
  "days": [
    { "day": 1, "meals": [], "note": "휴일" },
    {
      "day": 2,
      "meals": [
        { "meal_type": "snack_am", "items": [ { "name": "달걀채소죽", "raw": "달걀채소죽:①" } ] },
        { "meal_type": "lunch",    "items": [ { "name": "보리밥",     "raw": "보리밥" },
                                              { "name": "애호박국",   "raw": "애호박국:⑤⑥" } ] }
      ]
    }
  ],
  "unparsed": [ { "day": 4, "meal_type": "snack_pm", "raw": "빵(찐빵5,6,10,16,18)", "why": "번호가 흐림" } ],
  "notes": null
}
```

| 필드 | 규칙 |
| --- | --- |
| `source` | `image` / `xlsx` / `hwp`. **읽기 코드가 채운다.** 뒤 단계가 검수 필요 여부를 판단하는 근거 |
| `year_month` | `YYYY-MM` 만 |
| `institution_name` | 선택값. 급식표에 찍혀 있으면 그대로, 없으면 `null` (결정 ①) |
| `days[].day` | 1~31 이고 **그 달에 있는 날**이어야 한다. 같은 날이 두 번 나오면 거절. 날짜 계산은 `date_of(day)` |
| `meal_type` | `breakfast` / `snack_am` / `lunch` / `snack_pm` / `dinner`. 센터 급식표 실측(오전간식 · 점심 · 오후간식, 석식형은 저녁) |
| `items[].raw` | 원문 그대로. 번호 · 기호 보존. 알레르기 파서의 유일한 입력 |
| `items[].name` | `raw` 에서 번호 · 기호를 뺀 메뉴 이름 |
| `unparsed[]` | 못 읽은 칸. `why` 필수. 지어내지 않고 여기로 |
| `allergen_codes` | **없다.** 결정 ② |

---

## 4. 읽기 인터페이스 — [`base.py`](../../apps/api/app/providers/meal_plan/base.py)

```python
class MealPlanReader(Protocol):
    source: MealPlanSource
    def read(self, data: bytes, *, mime_type: str) -> MealPlanJSON: ...
```

```
app/providers/meal_plan/
├── schema.py      MealPlanJSON · MealPlanSource · MealType
├── base.py        MealPlanReader
├── image_ocr.py   (4/5) Gemini OCR
├── xlsx.py        (3/5 · #63) 센터 엑셀 — XlsxMealPlanReader(sheet=None) · list_menu_sheets()
└── hwp.py         (#63) 센터 한글
```

**엑셀 읽기의 규칙** (`xlsx.py` 머리 주석에 시트 배치가 있다)

- 시트 하나 = 급식표 하나. 6종 시트 중 어느 것을 읽을지는 호출 쪽이 `sheet` 로 고른다 (기관이 고른 유형). 안 주면 급식표로 보이는 첫 시트.
- `복숭아/치즈:⑪/②` 처럼 이름과 번호가 같은 개수로 갈리면 항목 둘로 나눈다(`복숭아:⑪` · `치즈:②`). 개수가 다르면(`찐고구마/우유:②`) 나누지 않는다 — 어느 번호가 어느 것인지 지어내지 않는다.
- `24:추석연휴` 는 24일 + 메모. 열 머리가 숫자가 아니면(생일식단 · 현장학습식단 견본 열) 날이 아니다.
- 빈 칸 · `0` 은 메뉴 없음. `#REF!` 같은 엑셀 오류값은 `unparsed`.
- 각주(`※알레르기 유발식품 …`)부터는 읽지 않는다.

---

## 5. PR 분할 (#21 → 5개)

| # | 내용 | 상태 |
| --- | --- | --- |
| 1/5 | `app/rules/allergen.py` 파서 | #64 머지 |
| 2/5 | 이 문서 + `MealPlanJSON` + `MealPlanReader` | #65 |
| 3/5 | 엑셀 읽기 (#63) | 이 PR |
| 4/5 | 사진 OCR (Gemini) + 설정 키 `MEAL_OCR_*` | |
| 5/5 | 평가 스크립트 + 정답셋 178항목 + 모델 비교 | |
| — | 저장 (테이블) | 회의안건 1-2 확정 후 별도 이슈 |

---

## 6. 열린 질문

- **`meal_type` 과 Food Agent 의 `MealSlot`.** Agent 쪽은 `snack` 하나인데 급식표는 오전 · 오후 간식이 갈린다. 저장은 다섯으로 두고 조회 때 `snack` 으로 합칠지, Agent 쪽을 나눌지 — 이시하 (2/5 PR 질문).
- **급식표 테이블.** 아이 기준 저장, 칸 단위 "확인 안 됨" 표시, `unknown` 번호를 남길 자리 — 회의안건 1-2 (김명성).
- **한글 라이브러리.** `pyhwp` 는 AGPL 이다. 채택 여부, 대안(센터에 엑셀 요청 · 다른 변환 경로) — #63 에서 정한다.
- **기관명 · 원본 보관.** `institution_name` 과 원본 이미지를 저장할지, 얼마나 둘지 — 회의안건 0-1 · 0-2.

---

## 7. 보안

저장소는 **public** 이다 (루트 §9). 실제 급식표 원본을 커밋하지 않는다. 테스트 fixture 는 기관명을 지운 1장 또는 센터 배포본(기관명 칸이 비어 있음)만 쓴다.
