# apps/web — 프론트 구현 컨텍스트

Owner: 고태영 (프론트 리드)

> 이 파일은 **프론트에서만 지키는 규칙**이다. 파트 경계를 넘는 규칙은 [최상위 CLAUDE.md](../../CLAUDE.md) §2 에 있다.
> 작업 전에 읽을 것: 최상위 `CLAUDE.md` (§2·§3·§5) → 이 파일 → [`docs/api/api-interface-v1.html`](../../docs/api/api-interface-v1.html) (무엇을 부르는가) → [`docs/web/design-system-v1.md`](../../docs/web/design-system-v1.md) (무엇으로 그리는가).
> 로그인은 계약서 §04 가 아니라 [`docs/api/auth-kakao-v1.md`](../../docs/api/auth-kakao-v1.md) (서버 정본) · [`docs/web/kakao-login-v1.md`](../../docs/web/kakao-login-v1.md) (프론트) 를 본다.

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

```
src/
├── app/                  App Router. 라우트 = 화면 00~10
│   ├── layout.tsx        루트 레이아웃 (lang="ko" · viewport)
│   ├── providers.tsx     QueryClientProvider + 세션 persist 복구
│   ├── globals.css       Tailwind 진입점 + @theme 디자인 토큰
│   ├── pretendard.css    🚨 Pretendard @font-face — 패키지에서 뽑은 파일. 손으로 고치지 않는다
│   ├── page.tsx          00 소개 · 로그인 (로그인 전에 보는 유일한 화면)
│   ├── auth/callback/    로그인 복귀 지점 — 웹·앱 공통. 🚨 AuthGate 로 감싸지 않는다
│   ├── onboarding/       01 첫 진입 — 아이 만들기. 아직 childId 가 없어서 아이 스코프 밖이다
│   └── child/[childId]/  아이 스코프 화면 전부 (02 온보딩 · 03~09)
├── lib/
│   ├── env.ts            NEXT_PUBLIC_* 검증 · API_BASE_URL
│   ├── query-client.ts   QueryClient 기본값 (retry 정책)
│   ├── cn.ts             조건부 클래스 합치기 (tailwind-merge 아님 — 뒤가 앞을 안 덮는다)
│   ├── auth/
│   │   ├── oauth.ts      로그인 시작 · provider·복귀경로 기억 · isAppShell
│   │   └── oauth-bind.ts bind 비밀 — 1회용 코드가 오가는 홉을 지킨다
│   └── api/
│       ├── types.ts      계약서 §02 공통 타입 5종 + Ref + enum + 엔드포인트 요청·응답
│       ├── errors.ts     에러 봉투 · ApiError · 에러 코드
│       ├── client.ts     fetch 래퍼 (Bearer · Idempotency-Key)
│       ├── sse.ts        GET /runs/{rid}/events 스트림 파서
│       └── queryKeys.ts  쿼리 키 팩토리
├── components/
│   ├── ui/               토큰만 아는 primitive (screen · button · text-input · chip · card)
│   ├── auth-gate.tsx     토큰 없으면 / 로 되돌린다
│   └── *.tsx             도메인을 아는 조합
├── hooks/                화면 여러 곳이 쓰는 훅
├── mocks/                MSW 목 서버 — 개발 환경 전용 (§7)
│   ├── scenario.ts       시나리오 스위치 (?scenario=)
│   ├── fixtures.ts       계약서 기준 시드 데이터
│   ├── handlers/         엔드포인트별 핸들러
│   └── start.ts          dev + 플래그일 때만 워커를 띄운다
└── stores/
    └── session.ts        토큰 · activeChildId (zustand persist)
```

`public/mockServiceWorker.js` 는 msw 가 생성한 파일이다. 손으로 고치지 않고 lint·prettier 대상에서 빼 뒀다.

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
| 03~09 | `/child/[childId]/…` |
| 디자인 시스템 (내부 문서) | `/design-system` |

`/onboarding` 만 아이 스코프 **밖**이다 — `POST /children` 이 성공해야 `childId` 가 생기고, 그때 `/child/{cid}/onboarding` 으로 넘어간다. 이 경계를 흐리면 childId 가 없는 상태의 아이 스코프 라우트가 생긴다.

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
- 지금 있는 것 — `Screen`(최대 폭·좌우 여백·**상하 여백+safe area**) · `PageTitle` · `Button`(§7 6변형) ·
  `TextInput` · `DateField` · `Checkbox` · `Chip`/`ChipRow` · `Card`/`CardFailed` · `Spinner` · `BottomSheet`.
  배너 · 제안 카드 · 탭은 그 화면 이슈에서 만든다
- 🚨 **외부 라이브러리는 `<dialog>`(시트) 와 `react-day-picker`(달력) 둘뿐이다.** 접근성을 손으로 짜면
  반드시 빠뜨리는 것만 예외로 얹는다. 달력은 **기본 CSS 를 불러오지 않고** `classNames` 로 토큰만 입힌다 —
  버튼·입력을 주는 UI 킷은 계속 쓰지 않는다 (디자인 시스템 §7)
