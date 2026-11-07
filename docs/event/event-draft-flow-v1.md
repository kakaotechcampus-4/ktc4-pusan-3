# 일정 초안 흐름 v1

문서 목적: 일정(`event`)이 만들어지는 경로와 초안 payload 계약을 한 곳에 고정한다.
9/21 전체 회의 결정 · #110 리뷰(FE·BE)에서 나온 합의 · 코드에 `TODO` 로 흩어져 있던 것을 담는다.

작성일: 2026-09-22 · 담당: 이시하
선행: [`docs/agents/memory-agent-v1.md`](../agents/memory-agent-v1.md) D9~D11 · 루트 [`CLAUDE.md`](../../CLAUDE.md) §2·§3

**이 문서에 없는 것** — `reminder`(알림)는 다루지 않는다. 복약 스케줄의 승인 게이트도 없다
(Health Agent 구현 뒤 별도 문서). 엔드포인트는 9/21 회의에서 다룬 범위까지만 적는다.

---

## 1. 왜 초안인가

9/17에 `event.status`를 없앴다. 승인 전 행을 DB에 두고 `draft/confirmed` 로 가르던 방식을 접은 것이다.
그래서 **`event` 테이블에는 보호자가 제출한 행만 들어간다.** Agent 는 필드를 채운 초안을 만들어
SSE 로 내보내는 데까지 하고, 저장은 제출 API 가 한다.

루트 §2의 승인 게이트 ㉠(캘린더 쓰기)이 여기 걸려 있다. **초안을 만드는 코드가 DB 에 직접 쓰면 게이트를 건너뛴다.**

---

## 2. 초안을 만드는 경로

지금 구현된 건 한 줄 입력과 추천 → 일정이다. 나머지는 담당 기능이 붙을 때 생긴다.

