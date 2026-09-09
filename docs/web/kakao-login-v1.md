# 카카오 로그인 v1

문서 목적: 웹 브라우저와 앱 웹뷰에서 카카오 로그인을 **프론트가 어떻게 처리하는지**를 고정한다. [`docs/api/auth-kakao-v1.md`](../api/auth-kakao-v1.md) 가 서버 계약의 정본이고, 이 문서는 그 계약이 화면·셸·저장소에 떨어지는 지점만 다룬다.

기준 브랜치: `feat/fe-28-login-onboarding`
작성일: 2026-09-09
담당: 고태영
선행 문서: [`docs/api/auth-kakao-v1.md`](../api/auth-kakao-v1.md) (**정본** — 엔드포인트·에러 코드·세션 수명), [`docs/api/api-interface-v1.html`](../api/api-interface-v1.html) §04 (동의 게이트), [`docs/web/design-system-v1.md`](./design-system-v1.md) (로그인 버튼 사양)

> 서버 명세는 확정됐다. 남은 팀 결정은 **동의 전 `parent` 미생성** 한 건이고([명세 §6-1](../api/auth-kakao-v1.md)), 그 결정이 §4-4·§4-5 를 바꾼다. 프론트가 서버에 추가로 요청한 것은 §9-2 에 있다.
>
> **프론트는 이 문서대로 구현했다** (§4-1 표에 파일별 상태). 동의 화면(§4-5)만 별도 이슈로 남았고, 그때까지 콜백 화면이 멈춘다.
> ⚠️ 서버 명세 [`docs/api/auth-kakao-v1.md`](../api/auth-kakao-v1.md) 는 아직 `docs/be-18-auth-kakao` 브랜치에 있다 — `develop` 에 합쳐지기 전까지 이 문서의 링크가 깨져 보인다.

---

## 1. 한눈에

|                       |                                                                                                     |
| --------------------- | --------------------------------------------------------------------------------------------------- |
| 카카오 JS SDK         | **로그인에 쓰지 않는다** — 클라이언트가 `access_token` 을 발급받는 메서드가 없다                      |
| 네이티브 카카오 SDK   | **쓰지 않는다** — 셸이 토큰을 만지면 [`apps/mobile/CLAUDE.md`](../../apps/mobile/CLAUDE.md) §1 이 깨진다 |
| 인가 코드 교환        | **서버**가 한다. 웹·앱 모두 카카오 토큰을 못 본다                                                    |
| 클라이언트가 보내는 것 | 서버가 발급한 **1회용 코드 + bind 비밀** 한 쌍                                                       |
| 시작 파라미터         | `client=web\|app` (열거값) · `bind` (base64url 43자)                                                 |
| 복귀 경로             | 웹 `/auth/callback?code=` · 앱 `yukameo://auth?code=` — **쿼리 모양이 같다**                          |
| 앱에서 카카오 페이지  | **인앱 인증 세션** (Android = Custom Tabs, iOS = `ASWebAuthenticationSession`)                        |
| 웹뷰 안에서 OAuth     | **금지** — 앱이 카카오 계정 입력창을 들여다볼 수 있는 구조가 된다                                     |
| 세션                  | 불투명 난수 · **12시간** · refresh 없음 · **`sessionStorage`**                                        |
| 카카오 키             | 프론트·셸 모두 **모른다.** `NEXT_PUBLIC_KAKAO_*` 를 만들지 않는다                                     |
| 신규 코드             | `lib/auth/oauth.ts` · `lib/auth/oauth-bind.ts` · `app/auth/callback/page.tsx` · 동의 화면 (별도 이슈)   |

**한 문장** — 로그인은 화면이 부르는 API 가 아니라 **페이지 이동**이다. 웹은 서버가 준 시작 URL 로 떠났다가 `/auth/callback` 으로 돌아오고, 앱은 그 이동만 인앱 브라우저로 가로챈다. 돌아온 뒤에야 API 를 부른다.

---

## 2. 흐름

번호는 [명세 §2-2](../api/auth-kakao-v1.md) 와 같다. 프론트가 하는 일만 굵게 뒀다.

### 2-1. 웹 브라우저

```
① 00 화면 진입 시 GET /auth/kakao/status 를 prefetch
   → { ready: true, start_url: "https://<API>/api/v1/auth/kakao" }
② "카카오로 시작하기" → bind 생성(256비트) · sessionStorage 보관
   → location = start_url + "?client=web&bind=<비밀>"
③~⑥ 서버가 state 쿠키 심고 카카오로 302 → 사용자 인증 → 서버 콜백이 state 대조 →
     인가 코드 교환 → auth_handoff 행 생성 → 복귀 URL 로 302
⑦ /auth/callback?code=… 도착
   → POST /auth/kakao { code, bind }
⑧ 응답이 두 갈래 (union)
   기존 회원: { token, expires_in, is_new: false, parent, consent_required }
   신규     : { status: "consent_required", consent_code }
⑨ 신규만 → 동의 화면 → POST /auth/kakao/signup { consent_code, bind, consents }
```

### 2-2. 앱 웹뷰

② 의 이동과 ⑦ 의 도착만 셸이 가로챈다.

```
②' 웹이 client=app 을 실어 이동을 시도
   → 셸의 onShouldStartLoadWithRequest 가 시작 URL 을 잡아 인앱 인증 세션으로 연다
     (시스템 브라우저 X · 웹뷰 X)
③~⑥ 동일. 단 서버가 yukameo://auth?code=… 로 302
⑦' 인증 세션이 스킴을 가로채 스스로 닫고 셸에 URL 을 넘긴다
   → 셸이 https://<web>/auth/callback?code=… 로 바꿔 웹뷰에 싣는다
⑦~⑨ 동일 — 웹은 자기가 어디서 돌아왔는지 몰라도 된다
```

