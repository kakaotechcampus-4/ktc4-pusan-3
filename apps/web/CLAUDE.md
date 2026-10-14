# apps/web — 프론트 구현 컨텍스트

Owner: 고태영 (프론트 리드)

> 이 파일은 **프론트에서만 지키는 규칙**이다. 파트 경계를 넘는 규칙은 [최상위 CLAUDE.md](../../CLAUDE.md) §2 에 있다.
> 작업 전에 읽을 것: 최상위 `CLAUDE.md` (§2·§3·§5) → 이 파일 → 아래 §0 표에서 **건드리는 코드에 맞는 문서** → API 서버의 **Swagger `/docs`** (무엇을 부르는가 — 계약 정본, 09-20 회의) → [`docs/web/design-system-v1.md`](../../docs/web/design-system-v1.md) (무엇으로 그리는가).
> 로그인은 계약서 §04 가 아니라 [`docs/api/auth-kakao-v1.md`](../../docs/api/auth-kakao-v1.md) (서버 정본) · [`docs/web/kakao-login-v1.md`](../../docs/web/kakao-login-v1.md) (프론트) 를 본다.
> 되돌릴 수 없는 5개를 건드린다면 [`docs/api/idempotency-v1.md`](../../docs/api/idempotency-v1.md) 도 읽는다 (승인 게이트 2곳이 거기 있다).

---

## 0. 이 파일과 `docs/web/` 의 나눔

이 파일에는 **모든 프론트 작업에 걸리는 규칙만** 둔다. 화면 하나나 영역 하나에만 걸리는 규칙은 `docs/web/` 로 옮겼고,
여기에는 그 문서로 가는 길과, 어기면 최상위 §2 가 깨지는 줄만 남겼다.

🚨 **아래 표의 코드를 만지면 그 문서를 먼저 읽는다.** 이 파일의 요약만 보고 고치지 않는다 — 옮긴 문서의 🚨 는 대부분 실제로 터진 버그에서 나왔다.

| 이걸 만지면 | 먼저 읽을 문서 |
| --- | --- |
| 05 · 07 · 08 · 09 · 11-1 · 11-2 화면, 라우트를 더하거나 시트를 라우트로 바꿀 때 | [`screen-rules-v1.md`](../../docs/web/screen-rules-v1.md) |
| 04 대화 · `stores/conversation.ts` · `hooks/use-run-stream.ts` · `lib/api/sse.ts` · `components/run-result.tsx` | [`chat-run-v1.md`](../../docs/web/chat-run-v1.md) |
| `components/` · `components/ui/` · `globals.css` · 아이콘 · 서체 · 10 설정 하위 화면(고객센터 · 탈퇴) | [`components-v1.md`](../../docs/web/components-v1.md) |
| `lib/auth/` · `stores/session.ts` · `/auth/*` · 01 · 02 · 동의(`lib/consent.ts`) | [`auth-consent-v1.md`](../../docs/web/auth-consent-v1.md) |
| `lib/api/` · 뮤테이션 · 에러 코드 처리 · `error.tsx` · `AppErrorBoundary` | [`api-client-v1.md`](../../docs/web/api-client-v1.md) |
| `mocks/` · `*.test.ts` · `e2e/` | [`mock-and-tests-v1.md`](../../docs/web/mock-and-tests-v1.md) |
| `package.json` 의 버전 · `eslint.config.mjs` · `ci-web.yml` · `Dockerfile` · 배포 | [`toolchain-deploy-v1.md`](../../docs/web/toolchain-deploy-v1.md) |

🚨 **같은 규칙이 이 파일의 요약과 `docs/web/` 원문 두 곳에 있으면 원문이 정본이다.** 규칙을 바꾸면 같은 PR 에서 둘 다 고친다.

규칙을 새로 적을 때도 같은 기준이다. 모든 작업에 걸리면 여기, 아니면 해당 문서에 적는다 — 이 파일은 프론트 작업마다
통째로 컨텍스트에 실리므로, 다시 커지면 그만큼 매번 먹는다.