- 🚨 **`<Screen>` 에 `py-*` 를 넘기지 않는다.** 상하 여백은 `Screen` 이 소유한다.
  safe-area 유틸은 **여백을 함께 받는다** (`pt-safe-8` = safe area + 32px) — 예전처럼 safe area 만 넣는
  유틸에 `py-8` 을 겹치면 같은 padding 속성이라 **한쪽이 조용히 죽는다.** 이 사고를 두 번 냈다:
  화면 5개의 상하 여백이 0 이었고, 바텀시트 닫기 버튼이 화면 맨 아래에 붙었다
- 🚨 **화면 제목은 `PageTitle` 로 쓴다.** §9 가 좁은 폰(< 380px)에서 `display` → `title` 로 낮추라고
  정했는데, 화면마다 손으로 쓰면 어긋난다
- ⚠️ `cn()` 은 **tailwind-merge 가 아니다.** 뒤에 온 클래스가 앞을 덮어주지 않으니, primitive 밖에서
  `className` 으로 색·크기를 덮어쓰지 않는다. 필요하면 변형을 primitive 안에 추가한다
- ⚠️ 라이브러리를 안 쓰는 대신 **접근성이 전부 우리 책임**이다. 포커스 트랩·ESC·스크롤 락을 직접 짜야 한다
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
- **Idempotency-Key 가 필수인 5곳** — `POST /children/{cid}/inputs` · `/onboarding` · `/photos` · `/health-safety` · `POST /events/{eid}/confirm`.
  `newIdempotencyKey()` 를 넘긴다. 빠뜨리면 dev 콘솔에 경고가 뜨고 서버는 400 을 준다.
  🚨 **재시도할 때 키를 새로 만들지 않는다** — 같은 키를 다시 보내는 게 중복 실행을 막는 유일한 방법이다.

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
- 🚨 **입력 원문을 훅에 두지 않는다.** 화면을 벗어나면 스트림을 끊는데, 실패 시 원문을 입력창에
  되돌려야 한다. `failed` 이벤트가 `raw_text` 를 실어 주지만 네트워크가 끊기면 그것도 못 받는다 —
  **원문의 정본은 입력 화면이 들고 있는 값이다.**

목의 `?scenario=partial` · `failed` 로 두 경로를 바로 확인할 수 있다 (§7).

---

## 4. 화면을 그릴 때 지켜야 하는 것

최상위 §2 가 프론트에 떨어지는 지점만 추렸다. 어기면 서비스가 성립하지 않는다.

- 🚨 **일반 추천과 개인화 추천을 한 컴포넌트로 그리지 않는다.** 일반 추천에는 "또래 기준 일반 추천" 을 **화면에 명시**하고 쌓인 기록 건수를 그대로 보여준다. 되물을 때 질문은 **1개**.
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
- **정본은 [`docs/web/design-system-v1.md`](../../docs/web/design-system-v1.md) 다.** 색·타이포·간격뿐 아니라 버튼 높이 · 카드 여백 · 시트 동작까지 거기 있다(§7 컴포넌트). 화면을 그리기 전에 읽고, 없는 값을 즉석에서 만들지 않는다 — 필요하면 문서를 먼저 고친다.
- `globals.css` 는 그 문서를 옮긴 것이다. 둘이 어긋나면 **CSS 가 틀린 것**이다.
- 🚨 **실패를 빨강으로 칠하지 않는다.** `failed` · `partial` 의 실패 쪽 · `llm_unavailable` 은 `surface-muted` + `ink-muted` 다. `danger` 는 알레르기·건강 중단에만, `caution` 은 승인 게이트 2곳에만 쓴다.
- 🚨 **일반 추천과 개인화 추천을 색으로 구분하지 않는다.** 라벨과 기록 건수가 본체다 (§4 첫 줄과 같은 규칙).
- 본문 기본은 **16px / 1.6** 이다. 프로토타입의 11~13px 을 그대로 옮기지 말 것.
- 🚨 **화면에 보이는 글자에 이모지·기호를 쓰지 않는다** (디자인 시스템 §4). `①②③` · `↓` · `★` 로 뜻을 나타내지 않는다 —
  스크린리더가 제각각으로 읽고, 글리프가 없으면 두부(□)가 되며, 뜻을 나르는 것은 lucide 한 곳에 모아야 한다.
  순서는 `<ol>`, 목록 마커는 CSS `list-*`/`marker:`, 그림은 아이콘 + 텍스트 라벨.
  가운뎃점(`·`)·줄표(`—`)는 문장부호라 예외고, 주석·문서의 🚨 는 화면에 안 나가므로 대상이 아니다.
- **Pretendard 는 자체 호스팅한다** (`public/fonts/pretendard/` · 92개 동적 서브셋).
  🚨 `src/app/pretendard.css` 는 `pretendard` 패키지에서 뽑아 url 만 바꾼 파일이다 — **손으로 고치지 않고**, 올릴 때 다시 뽑는다 (prettier 대상에서도 뺐다).
  CDN 을 쓰지 않는 이유와 서브셋을 고른 근거는 [디자인 시스템 §4](../../docs/web/design-system-v1.md) 에 있다.
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

