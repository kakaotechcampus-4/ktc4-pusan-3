---
name: screen-tour
description: apps/web 화면에 구역·카드·버튼을 더하거나 빼거나 이름을 바꾼 뒤, 그 화면의 화면 투어(처음 들어왔을 때와 ? 버튼에서 요소를 짚어 주는 단계별 설명)를 바뀐 UI 에 맞춰 고친다. 단계 정의(`lib/tour/screens/*.ts`)와 화면의 `data-tour` 를 맞추고, 실서버 화면에서 투어를 끝까지 넘겨 확인한다. 화면 작업 PR 을 올리기 전에, 새 화면에 투어를 붙일 때, "투어 업데이트해줘" 라고 할 때 쓴다.
---

# 화면 투어 맞추기

화면 투어는 **화면에 실제로 그려진 요소만** 비춘다 (`data-tour` 로 찾는다). 그래서 UI 를 바꾸고 투어를 안 고치면
깨지는 대신 **조용히 어긋난다** — 지운 구역의 단계는 그냥 빠지고, 새 구역은 아무도 설명하지 않는다.
이 스킬은 그 어긋남을 같은 PR 에서 맞추는 절차다.

규칙의 정본은 [`docs/web/screen-tour-v1.md`](../../../docs/web/screen-tour-v1.md) 다. **먼저 그 문서를 읽는다** — 여기엔 절차만 있다.

## 0. 어디가 무엇인가

| | |
| --- | --- |
| 단계 정의 (화면당 한 파일) | `apps/web/src/lib/tour/screens/<화면>.ts` |
| 투어 이름 · 모양 · `TOUR_KEYS` | `apps/web/src/lib/tour/types.ts` |
| 덮개 · ? 버튼 · 자동 시작 | `apps/web/src/components/screen-tour.tsx` (`ScreenTour` · `TourHelpButton`) |
| "봤음" 저장 (지금 기기 · 나중에 서버) | `apps/web/src/lib/tour/seen.ts` |
| 화면별 결정 표 | `docs/web/screen-tour-v1.md` §1 |
| e2e | `apps/web/e2e/screen-tour.spec.ts` · 다른 e2e 는 `fixtures.ts` 의 `toursSeen` 으로 다 본 채 시작 |

## 1. 무엇이 바뀌었나 찾는다

```bash
git diff --stat origin/develop...HEAD -- apps/web/src/app apps/web/src/components
git diff origin/develop...HEAD -- 'apps/web/src/app/child/**' apps/web/src/components | grep -nE '^[+-].*(data-tour|<Section|<Card|title=|label=|<h2|<h3)'
```

바뀐 화면마다 아래 셋 중 무엇인지 가른다.

- **구역이 생겼다** — 보호자가 처음 보고 "이게 뭐지" 할 자리인가? 아니면 설명하지 않는다. 모든 것을 비추면 아무것도 안 비춘 것과 같다
- **구역이 없어졌다** — 그 `target` 의 단계를 지운다 (남겨도 화면에서는 빠지지만 정의가 거짓이 된다)
- **이름 · 하는 일이 바뀌었다** — 단계의 `title` · `body` 를 화면 글자에 맞춘다

투어가 없는 화면(08 사진 · 11-2 검사지 · 문제 알리기 · 탈퇴)은 **새로 붙이지 않는다** — 흐름 중이거나 승인 게이트가 있는 화면이다 (문서 §1). 붙여야 할 것 같으면 사용자에게 먼저 묻는다.

## 2. 고친다

1. 비출 요소에 `data-tour="<이름>"` 을 단다
   - `Card` · `Section` · `SectionAction` · `EmptyState` · `SettingsInfoRow` 는 `data-tour` 를 받는다. 다른 primitive 가 안 받으면 **그 primitive 에 받는 길을 더한다** (`"data-tour"?: string` 을 루트에 넘긴다). `className` 덮기나 감싸는 `div` 는 마지막 수단이다 — 감싸면 `gap` · `flex` 배치가 바뀔 수 있다
   - 목록이면 **첫 줄에만** 단다 (`data-tour={i === 0 ? "row" : undefined}`). 턴이 쌓이는 04 대화는 턴마다 `data-tour="turn"` 을 달고 단계에 `within: "turn"` (가장 최근 턴 안에서만 찾는다)
   - 화면 밖까지 흐르는 줄(03 제안 알약 줄)은 안의 첫 칸에 단다 — 줄 전체를 비추면 구멍이 화면 밖으로 잘린다
2. `lib/tour/screens/<화면>.ts` 에 단계를 더하거나 지운다. 순서는 **보호자가 화면을 읽는 순서**(대개 위에서 아래)다
3. 지킬 것 (문서 §3 · §4 — 하나라도 어기면 고친다)
   - 🚨 화면당 **6단계 이하**. 넘으면 덜 중요한 것을 뺀다 (? 안내 단계는 자동이 붙인다 — 세지 않는다)
   - 🚨 **투어 이름(`TourKey`)을 바꾸지 않는다** — 이미 본 사람에게 다시 뜬다. 화면이 바뀌어도 다시 띄우지 않기로 했다
   - 🚨 화면에 적힌 이름 그대로 부른다. Agent · Memory · run · observation · affinity 같은 안쪽 말 금지 (기록 · 기억)
   - 🚨 AI 를 부풀리지 않는다 · 키몸무게에 평가 말 금지("잘 크고", "또래보다", "정상") · 알레르기는 "AI 는 이 목록을 만들지도 고치지도 못해요"
   - 🚨 승인 게이트(일정 넣기 · 알레르기 등록) 시트 안을 비추지 않는다 — 여는 버튼까지만
   - 🚨 화면에 보이는 글자에 이모지 · 줄표(`—`) 금지 (apps/web/CLAUDE.md §5). 가운뎃점은 화면 문구 관례를 따른다