- 표에 맞는 문서가 없으면 이 파일에 쌓지 않는다. 가장 가까운 문서에 절을 더하거나 `docs/web/<영역>-v1.md` 를 새로 만들고,
  이 표와 [`docs/README.md`](../../docs/README.md) 인덱스에 한 줄씩 더한다
- 새 코드 주석은 이 파일이 아니라 **원문 문서의 절**을 가리킨다 (`docs/web/chat-run-v1.md §3`).
  이미 있는 `apps/web/CLAUDE.md §N` 주석은 그 코드를 만지는 PR 에서 옮긴다

---

## 1. 스택 · 버전

버전은 `package.json` 이 정본이다 (`^` 없이 고정). 그 값으로 둔 이유는 [`toolchain-deploy-v1.md`](../../docs/web/toolchain-deploy-v1.md) §1.

- Next.js 16 (App Router · Turbopack) · React 19 · Tailwind CSS 4 (CSS-first — `tailwind.config.*` 없음, 토큰은 `globals.css` 의 `@theme`) ·
  Zustand(클라이언트 상태) · TanStack Query(서버 상태) · Zod(환경변수 검증)
- 🚨 **TypeScript 를 7 로 올리지 않는다.** `typescript-eslint` 가 아직 지원하지 않아 lint 가 죽는다
- 🚨 **React 를 올리면 `eslint.config.mjs` 의 `settings.react.version` 도 같이 올린다**
- `next lint` 는 16 에서 빠졌다 — lint 는 별도 스크립트

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
- `mocks/` = MSW 목 서버, **개발 환경 전용** (§7).
  🚨 `mocks/native-bridge.ts` 는 **MSW 가 아니다** — 최근 사진은 API 가 아니라 셸이 꽂는 값이라
  서비스 워커가 가로챌 대상이 없다. 스위치만 목과 같은 것을 쓴다
- `lib/native/` = **네이티브 셸이 꽂아 주는 것의 웹 쪽 약속.** 셸 구현은 `apps/mobile` 이다
- `stores/` = Zustand, **클라이언트 상태만.** 서버 상태는 TanStack Query (§3).
  🚨 `stores/draft.ts` 는 **메모리 전용**이다 — 아직 안 보낸 발화 원문이라 `persist` 금지.
  🚨 `stores/photo-draft.ts`(**아이 사진 원본**) · `stores/safety-scan-draft.ts`(**알레르기 검사지
  사진** — 의료 기록이다) · `stores/conversation.ts`(**그날 대화** — 보낸 원문 · run 결과 · 되물은
  질문이 그대로 들어 있다) 도 같다. 넷 다 로그아웃에서 `clearAll()` 로 놓는다 (`stores/session.ts`).
  대화 스토어는 로그아웃에서 **도는 스트림도 끊는다**
- `public/mockServiceWorker.js` 는 msw 가 생성한 파일이다. 손으로 고치지 않고 lint·prettier 대상에서 빼 뒀다

화면을 붙일 때 요청 · 응답 모양은 **Swagger(`/docs`)** 를 기준으로 잡는다. [`docs/api/api-interface-v1.html`](../../docs/api/api-interface-v1.html) 은 갱신하지 않는 초안이라, 그 **화면 → 호출** 표는 어느 화면이 무엇을 부르는지 훑는 용도로만 본다.

---

## 3. 구현 관례

### 라우팅 — 아이 스코프는 URL 에 둔다