| 경로 | 초안을 만드는 곳 | 상태 |
| --- | --- | --- |
| 한 줄 입력 ("금요일에 물놀이 있어") | Memory Agent (`app/agents/memory/drafts.py`) | 구현됨 (#110) |
| 추천 → 일정 (activity agent 의 "나들이") | suggestion 도메인 (서버) | 구현됨 (#236, 준비물 #315) |
| 기관 공지 OCR → 일정 | OCR 파이프라인 | 공지 구조화 확정 뒤 |

**추천 → 일정의 준비물은 `suggestion.items` 에서 온다** (값 하나가 `item_name` 하나, `item_id: null`).
🚨 food 제안 여러 건을 한 끼로 묶을 때 같은 재료(두부 등)가 겹치면 **서버가 한 번으로 합친다** —
화면은 받은 `items` 를 거르지 않아서 그대로 두 줄이 뜬다. 앞뒤 공백을 떼고 NFC 로 맞춘 이름이 같으면
같은 재료다(안쪽 띄어쓰기는 그대로). 빈 이름은 버리고, 처음 나온 순서를 지킨다. 제출 요청의 상한
(이름 100자 · 100개)을 넘는 것은 초안에 싣지 않는다 — 초안을 그대로 제출했을 때 422 가 나지 않게
(`app/domains/suggestion/draft_items.py`).

**세 경로가 같은 payload를 낸다.** 승인 시트를 한 벌로 유지하기 위해서다. 모양이 갈리면 화면이 여러 벌이 된다.

Memory 의 `EventDraft` 클래스를 다른 경로가 가져다 쓰지는 않는다. `app/agents/memory/` 안이라
`apps/api/CLAUDE.md` 레이어 경계상 `app/api` 가 import 할 수 없고, 추천 카드를 누르는 건 발화가 아니라
Memory Agent 가 돌 일도 없다. **각자 만들되 `app/core/event_draft.py` 의 스키마로 검증한다.**
`EventDraft` 는 run 안에서 값을 쌓는 조립용으로 남고, `to_payload()` 가 공통 모델을 채워 직렬화한다.
문서만 보고 모양을 맞추면 필드 이름이나 타입이 갈려도 아무 데서도 걸리지 않는다 (멘토 리뷰).

---

## 3. 초안 payload 계약

정본은 `app/core/event_draft.py` 의 `CreateEventDraft` · `UpdateEventDraft` 다. 모르는 키는 거부한다(`extra="forbid"`).
`event_type` 은 `core` · `episodic`, `category` 는 `institution` · `health` · `activity` · `etc` 만 받는다
(`domains/schedule/models.py` 와 같은 값). 시간대가 없는 시각도 거부한다.
시각은 `isoformat()` 표기 그대로다. pydantic 기본값처럼 UTC 를 `Z` 로 바꾸지 않고 기존 payload 표기를 그대로 둔다.
`op` 에 따라 두 모양이고, create 는 `POST`, update 는 `PATCH` 로 간다 (9/21 결정).

### create

```json
{
  "draft_id": "6f1c2a9e-3b7d-4c55-9a1e-2d8f0b4c7e31",
  "op": "create",
  "event": {
    "title": "물놀이",
    "starts_at": "2026-09-18T10:00:00+09:00",
    "ends_at": null,
    "all_day": false,
    "event_type": "episodic",
    "category": "activity"
  },
  "items": [
    { "item_id": null, "item_name": "수영복" },
    { "item_id": null, "item_name": "여벌옷" }
  ]
}
```

### update

```json
{
  "draft_id": "b2e47d08-91c6-4f3a-8d25-7c0e5a6b1f94",
  "op": "update",
  "event_id": "ev_1",
  "event":  { "title": "운동회", "starts_at": "2026-09-18T17:00:00+09:00",
              "ends_at": null, "all_day": false,
              "event_type": "episodic", "category": "institution" },
  "before": { "title": "운동회", "starts_at": "2026-09-18T15:00:00+09:00",
              "ends_at": null, "all_day": false,
              "event_type": "episodic", "category": "institution",
              "items": [ { "item_id": "ei_1", "item_name": "체육복" } ] },
  "items": [ { "item_id": "ei_1", "item_name": "체육복" },
             { "item_id": null,  "item_name": "모자" } ]
}
```

### create (요일 반복)

요일마다 되풀이되는 일정(#738)은 초안 **한 장**이다. create 모양에 `repeat` 만 더한다. 반복이 아닌 초안에는
`repeat` 키가 없다 — 항상 null 인 키는 싣지 않는 규칙이라 모델을 따로 뒀다(`CreateRepeatEventDraft`).

```json
{
  "draft_id": "…", "op": "create",
  "event": { "title": "등원", "starts_at": "2026-11-09T08:30:00+09:00", "ends_at": null,
             "all_day": false, "event_type": "core", "category": "institution" },
  "items": [ { "item_id": null, "item_name": "물통" } ],
  "repeat": { "weekdays": [1, 3], "until": "2026-12-20", "label": "12월 20일까지 매주 월, 수" }
}
```

`event.starts_at` 은 **첫 회차**의 시각이다 (말한 시작일이 그 요일이 아니면 다음 해당 요일로 옮긴다).
`weekdays` 는 ISO 요일(1=월 … 7=일), `until` 은 마지막 날(포함)이다. `label` 은 화면이 그대로 그린다 — 프론트는
날짜를 계산하지 않는다. 제출하면 백엔드가 회차마다 일정 행으로 펼친다. 기간은 **첫 회차부터 90일**(첫 회차 포함)이 상한이다. 세는 첫 날은 묶음에 저장되는 첫 회차 날짜다.

### update_series (묶음 전체 고치기)

반복 묶음을 **오늘 이후 회차 전체**에 고치는 초안이다 (`UpdateSeriesDraft`, `PATCH /children/{cid}/event-series/{sid}` 로 간다).
날짜가 없다 — 회차 날짜는 반복 규칙이 정한다. 시각은 KST `"HH:MM"` 이고 종일이면 null 이다.

```json
{
  "draft_id": "…", "op": "update_series", "series_id": "series-1",
  "event":  { "title": "등원", "starts_time": "09:00", "ends_time": null,
              "all_day": false, "event_type": "core", "category": "institution" },
  "repeat": { "weekdays": [1, 3], "until": "2026-12-20", "label": "12월 20일까지 매주 월, 수" },
  "before": { "title": "등원", "starts_time": "08:30", "ends_time": null, "all_day": false,
              "event_type": "core", "category": "institution",
              "repeat": { "weekdays": [1, 3], "until": "2026-12-20", "label": "12월 20일까지 매주 월, 수" },
              "items": ["물통"] },
  "items": ["물통", "실내화"]
}
```

`items` 는 준비물 **이름**이다 — 회차마다 `item_id` 가 달라서 이름으로 맞추고, 같은 이름은 그 회차의 챙김
표시를 지킨다. `event_id` 는 없다. 한 회차만 고칠 때는 이 모양이 아니라 보통 update 초안이다.

**제출 API 는 `before` 와 달라진 값만 오늘 이후 회차에 적용한다.** 값이 같은 칸은 건드리지 않는다 —
개별로 고쳐 둔 회차(그 날만 10시로 옮긴 것)의 다른 값이 "전부 고치기" 에 덮이지 않는다.
`before.event` 의 값은 오늘 이후 회차에서 **가장 흔한 값**이다 (첫 회차가 아니다). 준비물은 이름으로
비교한다: `before.items` 에만 있으면 뺀다, `items` 에만 있으면 더한다, 양쪽에 있으면 그 회차의
챙김 표시를 지킨다. 이름을 바꾸면 빼고 더하는 것과 같다.

### 필드의 의미

| 필드 | 뜻 | 근거 |
| --- | --- | --- |
| `draft_id` | 화면이 카드를 가리키고 세션 스토리지 키로 쓴다. **전체에서 유일한 uuid** 다 — 화면은 아이의 모든 초안을 한 스토어에 두고 `draft_id` 가 같으면 같은 장으로 갈아 끼워서, run 안에서만 유일하면 앞 run 의 안 넣은 초안이 조용히 덮인다. Memory 초안(`DraftBook`)과 제안 초안(`_next_draft_id`)이 같은 규칙이다 (#250) | 배열 인덱스로는 한 장 제출 뒤 나머지가 밀린다 |
| `op` | `create` / `update` 둘뿐. 화면이 부를 엔드포인트를 이 값으로 고른다 | 9/21 결정 |
| `event_id` · `before` | update 에만 있다. create 는 언제나 null 이라 싣지 않는다 | payload 를 op 별로 나눈 결과 |
| `before` | 수정 전 원본 **전체**(준비물 포함) | 화면이 "오후 3시 → 오후 5시" 를 그린다. 바뀐 필드 이름만으로는 보호자가 시트에서 값을 고치는 순간 못 쓰게 된다 |
| `items` | 제출 시점의 **최종 목록** | 항목별 op 가 없어서, 배열에서 빠진 것이 삭제를 표현하는 유일한 방법이다 |
| `item_id: null` | 아직 저장 전인 새 준비물 | — |

**`changed` 는 payload 에 없다.** `EventDraft` dataclass 에는 남아 있지만 tool 결과와 "바뀐 게 없으면
초안을 만들지 않는다" 판정에만 쓴다. 화면은 `before` 와 비교해 직접 구한다.

**`is_prepared` 는 payload 에 없다.** 공통 스키마의 `DraftItemPayload` 가 `item_id` 와 `item_name` 둘만 갖는다.
`before.items` 도 같은 모델을 쓴다.

**`source`(출처)는 아직 없다.** 제안 경로가 초안을 만들 때 `suggestion_id` 를 실을 자리가 필요한데,
Memory 는 채울 값이 영원히 `null` 이라 이번에 넣지 않았다. §6 참고.

---

## 4. 제출 API 가 지켜야 할 규칙

엔드포인트는 create 가 `POST /children/{cid}/events`(#236), update 가 `PATCH /children/{cid}/events/{eid}`(#284)다.
둘 다 `Idempotency-Key` 가 필수다. 요청 스키마의 정본은 Swagger 고, 아래는 **payload 를 어떻게 읽는지**에 대한 합의다.

### 4-1. 분기는 두 단계다

```
1단계  event_id 로 event 행을 정한다
         null(create)     → INSERT event, 새 id 를 받는다
         non-null(update) → UPDATE event

2단계  1단계에서 정해진 event id 아래에서 items 를 순회한다  ← create·update 둘 다
         item_id null      → INSERT
         item_id non-null  → UPDATE
         배열에 없는 기존 item_id → DELETE
```

🚨 **2단계를 `event_id` 분기 안에 넣으면 안 된다.** create 초안도 `items` 를 갖고(전부 `item_id: null`)
전부 INSERT 대상이다. `event_id` 로 먼저 갈라 놓고 update 쪽에서만 items 를 돌면 새 일정의 준비물이 통째로 빠진다.

2단계의 DELETE 는 **보호자가 승인 시트에서 준비물을 지운 경우**를 위한 것이다. Agent 가 지운 준비물은
`delete_event_item` 이 이미 DB 에서 바로 지운다.

### 4-2. 시각을 다시 검증한다

보호자가 시트에서 고친 시각도 `check_when` 으로 재검증한다. 루트 §2 의
"날짜·나이 계산·일정 충돌은 규칙(코드)이 막는다" 는 보호자 입력에도 걸린다.

`check_when` 은 지금 `app/agents/common/datetime_rules.py` 에 있다. `app/rules/` 로 옮겨 공용으로 두자는
제안이 열려 있다 (§6).

### 4-3. `is_prepared` 는 초안에서 읽지 않는다

체크 상태는 `PATCH /event-items/{iid}` 만의 몫이다. 초안이 정하는 것은 **새 준비물의 INSERT 초기값뿐**이고,
그 값은 payload 에 없으므로 제출 API 가 `false` 로 박는다.

payload 에 `is_prepared` 가 아예 없어서 이 규칙은 자동으로 지켜진다. 없앤 이유는 이렇다 —
초안이 2시에 나가고 보호자가 3시에 캘린더에서 체크하면 DB 는 `true` 가 되는데,
4시에 그 초안을 제출하면서 옛 값을 저장하면 3시에 한 체크가 풀린다.

### 4-4. `draft_id` 는 읽지 않는다

건별 제출로 정해지면서 서버가 `draft_id` → `event_id` 를 되돌려 줄 일이 없어졌다.
FE 도 제출 요청에 싣지 않는다. 실려 오더라도 400 없이 무시한다. **화면 전용 키다.**

### 4-5. 2차 검증 (BE 제안)

초안을 만드는 코드가 Memory 하나일 때는 아래가 구조적으로 불가능하지만, 제안·OCR 경로가 붙으면
payload 를 만드는 코드가 늘어난다. 저장하는 쪽에서 한 번 더 막는다.

- `items` 안에 같은 `item_id` 가 두 번 → 400
- 그 일정의 준비물이 아닌 `item_id` 가 섞여 옴 → 400

### 4-6. 요일 반복 (#738)

코드는 `app/domains/schedule/series.py`, 회차 날짜는 `app/rules/recurrence.py`.

- **만들기** — `POST /children/{cid}/events` 본문 **최상위**에 `repeat: {weekdays, until}` (`event` 안이 아니다, 문구 `label` 은 싣지 않는다).
  `event.starts_at` 의 KST 시각과 길이를 모든 회차에 쓴다. 한 회차는 **그날(KST) 안에서 끝난다** — 23:00 ~ 다음 날 01:00 처럼 자정을 넘으면 422 `invalid_repeat`(묶음 고치기가 하루 시각만 받아 만들어 두면 고칠 수 없다). 종일만 다음 날 00:00 끝을 허용한다. 회차마다 일정 · 준비물 · 알림 행이 따로 생기고,
  응답 `event` 는 첫 회차에 `series` 를 단다. 규칙이 성립하지 않으면(90일 초과 등) 422 `invalid_repeat`
- **알림** — 고른 값 하나를 회차마다 건다. 울릴 시각이 이미 지난 회차는 건너뛰고, 걸리는 회차가 하나도 없으면 단건처럼 422 `invalid_reminder`
- **묶음 고치기** — `PATCH /children/{cid}/event-series/{sid}` (게이트 ㉠, 키 필수). 아이 스코프 아래에 둔 것은 일정 수정과 같은 이유로 남의 아이 묶음을 가리기 위해서다
  - **아직 시작하지 않은 회차만 고친다 (서버 시계 기준).** 시간 일정은 시작이 지금 이후인 회차, 종일 일정은 KST 오늘(그날 포함) 이후 회차다. 시작이 지난 오늘 회차는 지난 회차로 보고 그대로 둔다 — 옮기면 그 회차 알림이 다시 울리고, 카드의 "지난 회차는 그대로예요" 가 틀린 말이 된다 (#761). 지우기 · 새로 만들기 · 아래 422 판정도 같은 기준이다. Agent 가 `before` 를 만들 때 읽는 범위도 이 기준이어야 한다
  - `before` 와 달라진 칸만 쓴다. 시각 · 종일은 한 칸으로 본다. `before` 가 null 이면 모든 칸을 쓴다
  - 요일 · 끝 날짜가 저장된 묶음과 다르면 다시 펼친다 — **옛 규칙에 있었는데 새 규칙에서 빠진 날**과 **새 끝 날짜 뒤**의 회차만 지운다. 규칙 밖 요일로 옮겨 둔 회차는 둘 다 아니라 남는다 (#761). 새 회차는 **옛 규칙에 없던 날에만** 만든다. 한 회차만 다른 날로 옮겼거나 지워 둔 날은 옛 규칙에 있던 날이라 되살리지 않는다
    - 한계: 회차의 원래 날짜를 저장하지 않아서, 빠진 요일 위로 옮겨 둔 회차는 지워지고 원래 날도 되살아나지 않는다
  - 새 회차의 알림은 보호자마다 그 묶음의 가장 늦은 회차 값을 따른다. 값은 옛 회차를 지우기 **전에** 읽는다 — 요일을 통째로 바꿔 오늘 이후 회차가 전부 새로 생겨도 알림이 따라간다
  - 다시 펼친 결과 오늘 이후 회차가 하나도 없으면(끝 날짜를 어제로 당기는 등) 422 `invalid_repeat`. 묶음을 지우는 길은 삭제 제안이다(§6). Agent 도 같은 경우를 막는다
  - 90일은 `starts_on`(첫 회차, 바뀌지 않는다) 뒤 **새 요일과 처음 맞는 날**부터 센다. 새 요일에 `starts_on` 의 요일이 없으면 그만큼 늦게 센다 — `rules/recurrence.py` 의 `occurrence_dates` 하나를 Agent 와 같이 쓰는 결과다
  - 응답은 `{series, occurrence_count}` (고친 뒤 오늘 이후 회차 수)
- **캘린더** — 회차인 일정은 `series: {id, weekdays, until, label}`. 반복이 아니면 **키가 없다**(null 이 아니다 — 웹 `CalendarEvent.series?`). 제출 · 수정 응답의 `event` 도 같다
- **묶음 고치기 본문의 종일** — `all_day: true` 면 함께 온 시각은 버리고 그날 00:00 으로 둔다 (422 로 막지 않는다). 반복 일정의 `event_type` 은 무엇이 와도 core 로 둔다

---

## 5. Agent 쪽 동작 (참고)

제출 API 를 만들 때 알아야 하는 것만 적는다. 자세한 건 [memory-agent-v1](../agents/memory-agent-v1.md) D9~D11.

### 5-1. 한 run 에서 같은 일정은 초안 하나

두 번째 호출은 DB 현재 값이 아니라 버퍼의 초안 위에 쌓는다. `update_event` 와 `create_event_item` 이
한 응답에 같이 와도 실행 순서와 무관하게 결과가 같다.

새 일정끼리는 합치지 않는다. `event_id` 가 없어 같은 일정인지 알 방법이 없다.

### 5-2. 바로 쓰는 것 셋

승인 게이트 밖이라 Agent 가 DB 에 바로 쓴다.

- 준비물 챙김 표시 (`is_prepared`) — 되돌릴 수 있다
- 일정 삭제 (`delete_event`)
- 준비물 삭제 (`delete_event_item`)

삭제는 되돌릴 수 없는데 게이트 밖이다. 9/17 에 그렇게 정했고, 대상이 모호하거나 범위가 넓으면
되묻는 규칙이 유일한 안전장치다. 반복 묶음의 회차도 일정 한 건이라 같은 규칙이다 — 묶음 전체를
한 번에 지우는 길은 없다 (#738, §6).

### 5-3. 초안의 수명

초안은 `AgentContext` 안에 run 단위로만 산다. SSE 로 나가고 나면 서버에 남지 않는다 (9/21 결정).

**화면이 세션 스토리지에 들고 있다.** 같은 `event_id` 의 초안은 최신 한 장만 남긴다(FE).
`DraftBook` 이 한 run 안에서 하는 병합을 화면이 run 밖으로 연장하는 셈이다.

### 5-4. 준비물 이름

Agent 가 조사를 떼고 사물 이름만 넣는다. `"수영복이랑 여벌옷을 챙겨야 해"` → `["수영복", "여벌옷"]`.

같은 이름을 **추가**하는 건 막혀 있다(`_has_item`). **이름 변경**으로 겹치는 건 안 막혀 있다 —
`"체육복을 물통으로 바꿔줘"` 인데 물통이 이미 있으면 같은 이름 두 행이 된다. 보호자가 명시적으로 시킨
변경이라 막지 않았고, 중복 합치기는 백엔드가 맡기로 했다.

### 5-5. 반복 일정과 범위 (#738)

**만들기.** 요일마다 되풀이되면 날짜마다 따로 부르지 않고 `create_event` 한 번에 `repeat_weekdays`(`mon`…`sun`) ·
`repeat_until` 을 넣는다. 반복 일정은 `core` 로 만들어진다 (모델이 `episodic` 을 줘도 코드가 정한다).
어느 날이 회차인지는 모델이 아니라 `app/rules/recurrence.py` 가 정한다.

- **끝 날짜를 안 말했으면 만들지 않는다.** `REPEAT_UNTIL_REQUIRED` 로 돌려보내 "언제까지 반복할까요?" 하나만 묻는다. 기본값으로 채우지 않는다
- **store 가 반복 묶음을 지원할 때만 만든다.** `store.supports_series` 가 참이어야 한다 (`store/ports.py` 의 `supports_series()`).
  DB 어댑터(`DbMemoryStore`)도 `supports_series = True` 다 (#738 BE). 그 전에는 `OUT_OF_SCOPE`
  로 돌려보내 "아직 반복 일정은 만들 수 없다" 고 알린다 — 제출할 곳이 없어 반복으로 말한 일정이 하나만
  만들어지는 일을 막는다
- **"계속"·"쭉"** 이라고 해도 90일까지만 된다고 알리고 끝 날짜를 묻는다 (`DATE_UNPARSEABLE`)
- 반복에 `ends_on`(다른 날 끝)을 같이 받지 않는다. 한 회차는 그날 안에서 끝난다 (자정을 넘으면 제출이 422 다 · §4-6)

**고치기 · 지우기.** `query_event` 결과에 `series` 가 붙은 일정(반복 묶음의 회차)을 고치거나 지울 때는 `scope` 를 정한다.

| `scope` | 뜻 | 결과 |
| --- | --- | --- |
| `one` | 날짜를 집어 말했다 — 그 회차 하나 | 보통 update 초안 / 그 회차만 삭제 |
| `series` | 날짜 없이 그 반복 일정 자체를 말했다 | `update_series` 초안(오늘 이후 회차) |
| 없음 | — | `SCOPE_REQUIRED` — 한 회차만 고치는 쪽으로 정하지 않고 모델이 범위를 정하거나 되묻는다 |

- 반복이 아닌 일정에 `scope=series` 는 `INVALID_ARGS` 다. `repeat_*` 인자는 `scope=series` 에서만 받는다
- `scope=series` 는 날짜(`starts_on` · `ends_on`)를 받지 않는다. 요일 · 끝 날짜는 `repeat_*` 로 바꾸고, 90일은 **묶음의 첫 회차 뒤 새 요일과 처음 맞는 날**에서 센다 (§4-6)
- 묶음 초안은 **오늘 이후** 회차에만 적용된다. 지난 회차는 그대로다
- 준비물: `create_event_item` · `update_event_item(item_name)` · `delete_event_item` 의 `scope=series` 는 전부 묶음 초안의 `items` 를 고친다. 저장은 보호자가 확인하고 제출한 뒤에 된다. 같은 준비물을 한 run 에서 두 번 고쳐도 처음 이름에서 지금 이름을 따라간다
- `scope` 는 고치기와 준비물에만 있다. `delete_event` 는 범위를 받지 않고 지금처럼 일정 한 건(회차 하나)을 지운다. 묶음 전체 삭제는 §6
- 묶음 초안의 기준 값(`before`)은 아직 시작하지 않은 회차에서 가장 흔한 값이다. 첫 회차를 쓰지 않는 것은 그 회차만 따로 고쳐 둔 값이 묶음 전체로 번지지 않게 하려는 것이다
  - "아직 시작하지 않은 회차" 는 서버 `update_series` 가 고치는 회차와 같다 — 시간 일정은 시작이 지금 이후, 종일은 오늘(그날 포함) 이후다. 시작이 지난 오늘 회차까지 세면 `before` 가 서버가 고치는 회차의 값과 달라진다 (#761)
  - 남은 회차가 없으면 초안을 만들지 않는다 — 묶음 그대로면 `TARGET_NOT_FOUND`, 바꾼 규칙이면 `DATE_UNPARSEABLE`. 바꾼 규칙의 오늘 회차는 고친 뒤의 시각으로 본다
- 묶음의 `event_type` 은 늘 core 다. `scope=series` 로 바꾸려 하면 `INVALID_ARGS` 다
- **챙김 표시(`is_prepared`)는 `scope` 를 받지 않는다** — 늘 그날 회차 하나다. `scope=series` 와 같이 오면 `INVALID_ARGS`
- 고쳤다가 원래 값으로 되돌리면 바뀐 게 없으니 묶음 초안을 뺀다 (`changed` 는 `before` 와 비교한 값이다)

---

## 6. 아직 안 정한 것

| | 상태 |
| --- | --- |
| `source`(출처) 필드 | 제안 경로가 `suggestion_id` 를 실어야 한다. 키 이름과 모양은 그 경로를 만드는 PR 에서 정한다. 9/21 회의 미결 사항에 "기존 이슈와 어긋나 재확인 필요" 로 남아 있다 |
| 낡은 초안 덮어쓰기 | 초안 두 장을 띄워두고 나중 것부터 제출하면 앞 초안이 뒤 결과를 지운다. **현상유지**로 두고 잠금은 후속 (should). 화면이 세션 스토리지에서 한 장만 남기는 것으로 1차 방어 |
| 유령 `item_id` | 초안이 나간 뒤 준비물이 지워지면 없는 `item_id` 가 제출에 실려 올 수 있다. `before.items` 와 현재 행을 맞춰보면 감지는 가능하다. 구현 시 고려사항 |
| `check_when` 위치 | `app/agents/common/` → `app/rules/` 이전. 제출 API 가 재검증에 써야 해서 공용이 맞다. 공동 소유 폴더라 양쪽 리뷰 필요 |
| SSE 계약서 | `docs/api/api-interface-v1.html` §03 의 이벤트 목록에 `event_draft` 와 `note` 가 없다. 9/21 에 갱신을 보류하기로 했다. |
| 묶음 전체 삭제 | 이 문서 범위 밖이다. 일정 삭제가 삭제 제안 카드(#535, `docs/api/delete-proposal-v1.md`)로 옮겨 가서, 묶음 전체는 그 제안에 `event_series` 종류를 더해 지운다 (후속 이슈 · BE · AI · FE). 회차를 `event` 탭에 담는 것만으로는 탭당 40건 상한에 걸리고 묶음 행이 남는다 |
| create 초안 중복 | 같은 일정을 두 번 말하면 카드가 두 장 남는다. 제목·시각이 같아도 다른 일정일 수 있어 서버·화면 모두 합칠 근거가 없다. 보호자가 한 장만 내는 것에 기댄다 |

---

## 7. 코드 위치

| | |
| --- | --- |
| 초안 payload 스키마 | `apps/api/app/core/event_draft.py` |
| 초안 모델 (Memory 조립용) | `apps/api/app/agents/memory/drafts.py` |
| 일정 tool 7개 | `apps/api/app/agents/memory/tools/schedule.py` |
| 반복 회차 날짜 · 90일 · 문구 | `apps/api/app/rules/recurrence.py` (`occurrence_dates` · `repeat_label`) |
| SSE 이벤트 `EventDrafts` | `apps/api/app/agents/pipeline.py` |
| ORM | `apps/api/app/domains/schedule/models.py` (`Event` · `EventItem`) |
| 시각 규칙 | `apps/api/app/agents/common/datetime_rules.py` (`resolve_when` · `check_when`) — §6 대로 `app/rules/` 로 옮기면 이 줄도 같이 고친다 |
| 제출 API | 아직 없다. `apps/api/app/api/v1/routers/` 에 생긴다 |

`MemoryStore` Protocol(`memory/store/ports.py`)에는 일정 쓰기 메서드가 없다.
`create_event` · `update_event` · `create_event_item` 을 뺐다 — 계약에 남겨 두면 다음에 tool 을 붙이는
사람이 승인 없이 쓰는 길을 다시 연다. ORM 어댑터를 붙일 때도 event 쓰기는 제출 API 쪽 리포지토리에 둔다.
