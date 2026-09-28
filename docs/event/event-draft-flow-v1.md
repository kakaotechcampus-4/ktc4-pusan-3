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

지금 구현된 건 한 줄 입력 하나뿐이다. 나머지는 담당 기능이 붙을 때 생긴다.

| 경로 | 초안을 만드는 곳 | 상태 |
| --- | --- | --- |
| 한 줄 입력 ("금요일에 물놀이 있어") | Memory Agent (`app/agents/memory/drafts.py`) | 구현됨 (#110) |
| 추천 → 일정 (activity agent 의 "나들이") | suggestion 도메인 (서버) | Activity Agent 뒤 |
| 기관 공지 OCR → 일정 | OCR 파이프라인 | 공지 구조화 확정 뒤 |

**세 경로가 같은 payload를 낸다.** 승인 시트를 한 벌로 유지하기 위해서다. 모양이 갈리면 화면이 여러 벌이 된다.

Memory 의 `EventDraft` 클래스를 다른 경로가 가져다 쓰지는 않는다. `app/agents/memory/` 안이라
`apps/api/CLAUDE.md` 레이어 경계상 `app/api` 가 import 할 수 없고, 추천 카드를 누르는 건 발화가 아니라
Memory Agent 가 돌 일도 없다. **각자 만들되 JSON 모양을 맞춘다.**

---

## 3. 초안 payload 계약

정본은 `app/agents/memory/drafts.py` 의 `EventDraft.to_payload()` 다.
`op` 에 따라 두 모양이고, create 는 `POST`, update 는 `PATCH` 로 간다 (9/21 결정).

### create

```json
{
  "draft_id": "d1",
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
  "draft_id": "d2",
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

### 필드의 의미

| 필드 | 뜻 | 근거 |
| --- | --- | --- |
| `draft_id` | 화면이 카드를 가리키고 세션 스토리지 키로 쓴다. run 안에서만 유일 (제안·OCR 경로에는 run 이 없다 — 그때는 "한 응답 안에서만 유일" 로 읽는다) | 배열 인덱스로는 한 장 제출 뒤 나머지가 밀린다 |
| `op` | `create` / `update` 둘뿐. 화면이 부를 엔드포인트를 이 값으로 고른다 | 9/21 결정 |
| `event_id` · `before` | update 에만 있다. create 는 언제나 null 이라 싣지 않는다 | payload 를 op 별로 나눈 결과 |
| `before` | 수정 전 원본 **전체**(준비물 포함) | 화면이 "오후 3시 → 오후 5시" 를 그린다. 바뀐 필드 이름만으로는 보호자가 시트에서 값을 고치는 순간 못 쓰게 된다 |
| `items` | 제출 시점의 **최종 목록** | 항목별 op 가 없어서, 배열에서 빠진 것이 삭제를 표현하는 유일한 방법이다 |
| `item_id: null` | 아직 저장 전인 새 준비물 | — |

**`changed` 는 payload 에 없다.** `EventDraft` dataclass 에는 남아 있지만 tool 결과와 "바뀐 게 없으면
초안을 만들지 않는다" 판정에만 쓴다. 화면은 `before` 와 비교해 직접 구한다.

**`is_prepared` 는 payload 에 없다.** `DraftItem.to_payload()` 가 `item_id` 와 `item_name` 둘만 낸다.
`before.items` 도 같은 함수를 탄다.

**`source`(출처)는 아직 없다.** 제안 경로가 초안을 만들 때 `suggestion_id` 를 실을 자리가 필요한데,
Memory 는 채울 값이 영원히 `null` 이라 이번에 넣지 않았다. §6 참고.

---

## 4. 제출 API 가 지켜야 할 규칙

엔드포인트 모양은 아직 확정 전이다(§6). 아래는 **payload 를 어떻게 읽는지**에 대한 합의다.

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
되묻는 규칙이 유일한 안전장치다.

### 5-3. 초안의 수명

초안은 `AgentContext` 안에 run 단위로만 산다. SSE 로 나가고 나면 서버에 남지 않는다 (9/21 결정).

**화면이 세션 스토리지에 들고 있다.** 같은 `event_id` 의 초안은 최신 한 장만 남긴다(FE).
`DraftBook` 이 한 run 안에서 하는 병합을 화면이 run 밖으로 연장하는 셈이다.

### 5-4. 준비물 이름

Agent 가 조사를 떼고 사물 이름만 넣는다. `"수영복이랑 여벌옷을 챙겨야 해"` → `["수영복", "여벌옷"]`.

같은 이름을 **추가**하는 건 막혀 있다(`_has_item`). **이름 변경**으로 겹치는 건 안 막혀 있다 —
`"체육복을 물통으로 바꿔줘"` 인데 물통이 이미 있으면 같은 이름 두 행이 된다. 보호자가 명시적으로 시킨
변경이라 막지 않았고, 중복 합치기는 백엔드가 맡기로 했다.

---

## 6. 아직 안 정한 것

| | 상태 |
| --- | --- |
| 제출 엔드포인트 모양 | create 는 POST, update 는 PATCH 까지만 정했다. 경로와 요청 스키마는 미정. 계약서 갱신도 보류(9/21) |
| `source`(출처) 필드 | 제안 경로가 `suggestion_id` 를 실어야 한다. 키 이름과 모양은 그 경로를 만드는 PR 에서 정한다. 9/21 회의 미결 사항에 "기존 이슈와 어긋나 재확인 필요" 로 남아 있다 |
| 낡은 초안 덮어쓰기 | 초안 두 장을 띄워두고 나중 것부터 제출하면 앞 초안이 뒤 결과를 지운다. **현상유지**로 두고 잠금은 후속 (should). 화면이 세션 스토리지에서 한 장만 남기는 것으로 1차 방어 |
| 유령 `item_id` | 초안이 나간 뒤 준비물이 지워지면 없는 `item_id` 가 제출에 실려 올 수 있다. `before.items` 와 현재 행을 맞춰보면 감지는 가능하다. 구현 시 고려사항 |
| `check_when` 위치 | `app/agents/common/` → `app/rules/` 이전. 제출 API 가 재검증에 써야 해서 공용이 맞다. 공동 소유 폴더라 양쪽 리뷰 필요 |
| SSE 계약서 | `docs/api/api-interface-v1.html` §03 의 이벤트 목록에 `event_draft` 와 `note` 가 없다. 9/21 에 갱신을 보류하기로 했다. |
| create 초안 중복 | 같은 일정을 두 번 말하면 카드가 두 장 남는다. 제목·시각이 같아도 다른 일정일 수 있어 서버·화면 모두 합칠 근거가 없다. 보호자가 한 장만 내는 것에 기댄다 |

---

## 7. 코드 위치

| | |
| --- | --- |
| 초안 모델 · payload | `apps/api/app/agents/memory/drafts.py` |
| 일정 tool 7개 | `apps/api/app/agents/memory/tools/schedule.py` |
| SSE 이벤트 `EventDrafts` | `apps/api/app/agents/pipeline.py` |
| ORM | `apps/api/app/domains/schedule/models.py` (`Event` · `EventItem`) |
| 시각 규칙 | `apps/api/app/agents/common/datetime_rules.py` (`resolve_when` · `check_when`) — §6 대로 `app/rules/` 로 옮기면 이 줄도 같이 고친다 |
| 제출 API | 아직 없다. `apps/api/app/api/v1/routers/` 에 생긴다 |

`MemoryStore` Protocol(`memory/store/ports.py`)에는 일정 쓰기 메서드가 없다.
`create_event` · `update_event` · `create_event_item` 을 뺐다 — 계약에 남겨 두면 다음에 tool 을 붙이는
사람이 승인 없이 쓰는 길을 다시 연다. ORM 어댑터를 붙일 때도 event 쓰기는 제출 API 쪽 리포지토리에 둔다.
