# 복약 화면 v1

문서 목적: Health 복약(#537)의 FE 몫인 네 가지(복약 확인 카드 · 회차 체크 · 되묻기에 답하기 · 새 readout)를 화면이 어떻게 다루는지와, **서버보다 먼저 잡은 잠정 계약**을 고정한다.

기준 브랜치: `feat/fe-537-medication-draft-card`
작성일: 2026-10-23
담당: 고태영
선행 문서: 이슈 [#537](https://github.com/kakaotechcampus-4/ktc4-pusan-3/issues/537) · `apps/api/app/core/medication_draft.py`(초안 모양 정본 · #544) · [`docs/medication/medication-tables-v1.md`](../medication/medication-tables-v1.md)(두 표와 제출 · 체크 API 가 막을 것 · #546) · [`event-draft-ui-v1.md`](event-draft-ui-v1.md)(같은 규칙을 먼저 정한 일정 초안)

> ⚠️ **초안 모양만 확정이다.** 위 둘은 아직 develop 밖(`feat/ai-519-health-skeleton` · PR #520)에 있고, 제출 API · 회차 체크 API ·
> SSE 번역 · 되묻기 표시는 BE 몫인데 아직 없다. 화면은 §5 의 잠정 계약으로 목 위에서 먼저 만들었다. 서버가 생기면
> Swagger 에 맞춰 §5 표의 "고칠 곳" 만 고친다.

---

## 1. 네 가지가 어디에 서나

| | 어디 | 게이트 | 쓰는 것 |
| --- | --- | --- | --- |
| 복약 확인 카드 | 04 대화의 "복약 일정으로 넣을까요?" → "찾은 복약" 시트 | 🚨 **㉡** (건강 기록 확정) | 코스 1건 + 기간만큼 회차 |
| 회차 체크 | **11-3 복약** (`/child/{cid}/profile/medications`) | 아니다 | 회차 1행의 `checked_at` |
| 되묻기에 답하기 | 04 대화, Agent 블록 안 | 아니다 | 한 줄 입력(`reply_to`) |
| 새 readout | 04 대화, Agent 블록 안 | 아니다 | 없음 (읽기 · 복사) |

🚨 **복약 제출은 새 게이트가 아니다.** 게이트 ㉡ 안에 메서드가 둘(등록 `POST` · 수정 `PATCH`) 는 것이다 — 알레르기 등록 · 고치기가 ㉡ 의 두 메서드인 것과 같다 (최상위 §2 — 딱 2곳). `idempotentPath` 표에 두 줄이 늘고 `approval-gates.test.ts` 가 부르는 곳을 고정한다.

---

## 2. 복약 확인 카드 (게이트 ㉡)

카드는 `components/medication-draft-card.tsx`, 한 장씩 넘기며 건별로 제출하는 묶음은 `components/medication-draft-list.tsx` 다. 판단은 `lib/medication.ts` 에 있다.

- 🚨 **실제로 울릴 시각(`notice_times`)을 맨 위에 보여 준다.** 보호자가 승인하는 것은 "식후" 가 아니라 그 말이 바뀐 시각이다.
- 🚨 **시각을 고치는 칸이 없다.** 시각은 서버가 기준 시각 + 오프셋으로 만든다 (저녁 기준이 17:00 → 19:00 으로 바뀐 것처럼 기준은 서버 상수다 · #546). 화면이 계산하지 않는다 — 다르면 대화로 다시 말하라고 카드에 적는다.
- 🚨 **비어 온 필수 칸(`missing`)을 오늘로 채우지 않는다.** 가장 흔한 것이 기간(`ends_on`)이다 — 발화에 며칠인지 없으면 서버가 짐작하지 않는다. 비면 제출이 잠기고 **왜 잠겼는지 버튼 위에 다 말한다.** 잠금은 초안의 `missing` 이 아니라 **지금 칸의 값**으로 정한다 (채우면 풀린다).
- 🚨 **시작 · 끝이 서로를 넘지 못하게 막는 것은 입력 제약이다** (`DateField` 의 `fromDate` · `toDate`). 날짜를 계산하지 않는다.
- 🚨 **복용 이유는 고치는 칸이 아니다.** 서버가 그 문장으로 증상 코드를 뽑아 같이 보냈다 — 이유만 고치면 코드와 문장이 어긋난다. 보관 · 약 모양 · 특이사항은 보호자가 적는 **선택 칸**이다 (Agent 는 비워 낸다).
- 수정 초안은 **"19:30 → 20:00" 처럼 값의 변화**를 보여 준다 (`medicationDiff`). 알림 시각은 원본 회차 시각과 초안의 `notice_times` 를 견준다.
- 🚨 **취소하면 아무것도 부르지 않는다.** "안 넣을래요" 버튼을 두지 않는다 — 시트를 닫는다.
- 게이트 표식은 일정 카드와 같다 — `btn-approve` 하나, 버튼 위 `caution` 글자 한 덩이.
- 🚨 **여러 장은 전부 마운트해 두고 지금 장만 보인다.** 지금 장만 그리면 넘길 때 앞 장의 제출 상태와 키가 사라져서, 넣은 장으로 돌아오면 처음 상태로 되살아나 **새 키로 한 번 더** 넣을 수 있었다(같은 약이 코스 둘 · #537 리뷰). ⚠️ 일정 초안 목록(`EventDraftList`)은 아직 지금 장만 그린다 — 같은 구조라 따로 고친다.
- 🚨 **제출 실패는 코드마다 다르게 말한다** (`failureOf`). `consent_required` · `not_found` · 그 밖의 4xx 는 다시 눌러도 같은 답이라 버튼을 잠근다. 🚨 예외는 `409 idempotency_in_progress` 다 — 같은 키의 첫 요청이 아직 처리 중이라 들어갔을 수 있어서, 응답 없음과 같이 다룬다(잠그지 않고 같은 키로 다시 확인). `validation_failed` 는 칸을 고치면 새 본문이라 다시 넣을 수 있다. 응답을 못 받은 실패(네트워크 · 5xx)는 서버가 받았을 수 있어서 **"넣지 않았어요" 라고 단정하지 않고** 같은 키로 다시 확인하게 한다.
- 제출 본문은 보호자가 확인한 최종 상태 전체다. 🚨 `scheduled_time` · `draft_id` · `notice_times` · `missing` · `before` 를 싣지 않는다 (서버가 다시 계산하거나 화면 전용이다). 키는 **본문에 묶는다** — 실패 뒤 칸을 고쳐 다시 누르면 새 키다.

### 초안 보관 — 일정 초안과 다르다

🚨 **`stores/medication-draft.ts` 는 메모리 전용이다** (`persist` 없음). 일정 초안은 세션 스토리지에 두지만, 복약 초안에는 약 이름 · 용량 · 복용 이유가 들어 있다 — **건강 정보**다 (최상위 §2 개인정보). 대가로 새로고침하면 넣지 않은 초안이 사라진다(대화로 다시 말한다). 로그아웃에서 비운다.

- 같은 코스(`schedule_id`)의 수정 초안은 **최신 한 장만** 남긴다 — 낡은 수정 초안을 나중에 내면 뒤 수정이 되돌아간다.
- 얹는 것은 SSE 가 도착할 때 한 번이다 (`stores/conversation.ts` — 일정 초안의 `seedDrafts` 와 같은 이유).

---

## 3. 11-3 복약 — 회차 체크

`app/child/[childId]/profile/medications/page.tsx`. 11 아이 프로필의 "복약" 구역이 입구다 (`MedicationSection` · 이름만 한 줄씩).

⚠️ **실서버에는 아직 `GET /children/{cid}/medications` 가 없다.** 그래서 목이 뜨지 않는 빌드에서는 11 의 "복약" 구역과 04 회차 현황의 "복약 화면 열기" 를 숨긴다 (`PENDING_SERVER_FEATURES.medications` · #409 와 같은 처리). 안 숨기면 11 에 "복약을 불러오지 못했어요" 가 늘 선다. 라우트와 목은 그대로다. 서버가 생기면 그 항목을 지운다.

- 🚨 **복용 기록은 여기서만 생긴다.** 대화에서 "먹였어" 라고 적어도 서버는 아무것도 쓰지 않고 이 화면으로 안내한다 (Health `dose.check_on_screen`). 04 의 회차 현황(`dose_status`) 블록에는 이 화면으로 가는 링크가 선다.
- 🚨 **게이트가 아니다.** 체크는 되돌릴 수 있다 — `btn-approve` · `caution` 을 쓰지 않는다 (`no-extra-gates.spec.ts`). 키도 달지 않는다.
- 되돌릴 수 있는 토글이라 **낙관적 업데이트 + 회차마다 줄 세우기**다 — 09 준비물 체크와 같은 패턴이다 (`lib/serial-queue.ts`).
- 🚨 **날짜를 계산하지 않는다.** 진행 중인 코스, 서버의 오늘(`today`), 체크할 수 있는 회차(`checkable`)는 서버가 정한다. 화면은 오늘 줄을 `today` 와 같은 글자인지로만 가르고, 날짜 묶음은 서버 순서를 뒤집기만 한다(최근이 위).
- 🚨 **"먹여도 돼요" 로 읽힐 표시를 만들지 않는다.** 체크 칸은 먹였다는 기록일 뿐이다 — 남은 시간이나 "지금 먹이세요" 를 세우지 않는다. "오늘 체크 n/m회" 는 사실만 센다.
- 11-1 처럼 11 아래 라우트고 네비가 붙는다(가는 곳). 돌아가는 길은 "아이 프로필" 링크다.

---

## 4. 되묻기에 답하기

Health 는 세 자리에서 되묻고 멈춘다 — 중단 확인 · 어느 약 · 언제 먹일지. 셋 다 `agent_result.question` 으로 나간다.

- 🚨 **`awaits_reply` 가 참인 질문만 답할 자리를 연다** (§5 잠정 이름). 아닌 Agent 질문은 서버가 맥락을 들고 있지 않아서 `reply_to` 로 보내면 400 이다 (#246) — 안내 문장으로만 선다.
- 🚨 **run 당 답을 받는 질문은 하나다** (`lib/agent-result.ts` 의 `askedQuestion`). Memory 질문이 먼저고, 그다음이 `awaits_reply` 인 Agent 질문의 첫째다. 답은 `reply_to` = 그 run 하나로 가고, 서버가 이어받는 맥락도 run 당 하나라서 둘을 다 열면 보호자가 고른 질문과 서버가 이어받는 질문이 어긋날 수 있다.
- 답하는 길은 Memory 질문과 **같다** — 입력창이 그 질문을 대상으로 잡고, 원문을 다시 보내지 않고, 내려놓아도 질문은 열린 채 남는다 (`chat-run-v1.md` §3).
- 🚨 **답은 짧은 글로 받는다** ("네" · 약 이름 · "저녁 8시"). 버튼으로 답을 미리 깔지 않았다 — 서버가 글자로 받고, 버튼을 두면 "어느 약" 처럼 선택지가 서버 값인 질문마다 모양이 갈린다. (이슈의 "같이 정하고 싶은 것" 둘째 — §6)

---

## 5. 잠정 계약 — 서버가 정하면 고칠 곳

| | 잠정 | 고칠 곳 |
| --- | --- | --- |
| SSE 복약 초안 프레임 | 이름 `medication_drafts` · `medication_draft` 둘 다 받는다. 모양 `{ drafts: MedicationDraft[] }` 한 프레임 (일정 초안과 같은 모양) | `lib/api/sse.ts` 의 `MEDICATION_DRAFT_EVENT_NAMES` · 목 |
| 답을 받는 질문 표시 | `agent_result.awaits_reply: boolean` (없으면 `false`) | `sse.ts` 의 `AgentResultEvent` · `askedQuestion` |
| 등록 (게이트 ㉡) | `POST /children/{cid}/medications` · Idempotency-Key 필수 · 본문 `{ schedule, doses, source }` · 201 `{ course }` | `idempotentPath.submitMedication` · `SubmitMedicationBody` |
| 수정 (게이트 ㉡) | `PATCH /children/{cid}/medications/{sid}` · 키 필수 · 본문 `{ schedule, doses }` | `idempotentPath.updateMedication` |
| 진행 중 코스 | `GET /children/{cid}/medications` → `{ today, courses[] }`. 코스마다 `notice_times` 와 **오늘까지의** 회차 | `MedicationsResponse` · 11-3 · 11 |
| 회차 체크 | `PUT /children/{cid}/medication-doses/{did}/check` 본문 `{ checked }` → 회차. 키 없음. 회차마다 `checkable` | `CheckDoseRequest` · `MedicationDose` · 11-3 |

- 본문의 `doses` 는 초안의 시각 패턴(`timing_anchor` · `offset_minutes` · `fixed_time`)을 그대로 되돌려 보낸다. `scheduled_time` 은 싣지 않는다 (DB 생성 컬럼).
- 수정 때 지난 회차와 체크된 회차를 지우지 않는 규칙은 서버 몫이다 ([`medication-tables-v1.md`](../medication/medication-tables-v1.md) §5). 목은 그 규칙을 흉내 낸다.
- 지난 대화(`GET /children/{cid}/chat`)에 복약 초안이 남는지는 정해지지 않았다. 지금 화면은 이 앱에서 보낸 줄에만 카드를 세우고, 지난 턴에는 넣는 길을 두지 않는다.

---

## 6. 정해야 하는 것

1. **위 §5 의 경로 · 이름 전부** — BE 가 Swagger 로 정하면 화면이 따라간다. 특히 회차 체크를 `PUT …/check` 한 경로로 둘지(체크 · 해제가 같은 상태 설정이라 키가 필요 없다), `POST`/`DELETE` 둘로 나눌지.
2. **`GET` 이 어느 회차까지 싣는가** — 지금은 "오늘까지" 로 잡았다. 아직 오지 않은 날까지 싣는다면 `checkable: false` 로 와야 화면이 날짜를 견주지 않는다.
3. **되묻기 답을 버튼으로도 받을지** — 지금은 짧은 글만. 중단 확인("네 / 아니요")만 버튼을 더할지는 서버가 글자로 받으니 화면만의 결정이다.
4. **제출 거절 코드** — 중단된 코스 수정 · 기간 위반 · 동의 없음을 서버가 어떤 코드로 낼지. 화면은 지금 `consent_required` · `not_found` · `validation_failed` 만 가른다 (§2).
5. **`notice.no_active_med` 의 kind** — "지금 등록된 복약 일정이 없어요." 도 `kind: med_notice_form` 이라 복사 버튼이 선다. 서류가 아닌 안내라 kind 를 나눠 달라고 할지, 화면이 그대로 둘지.
6. **제출 키를 본문에 묶은 것** — 실패 뒤 칸을 고쳐 넣으면 422 에 갇히지 않는다. 대신 응답을 못 받은 제출 뒤 칸을 고쳐 넣으면 새 키라 코스가 둘 생길 수 있다. 서버가 같은 내용 중복을 막아 줄지.
7. **복약 중단이 게이트 밖이다** — Health 가 한 번 묻고 "네" 면 다음 run 에서 `stopped` 가 된다 (#537 AI 설계). 중단된 코스는 다시 고칠 수 없어 되돌리기 어렵다 — 최상위 §2 의 게이트 ㉡ 로 볼지 팀 결정이 필요하다.
8. **Health readout 문구의 줄표(`—`)** — `med.confirm` 등 코드 문구에 줄표가 있다. 화면은 서버 문구를 그대로 그리는데, 화면 글자에 줄표를 쓰지 않는다는 규칙(apps/web CLAUDE §5)과 부딪힌다. 문구 쪽에서 고칠지 묻는다.
