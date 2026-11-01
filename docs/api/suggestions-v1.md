# 05 추천 받기 — 판단 순서 · 다시 시도 · 응답 조립 (#249)

필드 정의는 Swagger(`/docs` 의 `POST /children/{cid}/suggestions`)가 정본이다. 여기는 흐름과 이유만 둔다.

## 1. 판단 순서

`run_id` 가 없으면(홈 · 04 "이것도 도와드릴까요?") Agent 를 처음 요청으로 돌린다. 요청 문장은 홈 버튼
키의 표 문장(`rules/home_prompts.py`)뿐이다 — 화면이 보낸 문장으로 Agent 를 돌리면 Supervisor 안전
사전검사를 건너뛴다.

`run_id` 는 04 "후보 보기" · 부분 실패 카드의 "다시 준비하기"(#227)로 들어온 05 에만 붙고, 그 화면의 다시 시도 · 새로고침도 같은 값을
보낸다. 이때 Agent 마다:

1. 그 run 의 추천(채택한 것 전부 + 만료 전 draft)이 있으면 그대로 돌려준다.
2. 0개 기록이 있으면(24시간) 같은 0개 안내.
   2-1. 닫힘 기록이 있으면(24시간) 같은 guard — 다시 만든 결과가 blocked · unsupported 였던 Agent.
3. 실패 맥락이 있으면(15분, 처음 넣은 시각부터) 처음 시각 · 문장으로 다시 만든다. 다른 요청이
   쓰는 중이면 실패로 둔다(다시 시도할 수 있게).
4. 그 밖에는 다시 만들지 않고 `cannot_resume` 안내 — 맥락 15분 지남 · 서버 재시작 · 24시간 지남 ·
   그 run 에서 돌지 않은 Agent.

요청 모양을 그대로 두고 서버가 가르는 이유 — 화면이 실패한 Agent 만 골라 보내면 응답 둘을 화면이
합쳐야 한다(#245 프론트 답변).

## 2. 보관함

`app/api/runs/suggestion_retry.py`. 입력 run 의 pipeline 이 화면에 안 나가는 이벤트(`RetryContext` ·
`EmptyResult`)를 그 Agent 결과 · `partial` 보다 먼저 내고, 러너가 넣는다. 결과를 받자마자 "후보 보기"
로 들어와도 기록이 먼저 있다.

- 프로세스 메모리 — 재시작하면 사라진다(그래서 4번). `--workers 1` 전제.
- 실패 맥락에는 원문 조각이 있다. 로그를 남기지 않고, 넣을 때 보관 기간 뒤 지우는 예약을 건다. 탈퇴 · 연결
  끊기 때는 바로 지운다(`docs/safety/retention-v1.md` · #561). 그 순간 진행 중이던 다시 만들기가 끝나며 넣은
  것은 보관 기간 뒤 지워진다.
- 꺼낼 때 "사용 중" 만 표시하고, 성공하면 지우고 실패하면 표시만 푼다. 하루 한도에 걸려 429 로 끝나도
  표시를 푼다 — 안 풀면 15분 동안 다시 시도할 수 없다. claim 뒤 무엇이 터져도 풀린다(`finally`).

## 3. 응답 조립

| 칸 | 규칙 |
| --- | --- |
| `groups` | 결과를 낸 Agent 마다 하나(0개 포함). 실패한 Agent 는 없다 — 화면이 실패 카드를 그린다. `notice` 는 보이는 개수로 다시 만든다(알레르기 · 근거로 빠진 것이 있으면 Agent 의 안내와 다르다) |
| `guards` | `blocked` → `safety_unknown`(Growth 는 안전 문구일 때만, 월령 · 동의 · 포트 · 문서 행 없음으로 닫히면 `closed`), `unsupported` → `unsupported`, 아직 없는 Agent · Growth 처음 요청 → `not_ready` |
| `suggestions` | 개인화. 보이는 아이 기록 근거가 0행이면 뺀다(지운 기록은 근거로 다시 보여 주지 않는다) |
| `general` | 일반 추천. 근거는 문서 행이고 출처 기관 · 출처 제목을 싣는다. Growth 도서는 고른 책(`book_catalog`)이 근거고 출처는 국립중앙도서관 · 도서관 정보나루, 제목은 그 추천의 "제목(저자)" 다. 문서 근거 0행인 일반 추천은 Agent 와 저장이 둘 다 거절한다 — 보호자가 놀이를 정해 물은 Activity 추천(`suggestion.requested`)만 예외라 `evidence` 가 비고 기준은 `basis` 문구다. 검수가 풀린 문서만 남은 일반 추천은 내지 않는다(요청 기반이어도 문서를 달았으면 같다) |
| `scarcity` | 일반 추천이 있을 때만. `count` 는 그 도메인의 남아 있는 관찰 수, `question` 은 Agent 가 되물을 때만(답하는 길은 #246) |
| `suggestions[].warnings` · `general[].warnings` | 그 추천의 위험 경고 문구(위험 용어 사전의 축 문구 — 작은 부품 · 물놀이 등). Agent 가 코드로 싣고 서버는 저장한 그대로 낸다. 근거가 아니어서 `evidence` 에 없다. 없으면 `[]` (#402) |
| `groups[].cautions` | 식품 아닌 알레르기 확인 문구. `health_safety` 에서 코드로 다시 만든다(Activity 와 같은 함수) — 재생해도 같다. 동의가 없으면 `[]` |
| `general[].record_count` | 그 Agent 도메인에 남아 있는 관찰 수. 04 `agent_result.record_count` 와 같은 표(`agent_ports.RECORD_KINDS`) |
| `groups[].notice.code = "daily_limit"` | 하루 한도라 그 Agent 는 돌리지 않았다. 같은 응답에 보여 줄 다른 결과가 없으면 지금처럼 429 |
| `prechecks` | 추천 `allergens` ↔ `health_safety`(모든 kind · status) 를 같은 이름 사전으로 맞춘다. `active` 면 그 추천을 빼고, 행이 없으면 묻고, `none` · `retracted` 는 묻지 않는다. `child_health` 동의가 없으면 `allergens` 가 있는 추천을 뺀다 |

근거의 `label` · `observed_to` · `is_stale` 은 원본 행(관찰 · 관심 · 성장 기록)에서 읽는다(#323 최소분).
건강 관찰은 증상 문장을 되풀이하지 않고 고정 이름("건강 기록")으로, 성장 기록은 숫자 없이 "키 · 몸무게
기록" 으로 싣는다 — 평가처럼 읽히는 문장을 만들지 않는다. `reason` 을 객체로 바꾸는 것은 #323 에 남아 있다.

## 4. 하루 한도

Agent 를 실제로 새로 돌릴 때만 `/inputs` 와 같은 하루 한도에서 1 을 쓴다. 재생(1 · 2 · 4)은 모델을
부르지 않아 세지 않는다.

## 5. 하지 않은 것

- 만료 전 draft 를 후보 풀에서 빼기 · 예전 draft 바로 만료 — 다시 도는 건 결과를 못 낸 Agent 뿐이라
  그 run 에 겹칠 draft 가 없다. Food 의 이력 포트는 DB 어댑터가 붙을 때 같이 연결한다.
- `looked_at` · `groups[].prompt` 문구 — Agent 가 무엇을 봤는지 아직 내지 않는다.
- 날씨 안내 — 04 대화(agent_result)로만 나간다. 알레르기 확인 문구는 `cautions` 로 05 에도 나간다.

## 6. 알아 둘 것

- 일반 추천은 문서 행이 근거라, 검수를 마친(`approved`) 문서 행이 없으면 Agent 가 일반 추천을 만들지
  못한다. 지금 `activity_doc.yaml` · `food_doc.yaml` 의 행은 전부 `draft` 라서, 기록이 없는 아이에게는
  일반 추천 대신 0개 안내가 나간다. 문서 검수가 끝나야 이 경로가 실제로 열린다.
- 러너와 05 가 같은 안전 어댑터(`agent_ports.safety_readers()`)를 Food · Activity 에 넘긴다(#460). 안전 조회에
  실패하면 그 Agent 는 `blocked` 로 닫히고 05 는 guard 만 낸다.

## 7. 채택 이후의 일반 추천 (#512 · #532)

05 의 일반 추천도 개인화와 같은 길로 채택 · 일정 · 평가된다.

- 채택(`/suggestions/approve`) · 초안(`/suggestions/event-drafts`) · 제출(`/events`) · 평가(`/feedback`)는
  `kind` 를 보지 않는다. food 를 초안 한 장("저녁 식사")으로 묶는 것도 `kind` 가 아니라 agent 기준이라
  개인화 + 일반을 같이 고르면 한 장에 묶인다. 테스트가 이 동작을 고정한다 — `kind` 검사를 넣으면 깨진다.
- 07 목록(`GET /children/{cid}/suggestions`)은 둘을 함께 돌려주고 `kind` 로 구분한다. 일반 추천의
  `evidence` · `source_refs` 는 늘 빈 배열이고 `reason` 은 05 의 `basis` 와 같은 문구다.
- 채택 · 목록 · 평가 응답은 일반 추천도 `SuggestionOut` 모양이다. 05 의 `GeneralSuggestionOut` 처럼
  `record_count` · 문서 근거를 싣지 않는다 — `record_count` 는 05 응답 때 세는 값이고 저장하지 않아서,
  나중에 다시 세면 그 추천이 지금 기록에서 나온 것처럼 읽힌다. 그래서 07 의 일반 추천 줄은 라벨만 둔다.
- 품질 지표 "개인화인데 근거 0행" 은 이 목록이 아니라 저장 시점(`_checked_citations`) · eval 에서 막는다.

### 07 목록의 쪽 나누기와 `rated` 필터 (#592)

`GET /children/{cid}/suggestions` 는 기록 · 기억 목록(#584)과 같은 번호 쪽이다. 커서(`cursor` · `limit` ·
`next_cursor`)는 없다.

| 쿼리 | 기본 | 뜻 |
| --- | --- | --- |
| `page` | 1 | 쪽 번호 (1~10,000) |
| `size` | 20 | 한 쪽 개수 (1~100) |
| `status` | — | `draft` · `approved` · `rejected` · `expired`. 없으면 `draft` 를 뺀다 |
| `rated` | — | 안 보내면 전부, `false` 면 피드백을 아직 안 남긴 것만(`feedback IS NULL`), `true` 면 남긴 것만 |

- 응답은 `{items, total, page, size, total_pages}` 다. `total` 은 `status` · `rated` 를 건 **뒤의** 건수다.
  범위를 넘는 `page` 는 오류가 아니라 빈 목록이다.
- `rated` 를 안 보내면 응답은 쪽 나누기만 빼고 전과 같다. 평가한 제안도 `feedback` 값과 함께 나온다.
- 피드백은 `approved` · `rejected` 에만 저장되므로 `draft` 는 늘 미평가다. `rated=false` 여도 기본 목록의
  `draft` 제외는 그대로 걸린다. `status=draft&rated=true` 는 늘 빈 목록이다.
- 정렬은 `created_at DESC, id DESC` 다. 한 트랜잭션에서 만든 행은 `created_at` 이 같아서 `id` 로 끊어야
  쪽을 넘나들며 빠지거나 겹치지 않는다.
- 평가(`PATCH .../feedback`)는 이미 평가한 제안에도 다시 걸린다 — 덮어쓴다. `rated=true` 목록에서 다시 고를 수 있다.
- 값이 boolean 이 아닌 `rated`, 범위 밖 `page` · `size` 는 400 `validation_failed` 다 (422 아님).
- `expired` 행은 평가를 받을 수 없지만(`PATCH` 는 `approved` · `rejected` 만) 기본 목록에는 들어 있어서
  `rated=false` 에 나오고 `rated=true` 에는 나오지 않는다. 지금은 앱이 `expired` 를 DB 에 쓰지 않아(만료는
  `expires_at` 으로만 계산한다) 이런 행이 없다. 쓰게 되면 `rated=false` 에서 뺄지 다시 정한다 (테스트 R11).
- 옛 `cursor` · `limit` 은 선언하지 않은 쿼리라 오류 없이 무시된다. 첫 쪽(size 20)이 나온다.

**쪽을 쓰는 쪽(FE)이 알아 둘 것** — 서버는 요청마다 DB 의 현재 상태로 새로 계산하므로 처음 받은 응답을 들고
있을 필요가 없다. 필요한 값은 지금 보는 `page` 하나뿐이다.

1. `rated=false` 를 보다가 평가하면 그 제안이 목록에서 빠져 뒤의 항목이 한 칸씩 앞으로 당겨진다.
   평가(`PATCH`)가 성공하면 **현재 쪽을 다시 받는다.** 안 받고 다음 쪽으로 가면 앞으로 당겨진 항목 하나가
   어느 쪽에도 안 보인다 (중복은 없고 누락만 생긴다).
2. 평가로 건수가 줄면 `page` 가 `total_pages` 보다 커질 수 있다(3쪽의 마지막 하나를 평가한 경우).
   그때는 오류가 아니라 빈 목록이므로 응답의 `total_pages` 로 쪽을 다시 맞춘다.
   `rated=true` 에서 다시 고르는 것은 건수 · 순서가 바뀌지 않아 해당 없다.

### 일반 추천에 남긴 평가

지금 `feedback` 을 읽는 곳은 없다. 선호 신호로 쓰는 기능을 붙일 때 이 규칙을 따른다.

1. 저장은 `kind` 와 상관없이 한다.
2. 쓸 때는 `kind` 로 나눠 세고, 일반 추천에 남긴 평가는 개인화 근거로 쓰지 않는다 — 아이 기록에서 나온
   추천이 아니다 (루트 §2 "일반 추천과 개인화 추천은 타입으로 구분한다").
3. 평가를 관찰(Observation)로 바꿔 저장하지 않는다. 기억이 되는 길은 기록 → Curator 반복 집계 하나다
   (루트 §2 "한 번의 관찰을 성향으로 확정하지 않는다").

화면 문구 "피드백은 다음 제안의 무게만 바꿔요" 는 신호를 붙이는 PR 에서 개인화 · 일반을 같이 맞춘다.