4. **새 화면에 투어를 붙일 때만**
   - `types.ts` 의 `TOUR_KEYS` 에 이름을 더한다 (e2e 바탕이 이걸로 "다 봤음" 을 심는다 — 빠지면 그 화면을 여는 다른 e2e 가 덮개에 막힌다)
   - 헤더 오른쪽 끝에 `<TourHelpButton tour={…} />`, 화면 아무 데나 `<ScreenTour tour={…} ready={…} />`
   - `ready` 는 **비출 것이 다 받아졌다**다 (`query.isSuccess` 등). 진행 중 · 막 보내는 중에는 `false`. 곁가지 화면이면 정의에 `auto: false`
   - 문서 §1 표에 한 줄 더한다

## 3. 실서버 화면에서 끝까지 넘겨 본다

목이 아니라 **실서버**로 본다 — `ready` 가 켜지는 순간에 비출 것이 정말 그려져 있는지는 실서버에서 드러난다
(05 에서 `Swap` 때문에 받은 직후엔 DOM 이 비어 있었다 · 문서 §3).

전제: `make db-up` 의 로컬 DB(`ktc4-postgres`), API(`localhost:8000`), 웹 개발 서버(`localhost:3000`, `NEXT_PUBLIC_API_MOCKING=disabled`)가 떠 있다.
떠 있지 않으면 사용자에게 띄워 달라고 한다 — 남이 띄운 서버를 내리거나 다시 띄우지 않는다.

```bash
# 로그인 세션을 DB 에 넣고 쿠키를 담은 상태 파일을 만든다 (카카오 로그인 우회 · 로컬 DB 전용). child id 도 찍는다.
.claude/skills/screen-tour/login.sh "$SCRATCH/state.json"

# 화면마다 투어를 넘기며 스크린샷. 이미 본 화면이면 @help 로 ? 를 눌러 연다.
cd apps/web && node ../../.claude/skills/screen-tour/walk.mjs "$SCRATCH/state.json" "$SCRATCH" \
  "home@/child/<cid>/home@help" "mem@/child/<cid>/memories@help"
```

- 찍힌 `<이름>-<n>.png` 를 **직접 열어 본다.** 단계 번호 · 제목이 맞아도 구멍이 엉뚱한 곳을 비추거나 말풍선이 비춘 것을 덮는 일은 그림에만 보인다
- 비출 데이터가 없으면(기록 0건 등) 단계가 빠지는 것이 맞다. 그 단계까지 보려면 데이터를 만든다
  - 가장 정직한 길은 **실제 화면에서 한 줄을 보내는 것**이다 (LLM 파이프라인이 기록 · 일정 초안을 만든다). 🚨 하루 한도가 보호자당 `INPUT_DAILY_LIMIT`(5)이고 API 프로세스 메모리에 있어서, 다 쓰면 제안 화면도 막힌다. 아껴 쓴다
  - 반복된 기억 · 키몸무게 · 알레르기처럼 한 줄로 못 만드는 것은 로컬 DB 에 직접 넣어도 된다 (사용자 허락이 있을 때). 🚨 실제 아이 정보를 지어 넣지 말고 예시임이 드러나게 쓴다 (`(로컬 예시)`)
- 상태 파일은 스크립트가 끝날 때 다시 저장된다 (refresh 회전). 스크립트가 중간에 죽으면 `login.sh` 를 다시 돌린다

## 4. 테스트 · 문서

- 투어의 **동작**(자동 시작 · 그만 보기 · 누르기 막힘)을 바꿨으면 `e2e/screen-tour.spec.ts` 를 고친다. 단계 문구 · 목록만 바꿨으면 고칠 것이 없다 — 문구를 e2e 에 박지 않는다
- 규칙을 바꿨으면 `docs/web/screen-tour-v1.md` 를 같은 PR 에서 고친다
- 검사는 바뀐 파일만: `pnpm exec eslint <파일>` · `pnpm typecheck`. 전체 검사는 커밋 직전에 한 번 (프론트 작업 관례)

## 하지 않는 것

- 투어를 채우려고 **예시 데이터를 화면에 띄우지 않는다** — 기록이 없는데 기록 카드를 보이면 우리 아이를 아는 척하는 화면이다
- 투어 때문에 화면 구조를 바꾸지 않는다 (비출 자리를 만들려고 카드를 쪼개는 등). 투어가 화면을 따라간다
- "봤음" 저장소를 화면에서 직접 만지지 않는다 — `lib/tour/seen.ts` 의 훅만 쓴다
