# apps/web — 프론트 구현 컨텍스트

Owner: 고태영 (프론트 리드)

> 이 파일은 **프론트에서만 지키는 규칙**이다. 파트 경계를 넘는 규칙은 [최상위 CLAUDE.md](../../CLAUDE.md) §2 에 있다.
> 작업 전에 읽을 것: 최상위 `CLAUDE.md` (§2·§3·§5) → 이 파일 → [`docs/api/api-interface-v1.html`](../../docs/api/api-interface-v1.html).

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
├── app/                  App Router. 라우트 = 화면 01~10
│   ├── layout.tsx        루트 레이아웃 (lang="ko" · viewport)
│   ├── providers.tsx     QueryClientProvider + 세션 persist 복구
│   └── globals.css       Tailwind 진입점 + @theme 디자인 토큰
├── lib/
│   ├── env.ts            NEXT_PUBLIC_* 검증 · API_BASE_URL
│   ├── query-client.ts   QueryClient 기본값 (retry 정책)
│   └── api/
│       ├── types.ts      계약서 §02 공통 타입 5종 + Ref + enum
│       ├── errors.ts     에러 봉투 · ApiError · 에러 코드
│       ├── client.ts     fetch 래퍼 (Bearer · Idempotency-Key)
│       ├── sse.ts        GET /runs/{rid}/events 스트림 파서
│       └── queryKeys.ts  쿼리 키 팩토리
└── stores/
    └── session.ts        토큰 · activeChildId (zustand persist)
```

화면을 붙일 때는 [`docs/api/api-interface-v1.html`](../../docs/api/api-interface-v1.html) 의 **화면 → 호출** 표를 기준으로 잡는다.

---

## 3. 라이브러리 관례

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

### SSE

`EventSource` 를 쓰지 않는다. Authorization 헤더를 못 붙여서 NF-09 를 깬다.
`streamRunEvents(runId, signal)` 을 async iterator 로 돌린다.

```ts
const controller = new AbortController();
for await (const e of streamRunEvents(runId, controller.signal)) { ... }
```

---

## 4. 화면을 그릴 때 지켜야 하는 것

최상위 §2 가 프론트에 떨어지는 지점만 추렸다. 어기면 서비스가 성립하지 않는다.

- 🚨 **일반 추천과 개인화 추천을 한 컴포넌트로 그리지 않는다.** 일반 추천에는 "또래 기준 일반 추천" 을 **화면에 명시**하고 쌓인 기록 건수를 그대로 보여준다. 되물을 때 질문은 **1개**.
- 🚨 **"근거 없음" 상태를 만들지 않는다.** `evidence` 가 빈 suggestion 은 서버가 버리고 `scarcity` 로 내린다. 그 상태를 화면에 그리면 버그를 UI 로 덮는 것이다.
- 🚨 **`is_stale` 인 근거를 단독으로 보여주지 않는다** (6개월 · NF-08).
- 🚨 **`partial` 이벤트를 실패 화면으로 떨어뜨리지 않는다.** Agent 2개 중 1개만 성공해도 그 화면을 보여준다 — 성공과 실패를 **한 화면에** 섞는다 (NF-06).
- 🚨 **`failed` 는 `raw_text` 를 돌려준다.** 입력창에 그대로 남겨 놓는다 — 부모가 다시 타이핑하게 만들지 않는다.
- 🚨 **날짜·나이를 프론트에서 계산하지 않는다.** `age_display` · `observed_label` · `state_reason` 은 서버가 만든 문구다 (§3 — 100% 맞아야 하는 것은 코드가, 그것도 서버가 한다).
- 🚨 **health 관찰은 모양이 다르다.** `subject` · `polarity` · `affinity` 키 자체가 없다. `null` 검사가 아니라 `kind === "observation_health"` 로 분기한다 (`isHealthObservation()`).
- 🚨 **콘솔·로그에 발화 원문을 남기지 않는다.** `memory_id` 만 (§2 개인정보).

---

## 5. 스타일

- 색·radius·폰트는 `globals.css` 의 `@theme` 토큰으로만. 컴포넌트에서 `#hex` 를 직접 쓰지 않는다.
- 토큰 값은 **아직 임시다.** `docs/web/` 에 디자인 토큰 문서가 확정되면 그쪽이 정본이 된다.
- **모바일 우선.** 이 화면은 대부분 [`apps/mobile`](../mobile) 웹뷰 안에서 보인다. 데스크톱 레이아웃을 먼저 잡지 않는다.
- 노치·홈 인디케이터는 `pt-safe` / `pb-safe` 유틸로. 단 웹뷰 안에서는 네이티브 셸이 이미 safe area 를 먹고 있어서 0 이 된다 — 모바일 브라우저 직접 접속용 안전장치다.

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

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
