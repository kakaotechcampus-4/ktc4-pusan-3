# apps/web — 프론트 구현 컨텍스트

Owner: 고태영 (프론트 리드)

> 이 파일은 **프론트에서만 지키는 규칙**이다. 파트 경계를 넘는 규칙은 [최상위 CLAUDE.md](../../CLAUDE.md) §2 에 있다.
> 작업 전에 읽을 것: 최상위 `CLAUDE.md` (§2·§3·§5) → 이 파일 → [`docs/api/api-interface-v1.html`](../../docs/api/api-interface-v1.html) (무엇을 부르는가) → [`docs/web/design-system-v1.md`](../../docs/web/design-system-v1.md) (무엇으로 그리는가).
> 로그인은 계약서 §04 가 아니라 [`docs/api/auth-kakao-v1.md`](../../docs/api/auth-kakao-v1.md) (서버 정본) · [`docs/web/kakao-login-v1.md`](../../docs/web/kakao-login-v1.md) (프론트) 를 본다.
> 되돌릴 수 없는 5개를 건드린다면 [`docs/api/idempotency-v1.md`](../../docs/api/idempotency-v1.md) 도 읽는다 (승인 게이트 2곳이 거기 있다).

---

## 1. 스택 · 버전

| | 버전 | 비고 |
| --- | --- | --- |
| Next.js | 16.3.4 | App Router · Turbopack. `next lint` 는 16 에서 빠졌다 — lint 는 별도 스크립트 |
| React | 19.2.8 | |
| TypeScript | **6.0.3** | ⚠️ 아래 참고 |
| Tailwind CSS | 4.3.3 | CSS-first. `tailwind.config.*` 없음 — 토큰은 `globals.css` 의 `@theme` |
| Zustand | 5.0.15 | 클라이언트 상태 전용 |
| TanStack Query | 5.102.8 | 서버 상태 전용 |
| Zod | 4.5.4 | 환경변수 검증 · (필요해지면) 응답 파싱 |
| ESLint | 10.10.0 | `eslint-config-next` flat config |
| Prettier | 3.9.6 | `prettier-plugin-tailwindcss` 로 클래스 정렬 |

### ⚠️ TypeScript 는 왜 7 이 아닌가