**⑦ 이후가 웹·앱 공통이라는 게 이 설계의 요점이다.** 화면과 API 호출은 한 벌이고, 갈리는 것은 ② 와 복귀 스킴뿐이며 그 둘은 셸이 흡수한다. 서버가 쿼리 모양을 웹·앱 동일하게 둬서 파싱 코드도 한 벌이다.

---

## 3. 왜 이 구조인가

### 3-1. 카카오 JS SDK 로는 `access_token` 을 못 받는다

[`Kakao.Auth` 레퍼런스](https://developers.kakao.com/sdk/reference/js/release/Kakao.Auth.html) 의 메서드는 `authorize()` · `setAccessToken()` · `getAccessToken()` · `getStatusInfo()` · `logout()` · `cleanup()` 이다. **클라이언트가 토큰을 발급받는 메서드가 없다.** `authorize()` 는 인가 코드를 서비스 서버로 보내는 리다이렉트 흐름이고, 이미 발급된 토큰을 넣는 `setAccessToken()` 이 따로 있다는 것도 토큰이 서버에서 내려온다는 뜻이다.

→ 웹에서 계약서 §04 의 `{access_token}` 을 만들 방법이 없다. SDK 를 로그인에 쓰지 않는다.

### 3-2. 셸은 토큰을 만지지 않는다

네이티브 카카오 SDK 를 넣으면 셸이 `access_token` 을 받아 브릿지로 웹에 넘기게 된다. [`apps/mobile/CLAUDE.md`](../../apps/mobile/CLAUDE.md) §1 의 **"토큰을 들고 있지 않는다"** 를 정면으로 깬다. 셸이 자격증명을 다루기 시작하면 갱신·만료·로그아웃까지 셸이 알아야 한다.

인앱 인증 세션은 셸이 **URL 을 열고 URL 을 돌려받을 뿐**이라 이 경계를 지킨다. 카카오 키도 서버에만 있어서 [`apps/mobile/CLAUDE.md`](../../apps/mobile/CLAUDE.md) §4 의 "셸이 아는 비밀은 없어야 정상" 이 유지된다.

### 3-3. 웹뷰 안에서 OAuth 를 태우지 않는다

[`apps/mobile/CLAUDE.md`](../../apps/mobile/CLAUDE.md) §4 가 이미 적어 둔 이유다 — 사용자가 주소창을 못 봐서 피싱과 구분할 수 없고, 더 나쁘게는 **앱이 카카오 계정 입력창을 들여다볼 수 있는 구조**가 된다.

그렇다고 시스템 브라우저(`Linking.openURL`)로 내보내면 로그인 한 번에 앱이 통째로 두 번 전환된다 — [#23](https://github.com/kakaotechcampus-4/ktc4-pusan-3/issues/23) 에서 걸린 문제가 그거다.

| | 웹뷰에 태우기 | 시스템 브라우저 | **인앱 인증 세션** |
| --- | --- | --- | --- |
| 주소창 | 안 보임 | 보임 | 보임 |
| 앱이 입력창을 볼 수 있나 | **볼 수 있음** | 불가 | 불가 (별도 프로세스) |
| 화면 전환 | 없음 | 앱 밖으로 2회 | 없음 (앱 위에 얹힘) |
| 쿠키 저장소 | 웹뷰 격리 | 시스템 | 시스템 (= `state` 왕복 성립) |
| 복귀 | — | 딥링크 필요 | 스스로 닫고 URL 반환 |

### 3-4. `bind` — 1회용 코드만으로는 계정이 넘어간다

⑥ 의 코드는 URL 에 실려 돌아온다. 그것만으로 세션이 나오면, 공격자가 **자기 카카오 로그인으로 얻은 코드**를 링크나 `yukameo://auth?code=…` 딥링크로 피해자에게 던져 **피해자를 공격자 계정에 로그인**시킬 수 있다. 그 뒤 피해자가 입력하는 아이 이름·생일·건강 정보가 전부 공격자 계정에 쌓인다.

막는 방법 — 시작할 때 브라우저가 랜덤 비밀(256비트)을 만들어 ② 에서 `bind` 로 싣고, 서버는 그 **해시**를 `auth_handoff` 행에 묶는다. ⑦ 에서 같은 값을 다시 제시해야 세션이 나온다. 남이 던진 코드는 해시가 애초에 안 맞는다.

🚨 `state` 쿠키로는 이걸 못 막는다. `state` 는 카카오 왕복(③↔⑤)을 지키고 서버 콜백에서 소비되는데, 막아야 하는 마지막 홉은 그 뒤의 ⑦ 이다. **둘 다 필요하다.**

### 3-5. 복귀 경로를 프론트가 지정하지 않는다

`client` 는 **열거값**(`web` | `app`)이고 실제 복귀 URL 은 서버 환경변수(`AUTH_RETURN_URL_WEB` · `AUTH_RETURN_URL_APP`)에서 온다. 프론트가 URL 이나 경로를 넘기는 통로는 없다 — 그게 열리면 **오픈 리다이렉트**가 된다.

그래서 **"로그인 후 원래 보던 화면으로 돌아가기" 는 프론트 책임이다.** 서버는 관여하지 않는다 (§4-2).

---

## 4. 프론트 구현

### 4-1. 파일별 책임

| 파일 | 무엇을 하나 | 상태 |
| --- | --- | --- |
| [`lib/auth/oauth.ts`](../../apps/web/src/lib/auth/oauth.ts) | 로그인 **시작**. `ready` 확인 → bind 생성 → 절대 `start_url` 로 이동. provider·복귀경로 기억 | ✅ 신설 (`lib/auth/kakao.ts` 삭제) |
| [`lib/auth/oauth-bind.ts`](../../apps/web/src/lib/auth/oauth-bind.ts) | bind 비밀 생성·보관·소비 | ✅ 신설 |
| [`app/auth/callback/page.tsx`](../../apps/web/src/app/auth/callback/page.tsx) | 돌아온 코드를 교환하고 다음 화면으로 보냄 | ✅ 신설 |
| [`app/auth/consent/page.tsx`](../../apps/web/src/app/auth/consent/page.tsx) | 가입 동의 4건 → `POST /auth/kakao/signup` + `POST /consents` × 2 | ✅ 신설 |
| [`lib/consent.ts`](../../apps/web/src/lib/consent.ts) | 스코프 정본 · 약관 버전 · 어느 엔드포인트로 가는지 | ✅ 신설 |
| [`app/page.tsx`](../../apps/web/src/app/page.tsx) | 버튼 → 시작 함수. status prefetch. 성공 처리는 콜백 화면으로 이사 | ✅ 수정 |
| [`stores/session.ts`](../../apps/web/src/stores/session.ts) | 저장소 `localStorage` → **`sessionStorage`**, `expiresAt` 보관, `hasLiveSession()` | ✅ 수정 |
| [`lib/api/client.ts`](../../apps/web/src/lib/api/client.ts) | `401 unauthenticated` → 세션 비우고 00 으로 (핸들러는 Providers 가 꽂는다) | ✅ 수정 |
| [`components/auth-gate.tsx`](../../apps/web/src/components/auth-gate.tsx) | 만료까지 함께 판정 · 튕기기 전에 복귀 경로 저장 | ✅ 수정 (유지 예정이었음 — §4-2) |
| [`mocks/handlers/auth.ts`](../../apps/web/src/mocks/handlers/auth.ts) | 새 흐름으로 교체 (status · union 응답 · signup · logout) | ✅ 수정 |
| [`mocks/start.ts`](../../apps/web/src/mocks/start.ts) | `startMocks()` 를 멱등하게 — 아래 §7 마지막 항목 | ✅ 수정 (예정에 없었음) |

**"토큰을 받는 지점은 한 곳" 이라는 성질은 유지된다** ([`apps/web/CLAUDE.md`](../../apps/web/CLAUDE.md) §3). 다만 파일 이름과 함수 모양이 바뀐다 — provider 4종을 같은 틀로 쓰므로 `kakao.ts` 가 아니라 `oauth.ts` 이고, `requestKakaoAccessToken(): Promise<string>` 이 아니라 `startOAuthLogin(provider)` 다. 성공하면 페이지가 떠나므로 값을 돌려주지 않는다.

### 4-2. 로그인 시작

```ts
// lib/auth/oauth.ts — 실제 구현
export type LoginStart = { kind: "left" } | { kind: "internal"; path: string };

export function startOAuthLogin(provider: AuthProvider, status: AuthStatus): LoginStart {
  if (!status.ready) throw new OAuthUnavailableError();

  rememberProvider(provider);                 // 복귀 경로엔 provider 가 없다 (§9-2)
  const bind = createBind();

  if (MOCK_ONLY) return { kind: "internal", path: `${CALLBACK_PATH}?code=${PLACEHOLDER_CODE}` };

  const url = new URL(status.start_url);
  url.searchParams.set("client", isAppShell() ? "app" : "web");
  url.searchParams.set("bind", bind);
  window.location.href = url.toString();
  return { kind: "left" };
}
```

**설계안에서 두 가지가 달라졌다.**

- **`status` 를 인자로 받는다.** 함수 안에서 `queryClient` 를 부르면 lib 이 Query 인스턴스를 알아야 하고, 그러면 prefetch 여부를 이 함수가 판단하게 된다. 00 화면이 이미 `useQuery` 로 들고 있는 값을 넘기는 편이 "prefetch 된 값을 쓴다" 는 의도에 더 가깝다.
- **`LoginStart` 를 돌려준다.** 목에서는 앱 **내부** 경로로 가는데, 내부 이동을 `window.location` 으로 하면 전체 리로드가 되고 (`@next/next/no-location-assign-relative-destination` 이 이걸 잡는다) `sessionStorage` 왕복도 의미가 없어진다. 어디로 갈지는 lib 이 정하고 **이동은 화면이 라우터로** 한다.

- 🚨 **서버가 준 절대 `start_url` 로 이동한다.** 상대경로로 가면 Next 오리진에서 출발하는데 카카오는 API 오리진으로 돌려보내서, 시작 때 심은 `state` 쿠키가 서버 콜백에 실리지 않는다. 로컬은 포트가 달라도 쿠키가 공유돼 우연히 통과하니 **배포 기준으로 판단할 것.**
- `status` 는 **00 화면 진입 시 prefetch** 한다. 버튼을 누른 뒤 조회하면 이동 전에 왕복이 한 번 낀다. `ready: false` 면 버튼을 비활성화하고 "아직 연결 전" 이라고 말한다.
- `client` 판정은 셸 여부다. 앱 웹뷰는 `applicationNameForUserAgent` 로 붙은 `YukameoApp/…` 을 UA 에서 찾아 판별한다.
- **복귀 화면 복원은 프론트가 한다** (§3-5). 🚨 복원할 때 **저장된 값이 우리 앱 내부 경로인지 검사한다** — 그냥 `location = 저장값` 으로 쓰면 프론트가 오픈 리다이렉트를 자기 손으로 만든다. `/` 로 시작하고 `//` 가 아닌 값만 허용한다.
- ⚠️ **저장 시점이 이 문서의 초안과 다르다.** "로그인을 **시작할 때** 현재 경로를 저장" 하면 그 시점의 경로는 항상 로그인 화면(`/`)이라 복원할 게 없다. 실제로 복원이 필요한 곳은 세션이 끊겨 **튕겨 나오는 지점** — [`AuthGate`](../../apps/web/src/components/auth-gate.tsx) 와 [`client.ts`](../../apps/web/src/lib/api/client.ts) 의 401 처리 — 이므로 **거기서** `rememberReturnPath(currentPath())` 를 부른다. `startOAuthLogin` 은 저장된 값을 덮지 않는다.

### 4-3. bind 비밀

```ts
// lib/auth/oauth-bind.ts — 실제 구현
const KEY = "yukameo.oauth.bind";

export function createBind(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));            // 256비트
  const secret = btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");       // base64url 43자
  sessionStorage.setItem(KEY, secret);
  return secret;
}

export function readBind(): string { return sessionStorage.getItem(KEY) ?? ""; }
export function clearBind(): void { sessionStorage.removeItem(KEY); }
```

**`sessionStorage` 를 쓴다** ([명세 §2-2](../api/auth-kakao-v1.md)). 이 흐름에서는 살아남는다 — 웹은 같은 탭에서 오리진을 떠났다 돌아오는 것이라 탭이 살아 있는 동안 유지되고, 앱은 인앱 인증 세션이 앱을 죽이지 않아 웹뷰 컨텍스트가 그대로다.

⚠️ **재검토 조건이 하나 있다.** 앱이 인앱 브라우저에 있는 동안 OS 가 프로세스를 정리하면 `yukameo://auth?code=` 로 앱이 새로 열리는데, 그때 웹뷰가 새로 만들어져 `sessionStorage` 가 비어 있다. 지금은 셸이 그 딥링크 복귀 경로를 처리하지 않으므로(§5-2) 어차피 코드가 유실돼 사용자가 다시 누르면 되고, 그래서 `sessionStorage` 로 충분하다. **셸에 `Linking` 복귀를 추가하는 순간 bind 를 `localStorage` 로 올려야** 그 경로가 실제로 완주한다.

교환·가입이 끝나면 성공·실패 모두 지운다. 신규 가입은 ⑨ 에서 같은 bind 를 한 번 더 쓰므로 **⑧ 에서 지우지 않는다.**

### 4-4. 콜백 화면

`/auth/callback` 은 **사용자에게 보여줄 내용이 없는 화면**이다. 스피너 한 장 띄우고 교환만 한다. provider 는 쿼리에 없어서 시작 때 저장한 값을 쓴다(§9-2).

```
?code= 있음 → POST /auth/{provider} { code, bind }
              ├ status 없음  → 기존 회원. signIn(token, expires_in) → 복귀 경로 복원
              │                (consent_required 가 비어 있지 않으면 이동을 멈춘다)
              └ status 있음  → 신규. consent_code 를 들고 동의 화면으로 (§4-5)
?error= 있음 → 코드별 처리 (아래 표)
둘 다 없음   → 00 화면으로 조용히 되돌림 (직접 주소 입력 등)
```

`app/page.tsx` 의 `login.onSuccess` 분기가 통째로 여기로 왔다. 00 화면에는 "버튼 → 시작 함수" 만 남았다.

**동의 화면이 아직 없어서, 멈추는 두 갈래는 콜백 화면이 직접 그린다** (§9-3 이 별도 이슈로 뺐다).

| 갈래 | 지금 화면 |
| --- | --- |
| 신규 (`status` 있음) | "약관 동의가 필요해요" + 동의 화면이 아직 없다는 안내. **토큰을 저장하지 않고**, `bind` 는 남겨 둔다 |
| 기존 회원 + `consent_required` 있음 | 토큰은 저장하고 **이동만 멈춘다.** 남은 동의 건수를 보여준다 |

동의 화면이 생기면 이 두 자리에서 `router.replace` 로 넘긴다 — 화면이 없는 동안 임시로 넘기는 경로를 만들지 않는다.

**`?error=` 는 상태로 들고 있지 않고 URL 에서 파생한다.** effect 안에서 동기 `setState` 를 하면 렌더가 한 번 더 도는데(`react-hooks/set-state-in-effect` 가 잡는다), URL 파라미터는 렌더 단계에서 그대로 읽을 수 있는 값이다. effect 는 **부수효과만** 한다 — 핸드오프 정리와 `oauth_denied` 되돌리기.

**🚨 응답은 `status` 필드 유무로 분기한다.** `token` 유무로 판단하지 않는다 — 신규 응답에는 `token` 이 아예 없고, 그 상태에서 `signIn(undefined)` 를 부르면 토큰 없는 세션이 저장돼 이후 모든 요청이 401 이 된다.

#### 에러 문구는 프론트가 만든다

서버는 [명세 §8-2](../api/auth-kakao-v1.md) 의 **코드만** 싣는다. 문구를 URL 에 실으면 공격자가 프론트 화면에 임의 문구를 띄우는 통로가 되기 때문이다. 매핑은 이 화면 한 곳에 둔다.

| `?error=` | 화면 |
| --- | --- |
| `oauth_denied` | **에러가 아니다.** 사용자가 카카오에서 취소한 것 — 배너 없이 00 화면으로 되돌린다 |
| `invalid_state` | "로그인 요청이 만료됐어요. 다시 시도해 주세요." |
| `invalid_bind` · `invalid_client` | "로그인 요청이 올바르지 않아요. 다시 시도해 주세요." (구버전 번들·저장소 유실) |
| `oauth_provider_error` | "카카오 로그인이 지금 응답하지 않아요. 잠시 후 다시 시도해 주세요." |
| 모르는 코드 | 기본 문구. 🚨 **받은 코드를 화면에 그대로 출력하지 않는다** — 그 순간 문구 주입 통로가 되살아난다 |

교환(`POST /auth/{provider}`)의 JSON 에러는 [`ApiError`](../../apps/web/src/lib/api/errors.ts) 로 잡는다. `401 invalid_handoff` 는 **만료·재사용·bind 불일치를 구분하지 않는다**(서버가 의도적으로 뭉갠다) — 화면도 하나로 "로그인 확인이 만료됐어요. 다시 시도해 주세요." 로 묶는다.

### 4-5. 신규 가입 — 동의 화면

[명세 §6-1](../api/auth-kakao-v1.md) 대로면 **동의 전에는 `parent` 가 없다.** 그래서 신규는 교환 단계에서 토큰을 못 받고, 동의 화면이 계정을 만든다.

콜백이 `consent_code` 를 `sessionStorage` 에 넣고 `/auth/consent` 로 보낸다 — 🚨 **가입 대기표를 주소창·브라우저 기록에 남기지 않으려고** URL 이 아니라 저장소를 쓴다. 대기표 없이 이 주소로 들어오면 00 으로 되돌린다.

#### 항목 4건 — 보내는 곳이 갈린다

| 스코프 | 대상 | 어디로 | 근거 |
| --- | --- | --- | --- |
| `service_terms` | 계정 | `POST /auth/{provider}/signup` | 보호자 각자 1회 |
| `privacy_account` | 계정 | 〃 | 보호자 본인 개인정보 |
| `child_basic` | 아이 | `POST /consents` | 개인정보보호법 제22조의2 |
| `child_health` | 아이 | 〃 | 제23조 — 민감정보 **별도 동의** |

**아이 스코프를 아이가 생기기 전에 받는 이유** — 계약서 §04 가 "동의는 저장보다 먼저다" 로 못박았고 **`child_basic` 없이 `POST /children` 은 403** 이다. 즉 01 화면(아이 만들기)보다 앞서야 한다. 그래서 이 화면에서 4건을 다 받는다.

```
① POST /auth/kakao/signup { consent_code, bind, consents: [계정 2건] }   ← 계정이 여기서 생긴다
② signIn(token, expires_in) · consent_code · bind 정리
③ POST /consents × 2  { scope, action: "granted", policy_version, guardian_attested }
④ /onboarding 으로 — 방금 만든 계정이라 아이가 없다. /me 를 물어볼 것도 없다
```

🚨 **①이 성공한 뒤 ③이 실패하면 계정만 남는다.** 그 상태로 01 화면에 가면 `POST /children` 이 403 이다. 그래서 화면이 단계를 들고 있다가 **재시도할 때 ①을 다시 돌리지 않고 ③만 다시 보낸다.**

#### 화면 규칙

- 🚨 **"전체 동의" 를 두지 않는다.** 민감정보(`child_health`)는 다른 동의와 **구분해서** 받아야 한다 (개인정보보호법 제23조). 한 번에 쓸어 담는 버튼이 그 구분을 없앤다. 4건이라 개별 체크로 충분하다.
- 🚨 **승인 게이트가 아니다.** `btn-approve` 와 `caution` 색을 쓰지 않는다 — 그 둘은 되돌릴 수 없는 2곳 전용이다 (CLAUDE.md §2). 제출은 `btn-primary`.
- 🚨 **"언제든 철회할 수 있어요" 를 쓰지 않는다.** 네 건이 전부 필수라 하나라도 철회하면 서비스가 성립하지 않는다 — `child_basic` 없이는 아이를 등록할 수 없고 `child_health` 없이는 입력조차 저장되지 않는다(둘 다 403). 철회는 스위치 하나 끄기가 아니라 **탈퇴에 가깝고**, 무엇을 지우는지는 아직 미정이다(최상위 CLAUDE.md §10). 지금은 **아무 약속도 하지 않는다.**
- **각 항목에 "상세 보기" 가 있다.** 바텀시트로 확정된 사실만 펼친다 — 받는 것 · 쓰는 곳 · 하지 않는 것 · 동의하지 않으면. 🚨 **약관 전문을 지어내지 않는다**: 보관 기간·삭제 범위가 미정이라 정식 문구를 쓸 수 없고, 없는 조항을 그럴듯하게 넣으면 그대로 배포된다. 시트 하단에 최종본이 아님을 항상 밝힌다.
- 🚨 **다른 입력을 섞지 않는다.** 법적 고지를 읽고 확인하는 화면인데 무관한 입력이 같은 제출 버튼에 묶이면 "무엇에 동의한 것인가" 가 흐려진다 (테크스펙 리스크 ④). 보호자 닉네임을 여기서 받지 않기로 한 것도 같은 이유다 ([명세 §4-4](../api/auth-kakao-v1.md)).
- **근거 법조문을 화면에 그대로 보여준다.** 무엇에 동의하는지 숨기지 않는다.
- 4건이 전부 필수라 하나라도 빠지면 제출 버튼이 비활성이다. 필수는 **색이 아니라 `[필수]` 라벨**로 표시한다.
- `consent_code` TTL 이 10분이다. 만료되면 00 으로 되돌려 다시 시작하게 한다.

### 4-6. 00 화면에서 손볼 것

- `useMutation` → 이동이므로 mutation 이 아니다. `pending` 만 로컬 상태로 든다.
- `status` prefetch → `ready: false` 면 버튼 비활성화 + "아직 연결 전" 안내.
- **취소 복구** — 앱에서 사용자가 인앱 브라우저의 X 를 누르고 돌아오면 이 화면은 그대로 살아 있어서 버튼이 "로그인하는 중…" 에 멈춘다. 성공하면 복귀 URL 로 이동해 화면 자체가 사라지므로 **되돌아온 경우만** 풀어주면 된다.

  ```ts
  useEffect(() => {
    if (!pending) return;
    const onVisible = () => { if (document.visibilityState === "visible") setPending(false); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [pending]);
  ```

### 4-7. 세션 저장 — `localStorage` 를 쓰지 않는다

[명세 §4-3](../api/auth-kakao-v1.md) 이 토큰 저장소를 `sessionStorage` 로 못박았다. [`stores/session.ts`](../../apps/web/src/stores/session.ts) 의 `createJSONStorage(() => localStorage)` 를 `sessionStorage` 로 바꾼다.

| | 값 |
| --- | --- |
| 토큰 | 불투명 난수 43자. JWT 아님 — 디코딩해서 정보를 꺼내려 하지 않는다 |
| 수명 | **12시간** (`expires_in: 43200`) |
| refresh | **없다.** 만료되면 §2 를 다시 탄다 |
| 저장 | `sessionStorage` |

- `signIn(token)` → `signIn(token, expiresIn)` 으로 확장해 만료 시각을 같이 든다. 만료가 임박하면 요청을 보내기 전에 로그인 화면으로 유도할 수 있다 — 401 을 맞고 나서야 아는 것보다 낫다.
- `401 unauthenticated` 는 [`client.ts`](../../apps/web/src/lib/api/client.ts) 에서 한 번에 처리한다 — 세션을 비우고 00 으로. 화면마다 401 을 다루지 않는다.
- `activeChildId` 는 계속 저장해도 된다. 화면 선택값일 뿐 권한 근거가 아니고 `/me` 로 다시 확인한다.
  ⚠️ 다만 스토어를 통째로 옮겼으므로 **이 값도 세션 스코프가 됐다** — 탭을 닫으면 "마지막에 본 아이" 복원이 사라진다. 필드 하나 때문에 저장소를 두 벌로 나누지 않았다 (정본은 URL 이라 화면이 깨지지 않는다).
- 복구 시점에 **이미 만료된 토큰은 들고 있지 않는다.** `onRehydrateStorage` 에서 버린다 — 들고 있어도 첫 요청이 401 이 될 뿐이다.

**🚨 이 저장소 규칙이 의미를 갖는 전제가 프론트에 있다.** 토큰이 JS 에서 접근 가능하므로 XSS 한 건이 세션을 넘기는데, 이 서비스의 XSS 경로는 특정된다 — **기관 공지 붙여넣기·OCR 로 들어온 외부 텍스트가 LLM 을 거쳐 화면에 렌더링된다** (F-07 · F-14). 그래서:

- **LLM 출력과 공지 원문을 HTML 로 렌더링하지 않는다.** `dangerouslySetInnerHTML` 금지, 마크다운을 쓰더라도 raw HTML 비활성(`rehype-raw` 금지).
- React 는 기본적으로 이스케이프한다. 구멍은 위 둘뿐이고 막는 비용이 거의 0이다.

[명세 §7-7](../api/auth-kakao-v1.md) 이 이 규칙을 최상위 CLAUDE.md §2 절대 규칙으로 올릴 것을 제안한다 — 프론트가 지키지만 어겼을 때 깨지는 것은 인증이라 파트 경계를 넘는다. 동의한다.

⚠️ **앱은 콜드 스타트마다 재로그인이 된다.** `sessionStorage` 는 웹뷰 컨텍스트와 함께 사라지므로, 웹의 "탭을 닫을 때만" 과 성격이 다르다. 카카오 세션이 살아 있으면 `prompt=none` 으로 화면 없이 통과하지만 인앱 브라우저가 한 번 번쩍인다. 하루에 여러 번 여는 앱이라 체감을 측정해야 한다 — §9-2 에 후속으로 올렸다.

---

## 5. 앱 셸 ([`apps/mobile`](../../apps/mobile))

### 5-1. 시작 URL 만 예외로 뺀다

[`src/config.ts`](../../apps/mobile/src/config.ts) 의 `isInternalUrl()` 이 외부 URL 을 전부 시스템 브라우저로 넘기는 게 [#23](https://github.com/kakaotechcampus-4/ktc4-pusan-3/issues/23) 에서 걸린 지점이다. 로그인 시작 URL 하나만 앞에서 걸러낸다.

```ts
// App.tsx — onShouldStartLoadWithRequest
if (isInternalUrl(request.url)) return true;
if (isAuthStartUrl(request.url)) {                 // pathname 이 /auth/<provider> 로 끝나는지
  void WebBrowser.openAuthSessionAsync(request.url, "yukameo://auth")
    .then((r) => { if (r.type === "success") loadCallback(r.url); });
  return false;
}
void Linking.openURL(request.url);                 // 그 외 외부 링크는 기존 동작 그대로
return false;
```

`expo-web-browser` 의 `openAuthSessionAsync` 가 Android = Custom Tabs, iOS = `ASWebAuthenticationSession` 으로 갈라주고 양쪽 다 복귀 URL 을 promise 로 돌려준다. **네이티브 코드를 직접 짜지 않는다** — config plugin 도 필요 없고 CNG 가 처리한다.

`scheme: "yukameo"` 는 [`app.json`](../../apps/mobile/app.json) 에 이미 있다. **이 스킴을 카카오 콘솔에 등록하지 않는다** — `yukameo://auth` 는 우리 서버가 302 하는 대상이지 카카오의 `redirect_uri` 가 아니다. 카카오는 API 오리진만 안다.

### 5-2. 복귀 URL 을 웹뷰에 싣는다

```ts
function loadCallback(url: string) {
  // ⚠️ 커스텀 스킴을 URL 로 파싱하지 않는다 (§7)
  const PREFIX = "yukameo://auth";
  if (!url.startsWith(PREFIX)) return;
  const query = url.slice(PREFIX.length);
  if (query && !query.startsWith("?")) return;      // yukameo://authXXX 는 남이다
  setWebViewUri(`${WEB_URL}/auth/callback${query}`);
}
```

- 돌아갈 오리진은 **지금 웹뷰가 보고 있는 곳**(`WEB_URL`)이다. 개발 빌드에서 프로덕션 주소로 고정하면 로컬 서버가 발급한 코드를 라이브 사이트가 받게 되는데, 코드도 bind 도 그쪽 컨텍스트엔 없어서 반드시 실패한다.
- **웹뷰를 remount 하지 않는다.** `source` 의 uri 만 바꿔 같은 컨텍스트에서 이동시킨다 — `key` 를 바꿔 다시 만들면 `sessionStorage` 의 bind 가 사라진다 (§4-3).
- `Linking` 복귀 경로는 지금 처리하지 않는다. 추가하려면 §4-3 의 재검토 조건을 같이 본다.

### 5-3. Expo Go 로는 확인할 수 없다

`expo-web-browser` 자체는 Expo Go 에서 돌지만 **커스텀 스킴 복귀가 안 온다** — Expo Go 의 딥링크는 `exp://` 다. 카카오 로그인 확인은 `expo-dev-client` 개발 빌드로 한다. 기존 화면 작업은 계속 Expo Go 로 해도 되고, 이 한 경로만 개발 빌드가 필요하다.

---

## 6. 목 서버

실제 OAuth 왕복은 MSW 로 흉내 낼 수 없다 (외부 오리진 전체 페이지 이동이라 서비스 워커가 못 잡는다). 지금 [`lib/auth/kakao.ts`](../../apps/web/src/lib/auth/kakao.ts) 의 `MOCK_ONLY` 가드를 그대로 이어받아 **개발 + 목 서버일 때만** ②~⑥ 을 건너뛰고 자리표시 코드로 ⑦ 부터 태운다.

```
목 켜짐: 버튼 → 이동 대신 /auth/callback?code=dev-placeholder-code 로 push
목 꺼짐: 버튼 → 실제 이동 (서버 미구현이면 OAuthUnavailableError)
```

핸들러가 덮어야 하는 것:

| 경로 | 시나리오 |
| --- | --- |
| `GET /auth/kakao/status` | `ready: true` / **`?scenario=auth_unready`** 로 `ready: false` |
| `POST /auth/kakao` | 기존 회원 응답 / `?scenario=consent` 로 `{ status, consent_code }` |
| `POST /auth/kakao/signup` | 동의 완료 → `is_new: true` · 필수 스코프 누락 → `403 consent_required` |
| `POST /auth/logout` | 204 |

🚨 **자리표시 값은 프로덕션 빌드에 남지 않는다.** 로그인이 되는 것처럼 보이는 경로를 만들지 않는다 ([`apps/web/CLAUDE.md`](../../apps/web/CLAUDE.md) §3).

`?scenario=consent` 는 **신규 가입 경로를 확인하는 유일한 방법**이다 — 실서버로는 매번 새 카카오 계정이 필요하다.

---

## 7. 함정

- 🚨 **커스텀 스킴을 RN 의 `URL` 로 파싱하지 않는다.** RN 의 `URL` 은 브라우저 것이 아니라 부분 폴리필이라 `host`·`hostname`·`pathname`·`origin` 정규식이 `https?:` 로 고정돼 있어 `yukameo://` 는 hostname 이 빈 문자열로 나오고, `hash` 는 setter 자체가 없어 대입하면 TypeError 가 난다. 여기서 조용히 실패하면 **복귀가 무시돼 로그인이 끝나지 않는다.** 문자열 prefix 비교로 처리한다.
- 🚨 **시작과 콜백은 같은 오리진이어야 한다** (§4-2). 로컬에서만 통과하는 종류의 버그다.
- 🚨 **복귀 경로 복원값을 검증한다** (§4-2). 서버가 오픈 리다이렉트를 막았는데 프론트가 다시 열면 의미가 없다.
- 🚨 **응답 분기는 `status` 필드로** (§4-4). `token` 유무로 하면 토큰 없는 세션이 저장된다.
- 🚨 **bind 를 ⑧ 에서 지우지 않는다** (§4-3). 신규 가입이 ⑨ 에서 같은 값을 쓴다.
- 🚨 **취소를 실패로 다루지 않는다.** `oauth_denied` 와 인앱 브라우저 닫기는 로그인 시작 전으로 돌아간 것뿐이다. 에러 배너를 띄우지 말고 버튼만 원상복구한다.
- 🚨 **에러 코드를 화면에 그대로 출력하지 않는다** (§4-4). 문구를 프론트가 만드는 이유가 그거다.
- 콜백 라우트는 [`AuthGate`](../../apps/web/src/components/auth-gate.tsx) 로 감싸지 않는다. 토큰을 **얻으러** 가는 화면이라 감싸면 00 으로 튕긴다.
- 콜백 라우트는 `hydrated` 를 기다린 뒤 교환한다. persist 복구 전에 `signIn()` 을 부르면 복구가 그 위에 덮어쓴다.
- 교환은 **한 번만** 실행한다. React StrictMode 의 이중 실행으로 1회용 코드를 두 번 소비하면 두 번째가 `401 invalid_handoff` 가 되어 로그인이 실패한다 — `useRef` 가드를 둔다.
- 웹뷰 `key` 를 바꿔 remount 하지 않는다 (§5-2).
- 🚨 **`startMocks()` 는 멱등해야 한다.** StrictMode 가 개발 환경에서 effect 를 두 번 돌리는데, 두 번째 `worker.start()` 가 `cannot configure an already enabled network` 로 **거부되면** 호출부의 `.finally()` 가 즉시 실행돼 **워커가 뜨기 전에 화면이 그려진다.** 그러면 첫 화면의 첫 요청이 목을 통과해 실서버로 나가고, 응답이 없어 쿼리가 영구 `pending` 이 된다 — **00 화면의 status prefetch 가 정확히 여기 걸려 로그인 버튼이 계속 비활성이었다.** 약속을 모듈 수준에 캐시해 두 번째 호출이 같은 완료를 기다리게 했다. 첫 화면에서 쿼리를 부르는 화면이 생기기 전까지는 콘솔 에러 1건으로만 보이던 버그다.

---

## 8. 검증

목 서버로 확인한 것 (헤드리스 브라우저 390×844):

- [x] 00 진입에 status prefetch 가 **버튼 누르기 전에** 나감
- [x] 목 켜짐 + 버튼 → 콜백 화면 → `/child/c1/home`
- [x] StrictMode 에서 교환이 1회만 나감
- [x] `?scenario=auth_unready` → 버튼 비활성 + 안내
- [x] `?scenario=consent` → 콜백에서 멈춤 · **토큰 미저장** · `bind` 유지 · `/me` 미호출
- [x] `/auth/callback` 직접 접근(코드 없음) → 00 으로 조용히 되돌아감
- [x] `?error=oauth_denied` → **배너 없이** 00 으로
- [x] `?error=invalid_state` → 만료 문구
- [x] `?error=<모르는 값>` → 기본 문구, **코드 문자열이 화면에 안 나옴**
- [x] 복귀 경로 — 토큰 없이 `/onboarding` 진입 → 00 으로 튕기며 경로 저장 → 로그인 후 `/onboarding` 복원 → 성공 후 `bind`·복귀경로 정리
- [x] 새로고침 후 로그인 유지 · 토큰이 `sessionStorage` 에만 · `expiresAt` 저장 · 새 컨텍스트는 해제
- [x] 프로덕션 빌드 실행 번들(`.js`)에 자리표시 코드 문자열 없음 (`.js.map` 에만 남는다)

- [x] `?scenario=consent` → 동의 화면 → 4건 체크 → signup → `/onboarding` 진입
- [x] 체크박스 4개 · **"전체 동의" 없음** · 하나라도 빠지면 제출 비활성
- [x] `consent_code` 가 URL 이 아니라 `sessionStorage` 에 있음 · 대기표 없이 `/auth/consent` 직접 접근 → 00 으로
- [x] 보낸 요청이 `signup`(계정 2건) → `POST /consents` × 2(아이 2건) 순서

서버가 붙어야 확인 가능한 것에 추가:

- [ ] `consent_code` 만료(10분) 응답 → 00 으로 되돌림
- [ ] `POST /consents` 를 `child_id` 없이 받아 주는지 (§9-2)

서버가 붙어야 확인 가능한 것:

- [ ] 웹 브라우저 전체 왕복
- [ ] 앱(개발 빌드) 전체 왕복 — Android · iOS 각각
- [ ] 인앱 브라우저에서 X → 버튼이 원래대로 돌아옴
- [ ] bind 를 지우고 교환 → `401 invalid_handoff`
- [ ] 앱 콜드 스타트 후 재로그인 체감 (§9-2)

---

## 9. 남은 것

### 9-1. 팀 결정 대기

| 항목 | 프론트에 미치는 영향 | 어디서 |
| --- | --- | --- |
| **동의 전 `parent` 미생성** | 확정되면 §4-4·§4-5 그대로. 뒤집히면 신규도 ⑧ 에서 토큰을 받고 동의 화면은 `POST /consents` 를 쓴다 | [명세 §6-1](../api/auth-kakao-v1.md) · @tomchaccom |
| `session` · `auth_handoff` 테이블 신설 | 없음 | [명세 §5-3·§5-4](../api/auth-kakao-v1.md) |
| `parent.nickname` nullable | 없음 — 프론트 타입이 이미 `string \| null` 이고 화면은 "보호자" 로 폴백한다 | [명세 §5-2](../api/auth-kakao-v1.md) |

### 9-2. 프론트가 서버·팀에 요청한 것

- **복귀 URL 에 `provider` 를 실어주면 좋겠다.** 복귀 경로가 provider 를 안 담아서(`/auth/callback?code=`) 프론트가 시작 시점에 저장해 둬야 하고, 저장이 유실되면 어느 엔드포인트로 교환할지 모른다. 서버는 이미 아는 값이고 열거값이라 오픈 리다이렉트 표면도 아니다. provider 가 카카오 하나뿐인 동안은 저장 방식으로 버틴다.
- 🚨 **아이 스코프를 `child_id` 없이 받아 줄 수 있는지 확인이 필요하다.** 계약서 §04 의 `POST /consents` 예시는 아이 스코프에 `child_id` 를 싣는데, `child_basic` 은 **아이를 만들기 전에** 있어야 한다(`child_basic` 없이 `POST /children` → 403). 즉 가입 시점에는 실을 `child_id` 가 없다. 지금 프론트는 **생략하고 보낸다**. 서버가 거절하기로 하면 대안은 ㉠ `signup` 의 `consents` 에 4건을 다 받거나 ㉡ 01 화면에서 아이를 만든 뒤 받는 것인데, ㉡은 "동의는 저장보다 먼저다" 와 부딪힌다.
- **`guardian_attested` 를 별도 체크박스로 받아야 하는지 확인이 필요하다.** 지금은 아이 스코프에 `true` 로 함께 싣는다. 법정대리인 확인을 별도 문항으로 받아야 하면 항목이 5건이 된다.
- **`client` 허용값 밖일 때의 응답을 하나로 정해달라.** 명세 §2-3·§3-2 는 `400`, 리뷰 코멘트는 `?error=invalid_client` 리다이렉트였다. 프론트가 잘못된 값을 보낼 일은 없지만, 400 이면 사용자가 JSON 에러 페이지에 남고 리다이렉트면 앱으로 돌아온다.
- **앱 콜드 스타트 재로그인 체감을 `M-02` 에 넣어야 한다** (§4-7). 웹의 "탭 닫을 때" 와 성격이 다르다 — 앱은 열 때마다 인앱 브라우저가 한 번 번쩍인다. 12시간 조정 논의에 앱 케이스를 같이 올린다.
- **"외부 텍스트와 LLM 출력을 HTML 로 렌더링하지 않는다"** 를 최상위 CLAUDE.md §2 절대 규칙으로 올리는 데 동의한다 ([명세 §7-7](../api/auth-kakao-v1.md)).

### 9-3. 별도 이슈로 뺄 것

- **10 설정의 동의 관리** — 현황·철회 화면. `lib/consent.ts` 의 스코프 목록을 같이 쓰고, 가입 화면과 같은 컴포넌트를 쓸지는 그 이슈에서 정한다
- apple · google · naver — 서버가 같은 틀이라 `startOAuthLogin(provider)` 에 값만 추가된다. 버튼·약관 문구는 화면 이슈
- 셸의 `Linking` 복귀 경로 (§4-3 재검토 조건)