| 화면 | 라우트 |
| --- | --- |
| 00 소개 · 로그인 | `/` |
| 로그인 복귀 지점 (보여줄 내용 없음) | `/auth/callback` |
| 가입 — 보호자 이름 + 계정 동의 2건 (신규 회원만) | `/auth/consent` |
| 00-1 경로 고르기 (아이 0명) | `/start` |
| 초대 — 코드 입력 → 아이 확인 → 수락 (2단계 · 라우트 하나) | `/invite` |
| 01 첫 진입 (아이 만들기 + 아이 동의 2건) | `/onboarding` |
| 02 아이 정보 (관계 · 성별 · 키 · 몸무게 · 알레르기 · 전부 선택) | `/child/[childId]/onboarding` |
| └ 알레르기 구역은 11 과 **같은 컴포넌트**다 | `components/safety-section.tsx` |
| 03 홈 | `/child/[childId]/home` |
| 04 대화 (한 줄 입력의 결과 · 되묻기 답 · 하루 단위) | `/child/[childId]/chat` |
| 05 제안 후보 (고르기 → **채택** → 일정 만들기) | `/child/[childId]/suggestions?agents=food,activity&run=…` (03 홈 버튼에서 오면 `run` 대신 `prompt=…`) |
| 06 승인 | 05 위의 바텀시트 (라우트 없음) |
| 07 기억 | `/child/[childId]/memories?tab=observations\|profile\|feedback` |
| 08 사진으로 적기 | `/child/[childId]/photos?date=YYYY-MM-DD` (날짜는 09 에서 들어왔을 때만) |
| 09 캘린더 | `/child/[childId]/calendar?date=YYYY-MM-DD` |
| 10 설정 | `/child/[childId]/settings` |
| 10 설정 › 고객센터 (읽는 화면) | `/child/[childId]/settings/help` |
| 10 설정 › 탈퇴 (흐름 중인 화면) | `/child/[childId]/settings/withdraw` |
| 11 아이 프로필 | `/child/[childId]/profile` |
| 11-1 키 · 몸무게 상세 | `/child/[childId]/profile/growth` |
| 11-2 검사지에서 가져오기 | `/child/[childId]/profile/safety-scan` |
| 디자인 시스템 (내부 문서) | `/design-system` |

라우트를 고른 이유와 화면별 규칙은 [`screen-rules-v1.md`](../../docs/web/screen-rules-v1.md) §1 이다. 어기면 바로 사고가 나는 것만:

- 🚨 **화면이 읽는 `childId` 의 정본은 URL 이다.** 클라이언트 컴포넌트는 `useChildId()`, 서버 컴포넌트는 `params`.
  `stores/session.ts` 의 `activeChildId` 를 읽어 화면을 그리지 않는다 — 쿠키·스토어로 옮기면 셋이 어긋날 때 **다른 아이의 데이터가 보인다**