TS 7 (네이티브 컴파일러) 이 최신이지만 **`typescript-eslint` 가 아직 지원하지 않는다**
(`typescript-eslint does not support TS 7.0` 로 lint 가 죽는다 · [typescript-eslint#10940](https://github.com/typescript-eslint/typescript-eslint/issues/10940)).
그래서 툴체인 전체가 도는 최신 버전인 **6.0.3** 에 맞춰 뒀다. 위 이슈가 닫히면 그때 7 로 올린다.

`eslint.config.mjs` 의 `settings.react.version` 도 같은 종류의 임시 조치다 —
`eslint-plugin-react` 의 자동 버전 탐지가 ESLint 10 API 와 안 맞아 터진다. **React 버전을 올리면 이 값도 같이 올릴 것.**

---

## 2. 폴더 구조

`src/` 아래를 열어 보면 나오는 것은 안 적는다. **열어 봐도 안 보이는 것만** 적는다.

- `app/` = App Router. **라우트가 곧 화면 00~10** 이고, 대응표는 아래 §3 라우팅에 있다
- 🚨 `app/hakgyoansim.css` · `app/pretendard.css` 는 **생성된 파일이다.** 손으로 고치지 않는다 (§5 서체)
- 🚨 `app/auth/callback/` 은 **`AuthGate` 로 감싸지 않는다** — 토큰을 얻으러 가는 화면이다
- 🚨 `app/onboarding/` 만 아이 스코프 **밖**이다 — 아직 `childId` 가 없다 (§3 라우팅)
- `lib/api/` = 계약서 v1 타입 · fetch 클라이언트 · SSE 파서 · 쿼리 키 팩토리. **`fetch` 를 직접 부르지 않는다** (§3 API 호출)
- ⚠️ `lib/cn.ts` 는 **tailwind-merge 가 아니다** — 뒤에 온 클래스가 앞을 안 덮는다 (§3 컴포넌트)
- 🚨 `lib/format.ts` 는 **날짜 계산이 아니라 표시 변환**이다. 절대 시각 하나를 한국 시간대 표기로
  바꾸는 것이 전부고, 상대 시간·나이·기간은 여기서도 만들지 않는다 (§4)
- `lib/auth/oauth-bind.ts` = bind 비밀. 만지기 전에 그 파일 주석을 읽는다 (§3 로그인)
- `components/ui/` = 토큰만 아는 primitive. **도메인 타입을 import 하지 않는다** / `components/` = 도메인을 아는 조합
- `mocks/` = MSW 목 서버, **개발 환경 전용** (§7)
- `stores/` = Zustand, **클라이언트 상태만.** 서버 상태는 TanStack Query (§3).
  🚨 `stores/draft.ts` 는 **메모리 전용**이다 — 아직 안 보낸 발화 원문이라 `persist` 금지
- `public/mockServiceWorker.js` 는 msw 가 생성한 파일이다. 손으로 고치지 않고 lint·prettier 대상에서 빼 뒀다

화면을 붙일 때는 [`docs/api/api-interface-v1.html`](../../docs/api/api-interface-v1.html) 의 **화면 → 호출** 표를 기준으로 잡는다.

---

## 3. 구현 관례

### 라우팅 — 아이 스코프는 URL 에 둔다

| 화면 | 라우트 |
| --- | --- |
| 00 소개 · 로그인 | `/` |
| 로그인 복귀 지점 (보여줄 내용 없음) | `/auth/callback` |
| 가입 동의 (신규 회원만) | `/auth/consent` |
| 01 첫 진입 (아이 만들기) | `/onboarding` |
| 02 이야기 하나 | `/child/[childId]/onboarding` |
| 03 홈 + **04 진행·저장 결과** | `/child/[childId]/home` |
| 05 제안 후보 | `/child/[childId]/suggestions?agents=food,activity&run=…` |
| 06 승인 | 05 위의 바텀시트 (라우트 없음) |
| 07 기억 | `/child/[childId]/memories?tab=observations\|profile\|feedback` |
| 09 캘린더 | `/child/[childId]/calendar?date=YYYY-MM-DD` |
| 10 설정 | `/child/[childId]/settings` (자리만 있고 내용은 다음 이슈) |
| 디자인 시스템 (내부 문서) | `/design-system` |

`/onboarding` 만 아이 스코프 **밖**이다 — `POST /children` 이 성공해야 `childId` 가 생기고, 그때 `/child/{cid}/onboarding` 으로 넘어간다. 이 경계를 흐리면 childId 가 없는 상태의 아이 스코프 라우트가 생긴다.

🚨 **`useSearchParams()` 를 쓰는 화면은 `<Suspense>` 경계 안에 둔다.** 경계가 없으면 프리렌더가
CSR bailout 을 일으켜 **프로덕션 빌드가 그 화면에서 멈춘다** (`Missing Suspense boundary with useSearchParams`).
`pnpm dev` 에서는 드러나지 않아서 `pnpm build` 로만 잡힌다 — 실제로 `/auth/callback` 이 그렇게 빠져 있었다 (PR #71 리뷰).
위 표에서 `?` 가 붙은 화면(05 · 07 · 09)과 `/auth/callback` 이 대상이다. `fallback` 에는 그 화면이
hydrate 직후 그릴 것과 **같은 것**을 둔다. 다른 것을 끼우면 정적 HTML 과 hydrate 결과가 한 번 어긋나 깜빡인다.

🚨 **04 저장 결과에 라우트를 만들지 않는다.** 화면을 벗어나면 `useRunStream` 이 스트림을 끊는데,
`failed` 일 때 입력창에 되돌릴 **원문의 정본은 03 홈이 들고 있는 `text`** 다 (아래 run 상태 항목).
라우트를 나누면 그 값이 언마운트와 함께 죽는다 — 그래서 03 이 run 이 도는 동안 본문만 바꿔 그린다.
프로토타입에서 04 가 별도 화면으로 보이는 것은 뒤로가기 화살표 때문이지 주소가 달라서가 아니다.

🚨 **화면이 읽는 `childId` 의 정본은 URL 이다.** `stores/session.ts` 의 `activeChildId` 는
"마지막에 본 아이" 복원용일 뿐이고, `components/child-scope.tsx` 가 URL → 스토어 **한 방향으로만** 흘린다.

- 🚨 **스토어는 서버 컴포넌트에서 못 읽는다.** `localStorage` 기반이라, 서버가 아이를 아는 방법은
  URL 아니면 쿠키뿐이다. 쿠키로 옮기면 쿠키·스토어·쿼리 키 세 곳이 어긋날 때 **다른 아이의 데이터가
  보일 수 있다** (§2 개인정보). URL 이면 그 사고가 구조적으로 불가능하다 — 이게 URL 을 고른 첫째 이유다
- 뒤로가기가 웹뷰 히스토리 기반이라([`apps/mobile/App.tsx`](../mobile/App.tsx)), 아이를 바꾼 게 히스토리에 안 남으면 뒤로가기가 어긋난다
- 쿼리 키가 이미 `qk.child(cid)` 스코프라 URL 파라미터와 1:1 이다

⚠️ 계약서의 `deeplink` 는 `settings/consent` 처럼 **아이를 안 담은 상대 경로**다.
지금 보고 있는 아이 경로 아래에 붙여서 쓴다 (`/child/{childId}/settings/consent`).

클라이언트 컴포넌트에서는 `useChildId()`, 서버 컴포넌트에서는 `params` 를 그대로 쓴다.
**스토어를 읽어 화면을 그리지 않는다.**

### 컴포넌트 — 직접 만든다

별도 UI 라이브러리를 쓰지 않는다. 토큰과 1:1 로 붙고 고치기 쉬운 쪽을 골랐다.
사양은 [디자인 시스템 §7](../../docs/web/design-system-v1.md) 에 있다 — 없는 값을 즉석에서 만들지 않는다.

- `components/ui/` — 토큰만 아는 primitive. 도메인 타입(`Suggestion` 등)을 import 하지 않는다
- `components/` — 도메인을 아는 조합
- 지금 있는 것 (`components/ui/`) — `Screen`(최대 폭·좌우 여백·**상하 여백+safe area**) · `PageTitle` ·
  `Button`(§7 6변형) · `TextInput` · `TextArea` · `DateField` · `Checkbox` · `Chip`/`ChipRow` ·
  `EvidenceChip`/`CountChip`/`EvidenceRow` · `Card`(`accent`)/`CardFailed` · `Banner` · `Spinner` ·
  `IconButton` · `IconTile` · `ProgressSteps` · `EmptyState` · `Skeleton` · `BottomSheet` · `Tabs` · `Toast` · `Select`
- 도메인을 아는 조합 (`components/`) — `DomainChip`/`DomainMeta` · `AgentPrompts` · `ChildNav` · `SuggestionList` ·
  `HomeComposer` · `GeneralSuggestionCard` · `RunProgress`/`RunResult` · `ApprovalSheet` · `ConsentRequiredCard` ·
  `AuthGate` · `ChildScope` · `ObservationList` · `AffinityList` · `CorrectionButtons` · `MemoryDetailSheet` ·
  `SuggestionFeedbackList` · `MonthGrid`/`DayMarkLegend` · `CalendarDayPanel`
- 🚨 **화면 하단 고정 바는 `Screen` 의 `bottomBar` · `nav` 로 넘긴다.** 화면이 직접 `sticky` 를 붙이면
  아래 여백을 0 으로 되돌려야 하는데, 그게 상하 여백을 두 번 죽인 바로 그 조작이다.
  둘 다 넘기면 채팅바가 위·네비가 아래로 한 덩어리가 되고 **safe area 는 제일 아래 것만** 받는다
- 🚨 **하단 네비(`ChildNav`)는 가는 곳 세 화면과 설정에만 붙인다.** 04 저장 결과·05 제안 후보처럼
  흐름 중인 화면에 붙이면 고르는 도중에 새는 길이 생겨 그 화면이 끝나지 않는다 (디자인 시스템 §7)
- 🚨 **네비는 `surface-muted` 면이고 위에 선이 없다.** 선을 하나 더 긋는 것으로는 채팅바와 안 갈린다 —
  `line` 1px 은 `canvas` 위에서 1.21:1 이고 03 홈에는 **같은 선이 27px 위에도** 있어서, 한 신호가
  "여기부터 고정" 과 "여기부터 다른 종류" 를 나눠 쓰면 하단이 줄 쳐진 슬래브 하나로 읽힌다
- 🚨 **켜진 탭에 모양 신호를 함께 준다** (칸 위쪽 `brand` 2px). `brand` 와 `ink-subtle` 은 휘도 차가
  1.15:1 이고 본문 서체가 단일 웨이트라 굵기로도 못 만든다 — 색만 두면 단독 신호가 된다 (디자인 시스템 §3)
- 🚨 **설정에서 `aria-current` 를 붙이지 않는다** (`onRoute={false}`). 홈 칸을 켜 두는 것은 시각적
  결정이고, 제목이 "설정" 인 화면에서 "홈, 현재 페이지" 라고 읽히면 그건 사실이 아니다
- 🚨 **네비가 가리키는 곳에는 라우트가 먼저 있어야 한다.** 07·09·10 은 내용이 생기기 전에도 화면을 뒀다 —
  아무 데도 안 가는 탭을 만들지 않는다 (00 로그인의 `ready:false` 와 같은 원칙). 지금 남은 자리표시는 10 하나다
- 🚨 **브랜드색은 정해진 다섯 자리에만 쓴다** (디자인 시스템 §2-2 표). 밋밋하다고 아무 데나
  초록을 넣으면 "색 하나 = 뜻 하나" 가 무너진다. `brand-soft` 로 **큰 면을 칠하지 않는다** —
  제안이 앉는 색 면은 **그 제안의 도메인 색**이고(§2-3), 브랜드는 고르는 버튼이 가져간다.
  "어디서 왔나"(도메인)와 "무엇을 하는가"(브랜드)를 같은 색으로 쓰지 않는다
- `card-photo`(08)는 그 화면 이슈에서 만든다
- 🚨 **07 의 도메인 색은 왼쪽 아이콘 타일 하나까지다** (디자인 시스템 §3 예외 ㉡). 도메인 색의 뜻을
  "어느 Agent 결과인가" 에서 **"어느 영역인가"** 로 넓히면서 열린 자리다 — 07 은 제안이 아니라
  쌓인 것을 훑는 화면이고, 목록이 네 영역을 섞어 내려주므로 "한 화면에 2개" 상한의 예외이기도 하다
  (그 상한은 제안 화면의 규칙이다). 🚨 **색 면을 타일 밖으로 넓히지 않는다** — 줄 전체를 칠하면
  05 의 열린 제안 줄과 같은 언어가 되어 "고를 수 있는 것" 으로 읽힌다.
  🚨 **영역 이름은 항상 글자로 함께 선다** (색이 단독 신호가 될 수 없다).
  🚨 **브랜드는 여전히 못 쓴다** — "어디서 왔나"(도메인) 와 "무엇을 하는가"(브랜드) 를 같은 색으로 쓰지 않는다
- 🚨 **09 월 그리드의 표식은 색이 아니라 모양이다** (디자인 시스템 §7 캘린더 그리드). 전부
  `currentColor` 라 고른 날·오늘·비활성의 글자색을 따라간다 — 표식에 색을 주면 도메인 4색과 섞인다.
  🚨 **범례를 지우지 않는다.** 모양을 뜻으로 잇는 자리가 거기 하나뿐이라, 없으면 단독 신호가 된다
- 🚨 **화면에서 부르는 말과 코드의 이름이 다르다** — 관찰(`observation`)은 **기록**, 프로필
  (`profile_affinity`)은 **기억**으로 보여준다. 🚨 **코드·타입·쿼리 키의 이름은 계약서를 따른다.**
  화면 문구만 바꾸고 식별자를 같이 바꾸지 않는다 (바꾸면 계약서와 어긋난다)
- 🚨 **관찰(`ObservationList`)과 프로필(`AffinityList`)을 한 컴포넌트로 만들지 않는다.** 줄과 카드로
  모양이 다른 것이 이 화면의 요점이다 — 한 목록에 섞으면 "한 번 본 것" 과 "확정된 성향" 이 같은
  무게로 읽혀서 최상위 §2("한 번의 관찰을 성향으로 확정하지 않는다")가 화면에서 사라진다.
  두 목록은 **같은 아이콘 타일**을 왼쪽에 세운다 — 한 화면의 두 탭이라 기준선이 다르면 남남으로
  읽힌다. 🚨 `is_stale` 기억 카드만 뉴트럴 타일이다 (카드 전체가 `surface-muted` 인데 색 타일이
  서면 "근거에서 빠져 있다" 를 화면이 되받아친다)
- 🚨 **교정은 기록과 기억이 서로 다른 것을 묻는다** (디자인 시스템 §7 "교정"). 기록은
  `once_only`·`wrong` 둘, 기억은 `need_more_observation`(⚠️ 계약서에 없는 값)·`outdated`·`wrong` 셋이다.
  `confirm` 은 이력에만 남고 버튼으로 세우지 않는다 — 타입에 남아 있는 것은 그 이력 때문이다
- 🚨 **고르면 확인 단계가 뜨지만 그건 승인 게이트가 아니다.** 승인 게이트는 딱 2곳이고 늘리지
  않는다 (최상위 §2) — `btn-approve` 도 `caution` 도 쓰지 않는다. 게이트가 되돌릴 수 없는 것을
  막는 장치라면 이 단계는 **되돌릴 수 있다는 사실까지 같이 말해 주는** 자리다.
  🚨 확인 문구에 예측을 쓰지 않는다(일어날 일만) · 성공하면 패널을 닫고 버튼으로 되돌린다
  (`key={result?.correction.id}`) · 실패하면 남는다(다시 누르는 것이 재시도다)
- 🚨 **라벨에 조사를 박지 않는다.** `josa(label, "으로/로")`(`es-hangul`)가 받침으로 고른다 —
  라벨은 데이터고 조사는 문법이라, 박아 두면 다른 문장에서 못 쓴다.
  ⚠️ 옵션 키는 **받침형이 앞**이다(`"으로/로"`). `"로/으로"` 는 타입 에러다 교정은 append-only 라
  반대 교정으로 되돌린다 — `wrong` 도 `btn-danger` 가 아니다.
  🚨 **교정을 되돌리는 기능은 주지 않는다** (제품 결정). `wrong`·`outdated` 를 누르면 그 줄이
  목록에서 사라지고 다시 꺼내 볼 길이 없다 — 그래서 **확인 단계가 그 무게를 진다.**
  🚨 화면 문구도 "다시 고칠 수 있어요" 라고 쓰지 않는다. 한동안 "고쳐서 뺀 기록" 필터로 되살릴 수
  있게 뒀다가 내린 자리라, 문구가 남으면 **찾지 못할 길을 약속**하게 된다.
  🚨 같은 이유로 기억 탭의 상태 필터에 **`archived` 를 두지 않는다** — 목록에 없는 것을 필터로만
  되살리면 되돌릴 수 있다는 기대를 만들면서 수단은 안 주는 셈이다
- 🚨 **기록과 기억을 한 단어로 묶지 않는다.** 목록은 줄과 카드로 갈라 놓고 교정 시트의 문구가
  둘을 도로 합치면, 부모는 기억을 고치면서 자기가 기록 한 건을 고치는 줄 안다
  (`CorrectionButtons` 와 `CascadeResult` 가 `targetKind` 로 갈린다)
- 🚨 **일기를 관찰과 같은 구역·같은 표식으로 그리지 않는다.** 일기는 관찰로 자동 추출되지 않는다
  (계약서 §09). 같은 점을 찍거나 한 목록에 합치면 화면이 그 규칙의 반대말을 한다
- 🚨 **"채우려고 만들지 않아요" 와 채우는 버튼을 붙여 놓지 않는다.** 빈 날의 `EmptyState` 에 일기 쓰기
  버튼을 달았더니 화면이 스스로를 부정했다 — 사실을 말하는 자리와 할 일을 주는 자리를 나눈다.
  대신 일기 구역은 빈 날에도 **다른 날과 같은 모양으로** 선다 (빈 날에만 다른 버튼을 찾게 하지 않는다).
  같은 이유로 요약 줄은 빈 날에 아무 말도 하지 않는다 — 바로 아래가 같은 말을 더 크게 한다
- 🚨 **일반 추천(`GeneralSuggestionCard`)과 개인화 목록(`SuggestionList`)은 다른 컴포넌트 · 다른 타입 ·
  응답의 다른 필드다.** 한 곳에 플래그로 섞으면 언젠가 근거 0건인 것이 개인화로 그려지고, 그러면
  "개인화인데 근거 0행이면 버그" 라는 하드 기준이 무의미해진다 (최상위 §2).
  `GeneralSuggestion` 에는 `evidence` 필드가 **아예 없어서** 개인화 쪽에 넘기면 컴파일이 막는다.
  ⚠️ `SuggestionsResponse.general` 은 **계약서 v1 에 아직 없다** — 제안 형태로 두고 optional 로 받는다
  (서버가 안 보내면 질문 1개만 그린다). 👉 `apps/api` Owner 협의 대상 (최상위 §8)
- 🚨 **07 목록의 필터는 계약서가 주는 것만 만든다** (`?domain=` · `?unused_in_suggestions=` ·
  기억 탭의 `?state=`). 🚨 **정렬을 만들지 않는다** — 두 엔드포인트 다 정렬 파라미터가 없고
  관찰은 `observed_to DESC` 고정이다. 커서 페이지네이션이라 받아 온 쪽만 다시 정렬하면 다음 장을
  붙이는 순간 순서가 어긋난다. 정렬이 필요하면 계약서 먼저다 (최상위 §8)
- 🚨 **고르기 상자(`Select`)는 직접 만든 드롭다운이다.** 네이티브 `<select>` 는 닫혀 있을 때
  말고는 생김새를 우리가 못 정해서 쓰지 않기로 했다 — 대신 **키보드 · 포커스 · 스크린리더 ·
  바깥 클릭이 전부 그 파일의 책임**이 된다. 그 파일의 🚨 를 지우지 말 것:
  ARIA 는 `combobox` + `listbox` 한 쌍 · 열 때 고른 항목으로 포커스가 들어가고 **닫을 때 버튼으로
  돌아온다**(안 그러면 포커스가 `<body>` 로 떨어진다 · 09 달력에서 낸 사고와 같다) ·
  ESC · 바깥 클릭 · Tab · 스크롤에 닫힌다 · 그림자 없이 `line-strong` 1px 로 뜬 면을 만든다 ·
  등장 애니메이션도 쉐브론 회전도 없다(방향은 아이콘을 갈아 끼워 말한다)
- 🚨 **화면을 그리는 외부 라이브러리는 `<dialog>`(시트) 와 `react-day-picker`(달력) 둘뿐이다.**
  접근성을 손으로 짜면 반드시 빠뜨리는 것만 예외로 얹는다. 달력은 **기본 CSS 를 불러오지 않고**
  `classNames` 로 토큰만 입힌다 — 버튼·입력을 주는 UI 킷은 계속 쓰지 않는다 (디자인 시스템 §7).
  🚨 고르기 상자(`Select`)는 그래서 **직접 만들었다** — 예외를 늘리지 않았다
- **화면을 안 그리는 것은 이 규칙 밖이다.** 지금 있는 것은 `es-hangul`(조사) 하나다 —
  한글 받침 규칙은 손으로 짜면 `ㄹ` 예외 같은 데서 틀리는데, 화면 생김새에는 아무 영향이 없다.
  🚨 **여기에 UI 를 그리는 것을 끼워 넣지 않는다** — 그러면 위 규칙이 이 줄로 빠져나간다
- 🚨 **`<Screen>` 에 `py-*` 를 넘기지 않는다.** 상하 여백은 `Screen` 이 소유한다.
  safe-area 유틸은 **여백을 함께 받는다** (`pt-safe-8` = safe area + 32px) — 예전처럼 safe area 만 넣는
  유틸에 `py-8` 을 겹치면 같은 padding 속성이라 **한쪽이 조용히 죽는다.** 이 사고를 두 번 냈다:
  화면 5개의 상하 여백이 0 이었고, 바텀시트 닫기 버튼이 화면 맨 아래에 붙었다
- 🚨 **화면 제목은 `PageTitle` 로 쓴다.** §9 가 좁은 폰(< 380px)에서 `display` → `title` 로 낮추라고
  정했는데, 화면마다 손으로 쓰면 어긋난다
- ⚠️ `cn()` 은 **tailwind-merge 가 아니다.** 뒤에 온 클래스가 앞을 덮어주지 않으니, primitive 밖에서
  `className` 으로 색·크기를 덮어쓰지 않는다. 필요하면 변형을 primitive 안에 추가한다
- ⚠️ 라이브러리를 안 쓰는 대신 **접근성이 전부 우리 책임**이다. 포커스 트랩·ESC·스크롤 락을 직접 짜야 한다
- 🚨 **토스트는 "조용히 되돌아간 실패" 에만 쓴다** (디자인 시스템 §7 토스트). 성공은 화면이 이미
  말하고, 되돌릴 것이 있으면 화면 안에 자리를 만든다 — 토스트는 사라지므로 되돌릴 길을 담으면
  길이 같이 사라진다. 🚨 **바텀시트 안에서 부르지 않는다** — `<dialog>` 의 top layer 뒤로 깔려
  안 보인다. 🚨 발화 원문을 싣지 않는다 (최상위 §2)
- 🚨 **바텀시트는 네이티브 `<dialog>` 위에 얹는다.** 포커스 트랩 · ESC · 바깥 `inert` · 스크림을 브라우저가 준다.
  "승인 시트는 스크림 탭으로 닫히지 않는다"(디자인 시스템 §7)는 `cancel` 이벤트를 막아 처리한다

### 로그인 — API 호출이 아니라 페이지 이동이다

정본은 [`docs/web/kakao-login-v1.md`](../../docs/web/kakao-login-v1.md) 다. 여기는 코드를 만질 때 걸리는 것만.

🚨 **계약서 §04 의 `{access_token}` 은 쓰지 않는다.** 클라이언트가 카카오 토큰을 받는 경로가 없다 (카카오 JS SDK 에 그 메서드가 없고, 셸이 네이티브 SDK 로 받으면 [`apps/mobile/CLAUDE.md`](../mobile/CLAUDE.md) §1 "토큰을 들고 있지 않는다" 가 깨진다). 서버가 인가 코드를 교환하고, 프론트는 **1회용 코드 + `bind` 비밀** 한 쌍만 보낸다.

```
00 화면(/) — status prefetch → 버튼 → 서버가 준 절대 start_url 로 이동
   → 카카오 → 서버 콜백 → /auth/callback?code=  → POST /auth/{provider} { code, bind }
```

- 🚨 **`lib/auth/oauth.ts` 가 시작하는 유일한 지점이다.** 화면은 `startOAuthLogin()` 만 부른다. 성공하면 페이지가 떠나므로 값이 돌아오지 않는다 (`useMutation` 이 아니다). 목에서만 카카오 왕복을 건너뛰고 `{ kind: "internal" }` 을 돌려줘서 호출자가 라우터로 콜백 화면에 간다
- 🚨 **`bind` 를 빼먹으면 계정이 넘어간다.** 남의 1회용 코드를 딥링크로 던져 **피해자를 공격자 계정에 로그인**시킬 수 있다. 시작 단계의 `state` 쿠키로는 못 막는다 — 막아야 하는 홉이 그 뒤다. `oauth-bind.ts` 의 주석을 읽고 만질 것
- 🚨 **교환 응답은 `status` 필드 유무로 분기한다.** `token` 유무로 보면 안 된다 — 신규 응답에는 `token` 이 아예 없고, 그 상태로 `signIn(undefined)` 를 부르면 토큰 없는 세션이 저장돼 이후 모든 요청이 401 이 된다
- 🚨 **에러 문구는 프론트가 만든다.** 서버는 `?error=` 에 **코드만** 싣는다 (문구를 URL 에 실으면 임의 문구를 화면에 띄우는 통로가 된다). 매핑은 콜백 화면 한 곳에 두고, **받은 코드를 화면에 그대로 출력하지 않는다**
- 🚨 **취소는 실패가 아니다.** `?error=oauth_denied` 와 인앱 브라우저 닫기는 로그인 시작 전으로 돌아간 것뿐이다 — 배너를 띄우지 말고 버튼만 원상복구한다
- 🚨 **복귀 경로 복원값을 검증한다.** `/` 로 시작하고 `//` 가 아닌 값만 통과시킨다 (`//evil.com` 은 프로토콜 상대 URL 이라 외부로 나간다). 서버가 오픈 리다이렉트를 막았는데 프론트가 다시 열면 의미가 없다
- 🚨 **자리표시 코드는 개발 환경 + 목 서버일 때만 나간다.** 프로덕션 빌드에서 로그인이 되는 것처럼 보이는 경로를 만들지 않는다 (빌드 후 번들에서 문자열이 사라지는지 확인한다)
- 🚨 **1회용 코드는 한 번만 교환한다.** StrictMode 의 이중 실행으로 두 번 소비하면 두 번째가 `401 invalid_handoff` 다 — `useRef` 가드를 둔다
- 🚨 **`/auth/callback` 은 `AuthGate` 로 감싸지 않는다.** 토큰을 **얻으러** 가는 화면이라 감싸면 `/` 로 튕긴다
- **신규 회원은 `/auth/consent` 로 보낸다.** 동의 4건을 받아야 계정이 만들어진다 — 계정 2건은 `signup`, 아이 2건은 `POST /consents` 다.
  🚨 **아이 스코프를 아이보다 먼저 받는다** — `child_basic` 없이 `POST /children` 은 403 이다 (계약서 §04 "동의는 저장보다 먼저다")
  🚨 **"전체 동의" 를 만들지 않는다** — 민감정보(`child_health`)는 다른 동의와 구분해서 받아야 한다 (개인정보보호법 제23조)
  🚨 **승인 게이트가 아니다** — `btn-approve` · `caution` 을 쓰지 않는다. 그 둘은 되돌릴 수 없는 2곳 전용이다 (§2)
  스코프 목록·약관 버전의 정본은 `lib/consent.ts` 다. 10 설정의 동의 관리도 같은 파일을 쓴다
- 기존 회원인데 `consent_required` 가 남아 있으면 **콜백 화면에서 멈춘다.** 10 설정의 동의 화면이 아직 없다

### 세션 — `sessionStorage` 다

- 🚨 **토큰을 `localStorage` 에 두지 않는다.** [`docs/api/auth-kakao-v1.md`](../../docs/api/auth-kakao-v1.md) §4-3 이 못박은 규칙이고, 이유는 아래 XSS 항목과 한 덩어리다
- 토큰은 불투명 난수다. **JWT 가 아니니 디코딩해서 정보를 꺼내려 하지 않는다.** 수명 12시간 · refresh 없음
- `signIn(token, expiresIn)` 이 만료 시각을 함께 든다. `hasLiveSession()` 으로 판정한다 — 401 을 맞고 나서야 아는 것보다 낫다
- **`401 unauthenticated` 는 `lib/api/client.ts` 에서 한 번에 처리한다** (Providers 가 핸들러를 꽂는다). 화면마다 401 을 다루지 않는다. 🚨 `invalid_handoff`(401) 는 여기 안 걸린다 — 그건 로그인 교환 실패고 콜백 화면이 자기 문구로 처리한다
- 인증이 필요한 화면은 `components/auth-gate.tsx` 로 감싼다. `hydrated` 전에는 아무것도 그리지 않는다 —
  토큰이 `sessionStorage` 에 있어서 서버 렌더 시점엔 항상 `null` 이고, 그때 그리면 로그인한 사용자에게도 화면이 깜빡이고 튕긴다
- ⚠️ 부수 효과로 `activeChildId` 도 세션 스코프가 됐다. "마지막에 본 아이" 복원이 탭을 닫으면 사라진다 — 정본은 URL 이라 화면이 깨지지는 않는다

### 아이콘 — `lucide-react`

Next 16 기본 `optimizePackageImports` 목록에 있어서 배럴 임포트를 그대로 써도 트리셰이킹된다
(`next.config.ts` 에 설정할 것 없음. 아이콘 1개 추가에 번들 +12KB 로 확인).

크기 · 굵기 · 도메인 매핑은 `components/ui/icon.tsx` 한 곳에 있다. 화면에서 `size={18}` 처럼 직접 쓰지 않는다.

🚨 `DomainIcon` 은 **`aria-hidden`** 이다. 도메인 아이콘은 색과 함께 단독 신호가 될 수 없고
(디자인 시스템 §3), 의미는 항상 옆의 텍스트 라벨이 진다.

### 서버 상태 vs 클라이언트 상태 — 섞지 않는다

- **TanStack Query** = 서버에서 온 것 전부. 관찰·프로필·제안·일정·홈.
- **Zustand** = 서버가 모르는 것만. 토큰, 지금 보고 있는 아이, 열려 있는 모달.
- 🚨 **아이 이름·생일·알레르기를 Zustand 에 캐시하지 않는다.** 개인정보는 화면이 필요할 때 Query 로 가져오고, 스토어에는 `id` 만 둔다 (§2 개인정보).

쿼리 키는 손으로 쓰지 말고 `qk` 팩토리를 쓴다. 아이 스코프는 전부 `qk.child(cid)` 아래라서, 아이를 바꿀 때 한 번의 무효화로 끝난다.

### API 호출

- `fetch` 를 직접 부르지 않는다. **`src/lib/api` 의 `api.get/post/...` 만** 쓴다 — Bearer 토큰·에러 봉투·Idempotency 처리가 거기 한 곳에 있다.
- 토큰은 `useSessionStore.signIn()` 이 `setAuthToken()` 으로 클라이언트에 밀어 넣는다. 컴포넌트에서 헤더를 직접 만들지 않는다.

### 🚨 되돌릴 수 없는 5곳 — `api.post` 로 직접 부르지 않는다

`POST /children/{cid}/inputs` · `/onboarding` · `/photos` · `/health-safety`(게이트 ㉡) · `POST /events/{eid}/confirm`(게이트 ㉠).

**`lib/api/operations.ts` 의 전용 함수로만 부른다.** 키가 필수 인자라 빠뜨리면 `tsc` 가 잡는다.
경로도 `lib/api/idempotency.ts` 의 `idempotentPath` 표에서만 만든다 — 새 엔드포인트를 여기 더하면 차단·목·테스트가 함께 따라온다. **표를 거치지 않고 이 5개를 부를 방법은 없어야 한다.**

```tsx
const idem = useIdempotencyKey();                       // @/lib/api/use-idempotency-key
const mutation = useMutation({
  mutationFn: () => confirmEvent(eventId, idem.current()),
  onSuccess: () => { idem.rotate(); },                  // 🚨 성공한 뒤에만
});
```

- 키 없이 부르면 **요청이 나가지 않는다** (`IdempotencyKeyRequiredError`). 경고가 아니라 차단이고, 프로덕션에서도 같다.
- 🚨 **`mutationFn` 안에서 `newIdempotencyKey()` 를 부르지 않는다.** 재시도마다 새 키가 나가면 중복 방지가 통째로 무의미해진다. 타입은 이걸 못 잡는다 — 리뷰에서 지적된 지점이다.
- 🚨 **`rotate()` 를 실패 경로에서 부르지 않는다.** 실패 뒤 다시 누르는 게 재시도고, 재시도는 같은 키다.
  **끝을 확인하지 못한 경로에서도 부르지 않는다** — 서버가 이미 저장했을 수 있는 요청을 새 키로 보내면 그때 두 번 저장된다.
- 🚨 **사용자가 고쳐 쓸 수 있는 본문은 `current(text)` 로 본문을 넘긴다** (한 줄 입력 등). 서버가 처리했는데
  응답만 유실되면 화면은 실패로 보이고 보호자는 한 줄을 고쳐서 다시 보내는데, 키가 그대로면 "같은 키 · 다른 본문"
  이라 계속 `422` 다. 본문이 바뀌면 키도 바뀌어야 그 경로가 풀린다 (PR #71 리뷰).
- 서버가 무엇을 보장해야 하는지(재생 · 재사용 거부 · 동시 차단 · 2xx 만 저장)는 [`docs/api/idempotency-v1.md`](../../docs/api/idempotency-v1.md).
- `422 idempotency_key_reuse` 가 화면에 도달하면 **버그다.** 키 수명 관리가 깨진 것이니 화면을 그리지 말고 고친다.

### 에러

`ApiError` 로 잡는다. 특히:

- `consent_required` (403) → 저장이 아예 안 된 상태다. `error.consentDeeplink` 로 동의 화면에 보낸다.
- `llm_unavailable` (503) → 🚨 **기본값으로 대체하지 않는다.** 실패했다고 화면에 말한다.
- 4xx 는 재시도하지 않는다 (`query-client.ts` 에 이미 걸려 있다).
- **뮤테이션은 자동 재시도가 꺼져 있다.** 승인 게이트를 두 번 실행할 수 있어서다 — 켜지 말 것.
- 🚨 **승인 게이트에 낙관적 업데이트를 쓰지 않는다.** `onMutate` 로 캐시를 먼저 바꾸면 서버가 확정하기 전에 화면이 이미 확정된 것처럼 보인다 — "되돌릴 수 없는 것은 사람이 승인한다"(§2)가 **시각적으로** 깨진다. 응답을 받은 뒤 `invalidateQueries` 로 갱신한다. 자동 재시도와는 다른 경로라 따로 막아야 한다.
  낙관적 업데이트가 괜찮은 곳은 되돌릴 수 있는 것뿐이다 — 준비물 체크(`is_prepared`), 관심 칩 토글 정도.

### SSE

`EventSource` 를 쓰지 않는다. Authorization 헤더를 못 붙여서 NF-09 를 깬다.
`streamRunEvents(runId, signal)` 을 async iterator 로 돌린다.

```ts
const controller = new AbortController();
for await (const e of streamRunEvents(runId, controller.signal)) { ... }
```

### run 상태 — `useRunStream`

run 상태는 **서버 상태도 클라이언트 상태도 아니다.** 구독형이라 위 이분법에 안 맞는다.
`hooks/use-run-stream.ts` 가 `useReducer` 로 들고, 끝날 때 Query 를 무효화한다.

- `runReducer` 는 순수 함수다. 훅 없이 이벤트 배열만 흘려서 검증할 수 있다
- 끝나면 **`qk.child(cid)` 를 통째로** 무효화한다 — run 하나가 홈·관찰·프로필을 동시에 바꾼다
- 🚨 **`partial` 이 왔으면 `done` 이 와도 부분 결과다.** 성공 화면으로 덮지 않는다 (NF-06)
- 🚨 **20초 안전망이 훅 안에 있다.** 전체 시간이 아니라 *조용한 시간*을 잰다 —
  전환의 정본은 서버가 보내는 `partial` 이고, 이건 스트림이 멎었을 때의 그물이다
- 🚨 **서버가 말한 끝과 클라이언트가 스스로 끝낸 것을 섞지 않는다.** `done` · `partial` · `failed` 는
  서버가 말한 끝이고, `unconfirmed` 하나만 다르다 — 20초 침묵 · 종료 이벤트 없는 EOF · 연결 실패라
  **서버가 무엇을 저장했는지 모르는** 상태다. 섞으면 화면이 "저장됐어요" 라고 말하고 입력창의 원문까지
  지운다. 원문을 비워도 되는지는 `isRunConfirmed(status)` 하나로 판단한다 (PR #71 리뷰)
- 🚨 **종료 이벤트 없이 스트림이 닫히는 경우를 반드시 처리한다.** `for await` 는 EOF 에서 예외 없이
  끝난다 — 거기서 아무것도 안 하면 status 가 `streaming` 에 남고, idle 타이머까지 해제된 뒤라
  20초 전환조차 돌지 않아 진행 화면이 영원히 돈다. `pumpRunEvents()` 가 그 자리를 막는다
- 🚨 **입력 원문을 훅에 두지 않는다.** 화면을 벗어나면 스트림을 끊는데, 실패 시 원문을 입력창에
  되돌려야 한다. `failed` 이벤트가 `raw_text` 를 실어 주지만 네트워크가 끊기면 그것도 못 받는다 —
  **원문의 정본은 `stores/draft.ts` 다.**
- 🚨 **아직 안 보낸 한 줄은 화면 로컬 상태로 두지 않는다** (`stores/draft.ts`). 하단 네비가 보내기 버튼
  21px 아래에 다른 화면으로 가는 문을 세 개 열어서, `useState` 로 두면 잘못 누른 한 번에 쓰던 글이
  언마운트와 함께 죽는다. 승인을 하나 더 다는 건 답이 아니다 (최상위 §2 — 승인 게이트는 딱 2곳).
  🚨 **그 스토어에 `persist` 를 붙이지 않는다** — 아이에 대한 발화 원문이라 디스크에 남기지 않는다
  (최상위 §2 개인정보). 로그아웃에서 `clearAll()` 로 지운다

목의 `?scenario=partial` · `failed` · `disconnected` 로 세 경로를 바로 확인할 수 있다 (§7).

---

## 4. 화면을 그릴 때 지켜야 하는 것

최상위 §2 가 프론트에 떨어지는 지점만 추렸다. 어기면 서비스가 성립하지 않는다.

- 🚨 **일반 추천과 개인화 추천을 한 컴포넌트로 그리지 않는다.** 일반 추천에는 "또래 기준 일반 추천" 을 **화면에 명시**하고 쌓인 기록 건수를 그대로 보여준다. 되물을 때 질문은 **1개**.
  🚨 **둘이 한 화면에 같이 서지 않는다** — 일반 추천은 개인화 **대신** 나간다. 05 는 일반 추천이 위, 되묻는 질문이 아래다.
- 🚨 **"근거 없음" 상태를 만들지 않는다.** `evidence` 가 빈 suggestion 은 서버가 버리고 `scarcity` 로 내린다. 그 상태를 화면에 그리면 버그를 UI 로 덮는 것이다.
- 🚨 **`is_stale` 인 근거를 단독으로 보여주지 않는다** (6개월 · NF-08).
- 🚨 **`partial` 이벤트를 실패 화면으로 떨어뜨리지 않는다.** Agent 2개 중 1개만 성공해도 그 화면을 보여준다 — 성공과 실패를 **한 화면에** 섞는다 (NF-06).
- 🚨 **`failed` 는 `raw_text` 를 돌려준다.** 입력창에 그대로 남겨 놓는다 — 부모가 다시 타이핑하게 만들지 않는다.
- 🚨 **날짜·나이를 프론트에서 계산하지 않는다.** `age_display` · `observed_label` · `state_reason` 은 서버가 만든 문구다 (§3 — 100% 맞아야 하는 것은 코드가, 그것도 서버가 한다).
  - 그래서 01 화면은 프로토타입의 **나이 드롭다운 대신 생일**을 받는다. 나이 → 생일 환산이 곧 날짜 계산이다.
  - 같은 이유로 `GET /dev-screening/items` 에 `age_months` 를 만들어 보내지 않고 `child_id` 를 보낸다 (서버가 `birth_date` 로 환산한다 · 계약서 수정 대상).
  - 예외는 **표시가 아닌 입력 제약**뿐이다 — 달력에서 오늘 이후를 못 고르게 막는 것(`DateField` 의 `toDate`).
    🚨 `<input type="date">` 를 쓰지 않는다 — 브라우저·OS 마다 생김새가 달라 §7 입력 사양을 지킬 방법이 없다.
- 🚨 **health 관찰은 모양이 다르다.** `subject` · `polarity` · `affinity` 키 자체가 없다. `null` 검사가 아니라 `kind === "observation_health"` 로 분기한다 (`isHealthObservation()`).
- 🚨 **콘솔·로그에 발화 원문을 남기지 않는다.** `memory_id` 만 (§2 개인정보).
- 🚨 **외부 텍스트와 LLM 출력을 HTML 로 렌더링하지 않는다.** `dangerouslySetInnerHTML` 금지, 마크다운을 쓰더라도 raw HTML 비활성(`rehype-raw` 금지).
  이 서비스의 XSS 경로는 특정된다 — **기관 공지 붙여넣기·OCR 로 들어온 외부 텍스트가 LLM 을 거쳐 화면에 렌더링된다** (F-07 · F-14).
  토큰이 JS 에서 접근 가능하므로 XSS 한 건이 세션을 넘긴다. React 는 기본적으로 이스케이프하니 **구멍은 위 둘뿐이고 막는 비용이 거의 0이다.**
  ([`docs/api/auth-kakao-v1.md`](../../docs/api/auth-kakao-v1.md) §7-7 이 이 규칙을 최상위 CLAUDE.md §2 로 올릴 것을 제안했다 — 파트 경계를 넘어서 팀 결정 대기 중이고, 그때까지 프론트 규칙으로 여기 둔다.)

---

## 5. 스타일

- 색·radius·폰트는 `globals.css` 의 `@theme` 토큰으로만. 컴포넌트에서 `#hex` 를 직접 쓰지 않는다.
- 🚨 **토큰을 "비슷한 값" 으로 쓰지 않는다.** 이름이 곧 용도다 — `brand-ink` 는 **`brand-soft` 배경 위** 텍스트고,
  `canvas` 위 브랜드 텍스트는 `brand` 다. 대비만 맞으면 된다고 생각하면 "색 하나 = 뜻 하나"(문서 §1)가 무너진다.
- **`/design-system` 을 열어 보고 만든다.** 문서의 표를 실제 토큰·컴포넌트로 렌더하고 **대비비를 그 자리에서 계산**한다 — 토큰을 바꾸면 통과/미달이 바뀌므로 문서와 코드가 어긋나면 거기서 보인다. §7 에 사양은 있는데 아직 없는 컴포넌트 목록도 그 화면 맨 아래에 있다.
- 🚨 **`globals.css` 는 `@theme static` 이다.** 빼면 Tailwind 가 **쓰이는 토큰의 변수만** 내보내서, 아직 컴포넌트가 없는 토큰이 CSS 에서 사라진다 (실제로 9개가 빠져 있었다).
- **원본이 종류마다 한 곳이다** ([디자인 시스템](../../docs/web/design-system-v1.md) 머리말). 의도·사용 조건·금지 예는 **문서**,
  색·타이포·radius·높이처럼 여러 컴포넌트가 같이 쓰는 값은 **`globals.css` 의 `@theme`**, 한 컴포넌트 안의 치수와
  상태·포커스 동작은 **`components/ui/` 코드**다. 화면을 그리기 전에 문서를 읽고, 없는 값을 즉석에서 만들지 않는다.
- 🚨 **문서와 코드가 어긋나면 한쪽이 틀렸다고 가정하지 않는다.** 바뀐 이유를 먼저 찾는다 — 실기기 검증이나 접근성 때문에
  코드를 먼저 고친 것일 수 있다. 이유가 있으면 **같은 PR 에서** 문서 근거와 `DESIGN.md` 사본까지 맞추고, 못 찾으면 코드를 되돌린다.
- 🚨 **높이는 `min-h-*` 토큰으로만 쓴다** (`min-h-button` · `min-h-approve` · `min-h-field` · `min-h-touch` · `min-h-chip`).
  글자가 들어가는 요소에는 `h-12` 같은 숫자도, `h-button` 같은 고정 높이도 쓰지 않는다
  (아이콘만 있는 정사각 버튼은 `h-touch aspect-square` 로 고정해도 된다). 문구가 길거나 글자를 키우면 글자가 밖으로 흘러
  **승인 버튼이 무엇을 승인하는지 가려진다.** 컴포넌트를 추가·수정하면 `/design-system` 의 "긴 문구" 줄을
  **글자만 200%** 로 키워서 본다 (방법은 디자인 시스템 §10). 말줄임(`truncate`)으로 잘라 맞추지 않는다.
- 🚨 **실패를 빨강으로 칠하지 않는다.** `failed` · `partial` 의 실패 쪽 · `llm_unavailable` 은 `surface-muted` + `ink-muted` 다. `danger` 는 알레르기·건강 중단에만, `caution` 은 승인 게이트 2곳에만 쓴다.
- 🚨 **일반 추천과 개인화 추천을 색으로 구분하지 않는다.** 라벨과 기록 건수가 본체다 (§4 첫 줄과 같은 규칙).
- 본문 기본은 **16px / 1.6** 이다. 프로토타입의 11~13px 을 그대로 옮기지 말 것.
- 🚨 **화면에 보이는 글자에 이모지·기호를 쓰지 않는다** (디자인 시스템 §4). `①②③` · `↓` · `★` 로 뜻을 나타내지 않는다 —
  스크린리더가 제각각으로 읽고, 글리프가 없으면 두부(□)가 되며, 뜻을 나르는 것은 lucide 한 곳에 모아야 한다.
  순서는 `<ol>`, 목록 마커는 CSS `list-*`/`marker:`, 그림은 아이콘 + 텍스트 라벨.
  🚨 **줄표(`—`) 도 화면 텍스트에서 쓰지 않는다** — 괄호·쉼표·줄바꿈으로 바꾼다.
  **붙인 가운뎃점**(`수집·이용`)은 병렬 합성어라 그대로 두고, **띄운 가운뎃점**(` · `)은 메타 스트립에서 **줄당 하나까지**다.
  주석·문서·`/design-system`(내부 문서)은 대상이 아니다.
- **서체는 두 벌이고 쓰는 자리가 다르다** (디자인 시스템 §4). 둘 다 자체 호스팅 · 92개 동적 서브셋이다.
  - `font-sans` = **학교안심 날개 R.** 기본값이라 따로 붙일 일이 없다.
  - 🚨 `font-doc` = **Pretendard.** 이용약관 · 개인정보 처리방침 · 동의 전문처럼 **읽고 동의해야 하는 긴 법률 문서** 화면에 쓴다.
    폴백이 아니라 역할이다 — 손글씨는 단일 웨이트라 굵기가 브라우저 합성이고, 불리한 조항을 놓치지 않고 읽어야 하는 글에서는 손해만 된다.
    **문서 화면은 통째로 `font-doc` 이다.** 제목만 손글씨로 두는 식으로 한 화면에서 섞지 않는다.
  - 🚨 `src/app/hakgyoansim.css` · `pretendard.css` 를 **손으로 고치지 않는다.** 각각 `scripts/build-hakgyoansim-subset.py` 와
    `pretendard` 패키지에서 뽑은 파일이고, 올릴 때 다시 뽑는다 (prettier 대상에서도 뺐다).
  - 🚨 **폰트 CDN 을 쓰지 않는다.** 둘 다 자체 호스팅이다 (최상위 §9). 새 폰트를 넣을 때는
    **라이선스가 woff2 변환과 재배포를 허용하는지 먼저 확인한다** — 금지면 자체 호스팅 자체가 불가능해진다.
  - ⚠️ 본문 서체는 단일 웨이트라 **`label`(500)이 `body`(400)와 똑같이 나온다** (브라우저는 600 이상만 합성).
    칩·탭·폼 라벨을 굵기로 구분하려 하지 말 것.
  - 근거와 실측치는 [디자인 시스템 §4](../../docs/web/design-system-v1.md) 에 있다.
- 🚨 **`cursor` 는 `globals.css` 가 한 번에 건다.** Tailwind 4 preflight 는 버튼에 `cursor` 를 주지 않아서(v3 와 달라진 점) 전부 기본 화살표였다. 컴포넌트마다 붙이면 빠뜨린다 — 실제로 칩만 `not-allowed` 였다.
- **상호작용 상태는 primitive 안에 있다** (디자인 시스템 §8 표). 화면에서 `hover:`·`active:` 를 따로 붙이지 않는다.
  🚨 **`active:` 를 빠뜨리면 웹뷰에서 아무 반응이 없다** — 웹뷰에는 호버가 없어서 `hover:` 는 브라우저에서만 걸린다.
  🚨 `hover:` 는 `globals.css` 의 `@custom-variant` 로 **`@media (hover: hover)` 안에서만** 걸리게 덮어 뒀다.
  Tailwind 기본값은 그냥 `:hover` 라 터치 기기에서 탭한 뒤 **눌러붙는다** — 이 화면은 대부분 웹뷰라 그게 기본 경험이 된다.
- **기다리는 버튼에는 `Spinner` 를 붙인다.** 🚨 `prefers-reduced-motion` 에서는 스피너가 숨고 문구만 남는다 — 멈춘 스피너는 고장난 화면으로 읽힌다.
- **등장·스크롤 애니메이션을 만들지 않는다** (디자인 시스템 §8). 하루에 여러 번 지친 상태로 여는 화면이라, 처음엔 살아 있어 보여도 100번째엔 매번 기다려야 하는 것이 된다.
- 🚨 **도메인 4색을 한 화면에 다 쓰지 않는다** (최대 2개 · 디자인 시스템 §3). 예외는 **로그인 전 소개 화면 하나뿐**이다 — 거기엔 추천이 없어서 도메인 색이 "어느 Agent 결과인가" 신호로 쓰이지 않는다.
- **모바일 우선.** 이 화면은 대부분 [`apps/mobile`](../mobile) 웹뷰 안에서 보인다. 데스크톱 레이아웃을 먼저 잡지 않는다.
- 노치·홈 인디케이터는 `Screen` 이 상하 여백과 **함께 calc 로** 먹는다. 웹뷰 안에서는 네이티브 셸이 이미 처리해서 0 이 되고, 모바일 브라우저 직접 접속에서만 값이 생긴다.
  `pt-safe` / `pb-safe` 유틸은 **safe area 만** 필요한 곳(하단 고정 바 등)에 남겨 뒀다 — 여백과 같이 주려면 위 §3 의 경고를 볼 것.

---

## 6. 명령어

스크립트 목록은 `package.json` 에 있다. 거기서 안 보이는 것만 적는다.

`pnpm typecheck` 가 `next typegen` 을 먼저 도는 이유: `LayoutProps` · `PageProps` 같은 전역 타입은 Next 가 `.next/types` 에 생성한다. 빌드/타입젠 전에는 `tsc` 가 그 타입을 못 찾는다.

**최초 세팅** — `cp .env.example .env.local`. `NEXT_PUBLIC_API_BASE_URL` 이 없으면 앱이 뜨지 않고 바로 에러를 던진다 (조용히 잘못된 주소로 붙는 것보다 낫다).

🚨 `NEXT_PUBLIC_*` 는 **브라우저 번들에 그대로 박힌다.** 비밀은 여기 넣지 않는다 (§9 · NF-09).

---

## 7. 목(mock) 서버

백엔드가 아직 없어도 화면을 만들 수 있게 [MSW](https://mswjs.io) 가 계약서 v1 응답을 대신 내려준다.

```bash
# .env.local 에서 켠다
NEXT_PUBLIC_API_MOCKING=enabled
```

**개발 환경에서만 동작한다.** `start.ts` 가 `NODE_ENV` 로 먼저 막고 동적 import 를 쓰기 때문에, 프로덕션 빌드에는 msw 가 통째로 빠진다. 플래그가 `disabled` 면 요청은 그대로 실서버로 나간다.

### 🚨 이게 있는 진짜 이유 — 실서버로 못 만드는 상태들

색이나 문구가 아니라 **§4 의 규칙이 지켜지는지 확인하는 장치**다. 아래 상태들은 기억이 쌓이거나, 타이밍에 걸리거나, 모델이 죽어야 나온다.

| 시나리오 | 무엇이 나오나 |
| --- | --- |
| `default` | 기억이 쌓인 상태 · 개인화 추천 2건 |
| `empty` | 기록 0건 — `highlight: null` 빈 상태 · 05 는 일반 추천 **2건**(도메인 2색) |
| `scarcity` | 근거 부족 — 개인화 대신 **일반 추천 1건**(점선 카드) + 되묻는 질문 **1개** |
| `partial` | Agent 2개 중 1개 실패 — 성공·실패를 한 화면에 (NF-06) |
| `failed` | 입력 처리 실패 — `raw_text` 복원 |
| `consent` | 신규 가입 대기(`{ status, consent_code }`) · 403 `consent_required` — 저장 차단 · deeplink |
| `auth_unready` | `GET /auth/kakao/status` 가 `ready: false` — 로그인 버튼 비활성 |
| `stale` | 6개월 지난 근거만 — `is_stale` (NF-08) |

주소에 `?scenario=partial` 을 붙이면 저장되고 그다음부터 유지된다. 되돌리려면 `?scenario=default`.

### 화면 확인하는 법

시나리오별 주소 · 저장소 초기화 스니펫 · 화면별 진입 경로는 [`docs/web/mock-screens-v1.md`](../../docs/web/mock-screens-v1.md) 에 있다.

🚨 **저장소가 둘로 나뉘어 있다** — 로그인 세션·`bind` 는 `sessionStorage`(탭 닫으면 사라짐), 목 시나리오는 `localStorage`(안 사라짐). 이걸 모르면 "탭 닫았는데 왜 그대로지" 로 막힌다.

### 규칙

- **응답은 `lib/api/types.ts` 타입으로 강제한다.** 목이 계약서에서 벗어나면 타입 에러로 잡힌다 — 형태를 `any` 로 풀지 말 것.
- 🚨 **타입은 모양만 본다.** 동의 거부 · 중복 처리 · 상태 전이 · SSE 순서는 타입이 모른다. 그건 §8 의 테스트가 건다 — **둘 다 있어야 목이 계약에서 안 벗어난다.**
- **되돌릴 수 없는 5개는 `withIdempotency()` 로 감싼다** (`handlers/idempotency.ts`). 키 없으면 400 · 같은 키 재시도는 처음 응답 재생 · 다른 요청이면 422 · 처리 중이면 409.
  🚨 **핸들러 안의 409 는 "새 요청으로 이미 끝난 걸 또 하려는 경우" 에만 쓴다.** 재시도는 래퍼가 먼저 가로챈다 — 둘을 한 응답으로 합치면 화면이 구분할 수 없다.
- **핸들러에 없는 경로는 콘솔에 경고가 뜬다.** 조용히 통과시키지 않는다.
- **백엔드가 붙어도 목을 지우지 않는다.** 위 7개 상태는 실서버로 만들기 어렵고, 화면 회귀 확인에 계속 쓴다.
- 화면 00~07 · 09 가 덮여 있다. 08 사진 · 10 설정은 아직 없다.
- 🚨 **실제 OAuth 왕복은 목으로 흉내 낼 수 없다** — 카카오로 나가는 전체 페이지 이동이라 서비스 워커가 못 잡는다.
  목이 덮는 것은 시작 전(`status`)과 돌아온 뒤(교환·가입)이고, 중간은 `lib/auth/oauth.ts` 의 `MOCK_ONLY` 분기가 건너뛴다.
- 🚨 **`startMocks()` 는 한 번만 시작한다** (약속을 캐시한다). StrictMode 가 effect 를 두 번 돌리는데 두 번째 `worker.start()` 가
  거부되면서 `.finally()` 가 즉시 실행돼 **워커가 뜨기 전에 화면이 그려졌고**, 첫 화면의 첫 요청이 목을 통과해 실서버로 나갔다.
  00 화면의 status prefetch 가 여기 걸려서 로그인 버튼이 영구 비활성이 됐다 — 첫 화면에서 쿼리를 부르기 전까지는 안 보이던 버그다.
- 🚨 **fixtures 에 실제 사용자 발화나 아이 정보를 넣지 않는다.** 저장소가 public 이다 (최상위 §9).

msw 버전을 올리면 워커를 다시 만들어야 한다 — `pnpm exec msw init public`.

---

## 8. 테스트

```bash
pnpm test          # vitest run
pnpm test:watch
```

**지금 있는 건 계약 회귀 테스트 하나다.** 화면 테스트(RTL·jsdom)는 컴포넌트가 생길 때 붙인다.

### 무엇을 거는가

목이 **계약대로 행동하는지**를 건다. 타입 검사가 못 보는 것들이다 — 키 누락 · 재시도 · 동시 요청 · 권한 부족 · 상태 전이 · SSE 이벤트 순서.

- `src/mocks/contract.test.ts` — 목의 동작
- `src/lib/api/idempotency.test.ts` — 클라이언트의 차단

### 규칙

- 🚨 **테스트 전용 핸들러를 만들지 않는다.** `src/mocks/server.ts` 는 브라우저 워커와 **같은 핸들러**를 쓴다. 따로 두면 확인한 적 없는 목으로 화면을 만들게 된다.
- 🚨 **목은 프로세스 수명만큼 사는 상태를 들고 있다** (확정된 event · 저장된 응답 · 등록된 안전 항목). 새 상태를 추가하면 리셋 함수를 export 하고 `src/test/setup.ts` 의 `afterEach` 에 건다 — 안 그러면 결과가 테스트 순서에 따라 바뀐다.
- `onUnhandledRequest: "error"` 다. 계약서에 없는 경로를 부르면 테스트가 실패한다.
- 🚨 **픽스처·테스트 문자열에 실제 사용자 발화나 아이 정보를 넣지 않는다.** 저장소가 public 이다 (최상위 §9).
- 백엔드가 붙으면 **같은 표를 실서버에도** 건다. 목이 통과한다고 서버가 통과하는 게 아니다 ([`docs/api/idempotency-v1.md`](../../docs/api/idempotency-v1.md) §6-3).

---

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