```bash
pnpm dev           # 개발 서버 (Turbopack)
pnpm build         # 프로덕션 빌드 (타입 에러 나면 실패한다)
pnpm typecheck     # next typegen && tsc --noEmit
pnpm lint          # eslint
pnpm format        # prettier --write
```

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
| `empty` | 기록 0건 — `highlight: null` 빈 상태 |
| `scarcity` | 근거 부족 — 개인화 대신 일반 추천 + 되묻는 질문 **1개** |
| `partial` | Agent 2개 중 1개 실패 — 성공·실패를 한 화면에 (NF-06) |
| `failed` | 입력 처리 실패 — `raw_text` 복원 |
| `consent` | 신규 가입 대기(`{ status, consent_code }`) · 403 `consent_required` — 저장 차단 · deeplink |
| `auth_unready` | `GET /auth/kakao/status` 가 `ready: false` — 로그인 버튼 비활성 |
| `stale` | 6개월 지난 근거만 — `is_stale` (NF-08) |

주소에 `?scenario=partial` 을 붙이면 저장되고 그다음부터 유지된다. 되돌리려면 `?scenario=default`.

### 화면 확인하는 법

**저장소가 둘로 나뉘어 있다.** 이걸 모르면 "탭 닫았는데 왜 그대로지" 로 막힌다.

| | 어디 | 언제 사라지나 |
| --- | --- | --- |
| 로그인 세션 · `bind` · 가입 대기표 | `sessionStorage` | **탭 닫으면** 자동 |
| 목 시나리오 | `localStorage` | **안 사라진다** — 직접 지우거나 `?scenario=default` |

전부 초기화 (DevTools 콘솔):

```js
sessionStorage.clear();
localStorage.removeItem("yukameo.session");
localStorage.removeItem("yukameo.mock.scenario");
location.replace("/");
```

| 보고 싶은 화면 | 어떻게 |
| --- | --- |
| 00 로그인 | `/` |
| 00 로그인 · 버튼 비활성 | `/?scenario=auth_unready` |
| **가입 동의** | 초기화 후 `/?scenario=consent` → 카카오로 시작하기 |
| 01 아이 만들기 | 로그인 후 `/onboarding` (목의 `/me` 는 항상 아이가 1명이라 로그인만으로는 안 닿는다) |
| 02 이야기 하나 | `/child/c1/onboarding` |
| 로그인 실패 문구 | `/auth/callback?error=invalid_state` |
| 디자인 시스템 | `/design-system` |

화면만 빨리 보려면 값을 직접 심어도 된다. 🚨 **`bind` 를 빼면 화면은 떠도 제출이 400 이다** — 서버가 형식을 검증하는 게 정상 동작이다.

```js
sessionStorage.setItem("yukameo.oauth.consent_code", "cc_mock");
sessionStorage.setItem("yukameo.oauth.provider", "kakao");
sessionStorage.setItem("yukameo.oauth.bind", "dev".padEnd(43, "x"));
location.replace("/auth/consent");
```

### 규칙

- **응답은 `lib/api/types.ts` 타입으로 강제한다.** 목이 계약서에서 벗어나면 타입 에러로 잡힌다 — 형태를 `any` 로 풀지 말 것.
- **핸들러에 없는 경로는 콘솔에 경고가 뜬다.** 조용히 통과시키지 않는다.
- **백엔드가 붙어도 목을 지우지 않는다.** 위 7개 상태는 실서버로 만들기 어렵고, 화면 회귀 확인에 계속 쓴다.
- 화면 00~06 만 덮여 있다. 07~10 은 아직 없다.
- 🚨 **실제 OAuth 왕복은 목으로 흉내 낼 수 없다** — 카카오로 나가는 전체 페이지 이동이라 서비스 워커가 못 잡는다.
  목이 덮는 것은 시작 전(`status`)과 돌아온 뒤(교환·가입)이고, 중간은 `lib/auth/oauth.ts` 의 `MOCK_ONLY` 분기가 건너뛴다.
- 🚨 **`startMocks()` 는 한 번만 시작한다** (약속을 캐시한다). StrictMode 가 effect 를 두 번 돌리는데 두 번째 `worker.start()` 가
  거부되면서 `.finally()` 가 즉시 실행돼 **워커가 뜨기 전에 화면이 그려졌고**, 첫 화면의 첫 요청이 목을 통과해 실서버로 나갔다.
  00 화면의 status prefetch 가 여기 걸려서 로그인 버튼이 영구 비활성이 됐다 — 첫 화면에서 쿼리를 부르기 전까지는 안 보이던 버그다.
- 🚨 **fixtures 에 실제 사용자 발화나 아이 정보를 넣지 않는다.** 저장소가 public 이다 (최상위 §9).

msw 버전을 올리면 워커를 다시 만들어야 한다 — `pnpm exec msw init public`.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