- `/onboarding` · `/start` · `/invite` 는 아이 스코프 **밖**이다. 🚨 **아이가 0명이면 `/onboarding` 이 아니라 `/start` 로 보낸다** (#96)
- 🚨 **`useSearchParams()` 를 쓰는 화면은 `<Suspense>` 경계 안에 둔다.** 없으면 `pnpm build` 만 그 화면에서 멈춘다 (`pnpm dev` 에서는 안 보인다).
  `fallback` 에는 hydrate 직후와 **같은 것**을 둔다
- 🚨 **03 홈 버튼은 05 에 문장이 아니라 키를 넘긴다** (`?prompt=dinner` · #286). 문장을 화면이 보내면 Supervisor 안전 사전검사를 건너뛴다
- 🚨 **서버가 준 `deeplink` 를 주소로 쓰지 않는다.** 어느 구역인지 힌트로만 쓴다
- 🚨 **기기 뒤로가기는 웹뷰 히스토리다** ([`apps/mobile/App.tsx`](../mobile/App.tsx)). 그래서 04 · 11-1 · 11-2 는 시트가 아니라 라우트고,
  돌아가는 길은 `router.back()` 이 아니라 링크다

### 컴포넌트 — 직접 만든다

별도 UI 라이브러리를 쓰지 않는다. 사양은 [디자인 시스템 §7](../../docs/web/design-system-v1.md) 에 있다 — 없는 값을 즉석에서 만들지 않는다.
지금 있는 컴포넌트 목록과 공통 UI 규칙 원문은 [`components-v1.md`](../../docs/web/components-v1.md) §1 이다. **새로 만들기 전에 그 목록에서 찾고, 만들면 목록에 더한다.**

- `components/ui/` = 토큰만 아는 primitive. **도메인 타입을 import 하지 않는다** / `components/` = 도메인을 아는 조합
- 🚨 **화면을 그리는 외부 라이브러리는 `<dialog>`(시트) · `react-day-picker`(달력) · `recharts`(11-1 변화 그래프 한 자리)뿐이다.** 늘리지 않는다.
  화면을 안 그리는 것(`es-hangul` · `react-error-boundary`)은 이 규칙 밖이다
- ⚠️ `cn()` 은 **tailwind-merge 가 아니다.** primitive 밖에서 `className` 으로 색·크기를 덮지 않는다 — 필요하면 변형을 primitive 안에 더한다
- 🚨 **`<Screen>` 에 `py-*` 를 넘기지 않는다.** 위·아래 고정 바는 `topBar` · `bottomBar` · `nav` 로 넘긴다. 화면 제목은 `PageTitle`
- 🚨 **하단 네비(`ChildNav`)는 가는 곳 네 화면과 설정에만 붙인다.** 흐름 중인 화면(04 · 05 · 11-2 · 탈퇴)에 붙이면 고르는 도중에 새는 길이 생긴다
- 🚨 **돌아가기는 `IconButtonLink`**(`label` 에 "뒤로" 가 아니라 가는 곳), **버튼처럼 보이는 링크는 `ButtonLink`** 다.
  `router.back()` · `<button onClick={router.push}>` 로 대신하지 않는다
- 🚨 **승인 게이트 표식(`btn-approve` · `caution`)은 게이트 2곳 전용이다.** 05 채택 · 07 교정 확인 · 08 저장 · 가입 동의는 게이트가 아니다
- 🚨 **토스트는 "조용히 되돌아간 실패" 에만 쓰고, 바텀시트 안에서 부르지 않는다** (`<dialog>` top layer 뒤로 깔린다)
- 🚨 **화면에서 부르는 말과 코드의 이름이 다르다** — 관찰(`observation`)은 **기록**, 프로필(`profile_affinity`)은 **기억**.
  화면 문구만 바꾸고 식별자·타입·쿼리 키는 계약서 이름 그대로 둔다
- ⚠️ 라이브러리를 안 쓰는 대신 **접근성(포커스 트랩 · ESC · 스크롤 락)이 전부 우리 책임**이다

### 로그인 — API 호출이 아니라 페이지 이동이다

결정은 [`docs/web/kakao-login-v1.md`](../../docs/web/kakao-login-v1.md), 코드를 만질 때 걸리는 것과 가입 동의 · 01 · 02 는
[`auth-consent-v1.md`](../../docs/web/auth-consent-v1.md) 다.

- 🚨 **`lib/auth/oauth.ts` 가 시작하는 유일한 지점이다.** 🚨 **`bind` 를 빼먹으면 계정이 넘어간다** — `oauth-bind.ts` 의 주석을 읽고 만진다
- 🚨 **서버가 `?error=` 에 실은 코드를 화면에 그대로 내지 않는다.** 복귀 경로는 `/` 로 시작하고 `//` 가 아닌 값만 통과시킨다
- 🚨 **`/auth/callback` 은 `AuthGate` 로 감싸지 않는다**
- 🚨 **"전체 동의" 를 만들지 않는다** · 법정대리인 확인을 상수 `true` 로 보내지 않는다 · 무엇을 묻는지의 정본은 `GET /policies` 다

### 세션 — `sessionStorage` 다

- 🚨 **토큰을 `localStorage` 에 두지 않는다.** 불투명 난수라 디코딩하지 않는다. 판정은 `hasLiveSession()`
- **`401 unauthenticated` 는 `lib/api/client.ts` 에서 한 번에 처리한다.** 화면마다 다루지 않는다 (예외는 탈퇴 화면 하나 · #389)
- 인증이 필요한 화면은 `components/auth-gate.tsx` 로 감싼다

### 아이콘 — `lucide-react`

크기 · 굵기 · 도메인 매핑은 `components/ui/icon.tsx` 한 곳에 있다. 화면에서 `size={18}` 처럼 직접 쓰지 않는다.
🚨 `DomainIcon` 은 **`aria-hidden`** 이다 — 의미는 항상 옆의 텍스트 라벨이 진다.

### 서버 상태 vs 클라이언트 상태 — 섞지 않는다

- **TanStack Query** = 서버에서 온 것 전부. 관찰·프로필·제안·일정·홈.
- **Zustand** = 서버가 모르는 것만. 토큰, 지금 보고 있는 아이, 열려 있는 모달.
- 🚨 **아이 이름·생일·알레르기를 Zustand 에 캐시하지 않는다.** 개인정보는 화면이 필요할 때 Query 로 가져오고, 스토어에는 `id` 만 둔다 (§2 개인정보).

쿼리 키는 손으로 쓰지 말고 `qk` 팩토리를 쓴다. 아이 스코프는 전부 `qk.child(cid)` 아래라서, 아이를 바꿀 때 한 번의 무효화로 끝난다.

### API 호출

- `fetch` 를 직접 부르지 않는다. **`src/lib/api` 의 `api.get/post/...` 만** 쓴다 — Bearer 토큰·에러 봉투·Idempotency 처리가 거기 한 곳에 있다.
  예외(`sendClientError`)와 그 이유는 [`api-client-v1.md`](../../docs/web/api-client-v1.md) §1
- 토큰은 `useSessionStore.signIn()` 이 `setAuthToken()` 으로 클라이언트에 밀어 넣는다. 컴포넌트에서 헤더를 직접 만들지 않는다.

### 🚨 되돌릴 수 없는 5곳 — `api.post` 로 직접 부르지 않는다

`POST /children/{cid}/inputs` · `/onboarding` · `/photos` · `/health-safety`(게이트 ㉡ · 고치기 `PATCH` 포함) · **초안 제출**(게이트 ㉠ · `POST`/`PATCH` events).
메서드가 둘인 이유와 각 줄의 규칙 원문은 [`api-client-v1.md`](../../docs/web/api-client-v1.md) §2.

**`lib/api/operations.ts` 의 전용 함수로만 부른다.** 키가 필수 인자라 빠뜨리면 `tsc` 가 잡는다.
경로도 `lib/api/idempotency.ts` 의 `idempotentPath` 표에서만 만든다 — **표를 거치지 않고 이 5개를 부를 방법은 없어야 한다.**

```tsx
const idem = useIdempotencyKey();                       // @/lib/api/use-idempotency-key
const mutation = useMutation({
  mutationFn: () => submitEventDraft(childId, body, idem.current()),
  onSuccess: () => { idem.rotate(); },                  // 🚨 성공한 뒤에만
});
```

- 🚨 **`mutationFn` 안에서 `newIdempotencyKey()` 를 부르지 않는다.** 재시도마다 새 키가 나가면 중복 방지가 무의미해진다 (타입이 못 잡는다)
- 🚨 **`rotate()` 를 실패 경로에서도, 끝을 확인하지 못한 경로에서도 부르지 않는다.** 재시도는 같은 키다
- 🚨 **사용자가 고쳐 쓸 수 있는 본문은 `current(text)` 로 본문을 넘긴다** — 키가 본문을 따라 바뀌어야 "같은 키 · 다른 본문" 422 에 안 갇힌다
- `422 idempotency_key_reuse` 가 화면에 도달하면 **버그다.**

### 에러

`ApiError` 로 잡는다. 코드별 화면 처리 원문은 [`api-client-v1.md`](../../docs/web/api-client-v1.md) §3 — 🚨 대부분이 **"다시 시도" 를 세우면 안 되는** 코드다
(`daily_input_limit` · `reply_context_unavailable` · 알레르기 `already_exists` · `not_found`). 누를 때마다 같은 응답이 온다.

- `consent_required` (403) → 저장이 안 된 상태다. 동의 화면으로 보낸다
- `llm_unavailable` (503) → 🚨 **기본값으로 대체하지 않는다.** 실패했다고 화면에 말한다
- **뮤테이션은 자동 재시도가 꺼져 있다** — 승인 게이트를 두 번 실행할 수 있어서다. 켜지 말 것
- 🚨 **승인 게이트에 낙관적 업데이트를 쓰지 않는다.** 응답을 받은 뒤 `invalidateQueries` 로 갱신한다.
  되돌릴 수 있는 곳에서 낙관적 업데이트를 쓰면 같은 대상의 요청을 `lib/serial-queue.ts` 로 직렬화한다

### 에러 바운더리 — API 실패와 다른 것을 잡는다

바운더리는 **렌더 도중 던져진 예외**만 잡는다. 누가 무엇을 잡는지의 표는 [`api-client-v1.md`](../../docs/web/api-client-v1.md) §4.

- 🚨 **`throwOnError` 를 켜서 쿼리 실패를 바운더리로 올리지 않는다** — 부분 실패가 화면 전체 교체가 되어 NF-06 이 깨진다
- 🚨 **에러 메시지를 화면에도 콘솔에도 내보내지 않는다** (`lib/report-render-error.ts` 한 곳 · `name` 과 `digest` 만). `new Error()` 에 사용자 입력을 넣지 않는다
- 🚨 **승인 게이트 2곳에 `AppErrorBoundary` 를 붙이지 않는다** — 되돌릴 수 없는 것 앞에서는 화면째 실패하는 편이 안전하다

### SSE

`EventSource` 를 쓰지 않는다. Authorization 헤더를 못 붙여서 NF-09 를 깬다.
`streamRunEvents(runId, signal)` 을 async iterator 로 돌린다.

### run 상태 — `runReducer` · `subscribeRun`

run 상태는 서버 상태도 클라이언트 상태도 아니다 (구독형). 04 대화(`stores/conversation.ts`)와 08 사진(`useRunStream`)이 같은 리듀서 · 구독을 쓴다.
규칙 원문은 [`chat-run-v1.md`](../../docs/web/chat-run-v1.md) 다. 어기면 최상위 §2 가 깨지는 것만:

- 🚨 **`partial` 이 왔으면 `done` 이 와도 부분 결과다** (NF-06). `guidance` · `note` · `unavailable` 과 `done` + `completed: false` 는 **실패가 아니다**
- 🚨 **서버가 말한 끝(`done` · `partial` · `failed`)과 클라이언트가 스스로 끝낸 `unconfirmed` 를 섞지 않는다.**
  원문을 비워도 되는지는 `isRunConfirmed(status)` 하나로 판단한다
- 🚨 **종료 이벤트 없이 스트림이 닫히는 경우를 반드시 처리한다** (`pumpRunEvents`). 안 하면 진행 화면이 영원히 돈다
- 🚨 **원문의 정본은 보내기 전엔 `stores/draft.ts`, 보낸 뒤엔 그 줄의 말풍선이다.** 둘 다 메모리 전용 — `persist` 금지
- 🚨 **되묻기 답은 원문을 다시 보내지 않고 `reply_to` 로 맥락을 잇는다.** Idempotency-Key 는 `text` 가 아니라 본문 전체(`keyPayload`)에 묶는다

---

## 4. 화면을 그릴 때 지켜야 하는 것

최상위 §2 가 프론트에 떨어지는 지점만 추렸다. 어기면 서비스가 성립하지 않는다.

- 🚨 **일반 추천과 개인화 추천을 한 컴포넌트로 그리지 않는다.** 일반 추천에는 "또래 기준 일반 추천" 을 **화면에 명시**하고 쌓인 기록 건수를 그대로 보여준다. 되물을 때 질문은 **1개**.
  🚨 **둘이 한 화면에 같이 서지 않는다** — 일반 추천은 개인화 **대신** 나간다. 05 는 일반 추천이 위, 되묻는 질문이 아래다.
- 🚨 **"근거 없음" 상태를 만들지 않는다.** `evidence` 가 빈 suggestion 은 서버가 버리고 `scarcity` 로 내린다. 그 상태를 화면에 그리면 버그를 UI 로 덮는 것이다.
- 🚨 **`is_stale` 인 근거를 단독으로 보여주지 않는다** (6개월 · NF-08).
- 🚨 **"기록했어요" 는 서버 저장이 끝난 뒤에만 말한다.** 특히 알레르기다 — 보호자의 선택(이 제안을 막는다)과
  서버 저장 완료(다음 제안부터 걸러낸다)는 **다른 상태**다. 선택만 보고 "앞으로도 걸러내요" 라고 말하면,
  저장이 실패한 채로 닫은 보호자가 등록되지 않은 알레르기를 등록됐다고 믿는다 (PR #71 리뷰).
  실패는 같은 키로 **다시 시도**할 길을 같은 자리에 둔다 — 안내만 지우는 버튼을 두지 않는다.
- 🚨 **`partial` 이벤트를 실패 화면으로 떨어뜨리지 않는다.** Agent 2개 중 1개만 성공해도 그 화면을 보여준다 — 성공과 실패를 **한 화면에** 섞는다 (NF-06).
- 🚨 **`failed` 는 `raw_text` 를 돌려준다.** 입력창에 그대로 남겨 놓는다 — 부모가 다시 타이핑하게 만들지 않는다.
- 🚨 **날짜·나이를 프론트에서 계산하지 않는다.** `age_display` · `observed_label` · `state_reason` 은 서버가 만든 문구다 (§3 — 100% 맞아야 하는 것은 코드가, 그것도 서버가 한다).
  - 그래서 01 화면은 프로토타입의 **나이 드롭다운 대신 생일**을 받는다. 나이 → 생일 환산이 곧 날짜 계산이다.
  - ⚠️ 같은 이유로 `GET /dev-screening/items` 에 `age_months` 대신 `child_id` 를 보내기로 했었다 — **지금은 화면이 그 경로를 아예 부르지 않는다.** 02 에서 발달 문항을 뺐다 (화면에 "발달 상태" 라는 말이 서면 그때부터 발달 평가로 읽힌다 · 최상위 §1). 타입과 목은 남아 있다.
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

원문(서체 두 벌 · `cursor` · `hover` 변형 · safe area 의 출처)은 [`components-v1.md`](../../docs/web/components-v1.md) §3 이다.

- 색·radius·폰트는 `globals.css` 의 `@theme` 토큰으로만. 컴포넌트에서 `#hex` 를 직접 쓰지 않는다.
  🚨 **토큰을 "비슷한 값" 으로 쓰지 않는다** — 이름이 곧 용도다 (`brand-ink` 는 `brand-soft` 배경 위 텍스트)
- **`/design-system` 을 열어 보고 만든다.** 원본은 종류마다 한 곳이다 — 의도는 [디자인 시스템](../../docs/web/design-system-v1.md), 공통 값은 `@theme`,
  한 컴포넌트의 치수·상태는 `components/ui/` 코드. 🚨 **문서와 코드가 어긋나면 바뀐 이유부터 찾고, 같은 PR 에서 `DESIGN.md` 사본까지 맞춘다**
- 🚨 **높이는 `min-h-*` 토큰으로만 쓴다.** 글자가 들어가는 요소에 고정 높이를 주지 않고, `truncate` 로 잘라 맞추지 않는다
- 🚨 **실패를 빨강으로 칠하지 않는다** (`surface-muted` + `ink-muted`). `danger` 는 알레르기·건강 중단에만, `caution` 은 승인 게이트 2곳에만
- 🚨 **일반 추천과 개인화 추천을 색으로 구분하지 않는다** — 라벨과 기록 건수가 본체다
- 🚨 **화면에 보이는 글자에 이모지·기호·줄표(`—`)를 쓰지 않는다** (주석·문서·`/design-system` 은 대상이 아니다)
- **상호작용 상태는 primitive 안에 있다.** 화면에서 `hover:` · `active:` 를 따로 붙이지 않는다 (웹뷰에는 호버가 없다)
- 🚨 **도메인 4색을 한 화면에 다 쓰지 않는다** (최대 2개) · 🚨 **브랜드색은 정해진 여섯 자리에만** · 등장·스크롤 애니메이션을 만들지 않는다
- **서체** — 기본은 `font-sans`(학교안심 날개), 약관처럼 읽고 동의하는 문서 화면은 통째로 `font-doc`(Pretendard).
  🚨 `hakgyoansim.css` · `pretendard.css` 는 생성된 파일이라 손으로 고치지 않는다 · 🚨 폰트 CDN 을 쓰지 않는다
- **모바일 우선.** 이 화면은 대부분 [`apps/mobile`](../mobile) 웹뷰 안에서 보인다

---

## 6. 명령어

스크립트 목록은 `package.json` 에 있다. CI · 배포 원문은 [`toolchain-deploy-v1.md`](../../docs/web/toolchain-deploy-v1.md) 다.

- **최초 세팅** — `cp .env.example .env.local`. `NEXT_PUBLIC_API_BASE_URL` 이 없으면 앱이 뜨지 않고 바로 에러를 던진다
- 루트에서 `make web-*`. `make web-check` = format(검사만) + typecheck + lint + test. CI(`ci-web.yml`)는 여기에 build 와 e2e 를 더 돈다
- 🚨 **`make web-build` 를 PR 전에 한 번 돈다.** `pnpm dev` 가 통과하는데 프로덕션 빌드만 멈추는 자리가 있다 (§3 라우팅의 Suspense 경계)
- 🚨 `NEXT_PUBLIC_*` 는 **브라우저 번들에 그대로 박힌다.** 비밀은 넣지 않는다. 배포 이미지에서는 런타임 환경변수가 아니라 `--build-arg` 라, 주소가 바뀌면 다시 굽는다
- 포맷이 어긋나면 `make web-fmt` 로 고쳐서 **별도 커밋**으로 올린다

---

## 7. 목(mock) 서버

백엔드 없이도 화면을 만들 수 있게 [MSW](https://mswjs.io) 가 계약서 응답을 대신 내려준다. `.env.local` 의 `NEXT_PUBLIC_API_MOCKING=enabled` 로 켜고,
**개발 환경에서만** 동작한다 (프로덕션 빌드에는 msw 가 통째로 빠진다).

진짜 이유는 **실서버로 못 만드는 상태**(근거 부족 · 부분 결과 · 저장 뒤 끊김 · 되묻기 상한 등)를 `?scenario=` 로 만들어 §4 가 지켜지는지 보는 것이다.
시나리오 표와 목 규칙 원문은 [`mock-and-tests-v1.md`](../../docs/web/mock-and-tests-v1.md) §1, 시나리오별 주소와 저장소 초기화는 [`mock-screens-v1.md`](../../docs/web/mock-screens-v1.md).

- **응답은 `lib/api/types.ts` 타입으로 강제한다.** 형태를 `any` 로 풀지 않는다
- **되돌릴 수 없는 5개는 `withIdempotency()` 로 감싼다** (`handlers/idempotency.ts`)
- **백엔드가 붙어도 목을 지우지 않는다** · 새 시나리오를 더하면 위 두 문서의 표에 같이 적는다
- 🚨 **fixtures 에 실제 사용자 발화나 아이 정보를 넣지 않는다.** 저장소가 public 이다 (최상위 §9)

---

## 8. 테스트

```bash
pnpm test          # vitest run — 목 계약 · lib · 스토어 · 게이트 정적 검사
pnpm test:e2e      # Playwright — 목 서버로 띄운 화면에서 화면 규칙 (make web-e2e)
```

무엇을 거는지와 규칙 원문은 [`mock-and-tests-v1.md`](../../docs/web/mock-and-tests-v1.md) §2 다.

- 🚨 **테스트 전용 핸들러를 만들지 않는다.** `src/mocks/server.ts` 는 브라우저 워커와 같은 핸들러를 쓴다
- 🚨 **목에 프로세스 수명만큼 사는 상태를 더하면 리셋 함수를 `src/test/setup.ts` 의 `afterEach` 에 건다**
- 🚨 **`src/test/approval-gates.test.ts` 가 깨지면** 목록을 고치기 전에 **게이트를 늘리는 변경인지**부터 본다 (최상위 §2)
- e2e 는 `next dev` 로만 돈다 · `page.route` 로 응답을 못 바꾼다 (실패를 만들려면 목 시나리오를 더한다)
- 🚨 **픽스처·테스트 문자열에 실제 사용자 발화나 아이 정보를 넣지 않는다**

---

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
