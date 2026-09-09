# 카카오 OAuth 로그인 v1

문서 목적: 카카오 소셜 로그인의 요청·응답 계약, 세션 규칙, 웹·앱의 진입 방식, 동의 게이트와의 순서를 고정한다.

기준 브랜치: `docs/be-18-auth-kakao`
작성일: 2026-09-09
담당: 이도헌
선행 문서: [`docs/api/api-interface-v1.html`](./api-interface-v1.html) §01 공통 규약 · §04 인증·동의 — 그 문서의 `POST /auth/{provider}` 를 카카오 기준으로 구체화한다

---

## 0. 30초 요약

**계약서 §01 과 엔드포인트 모양을 유지한다.** 요청 바디만 바뀐다.

| 정한 것 | 값 |
| --- | --- |
| 카카오 인증 | **인가 코드.** 프론트가 `code` 를 받아 서버에 넘기고, 서버가 `client_secret` 으로 교환한다 |
| 왜 SDK 토큰이 아닌가 | **카카오 JS SDK 에 클라이언트가 access_token 을 받는 메서드가 없다** (§2-1) |
| 요청 바디 | `{ "code": "..." }` — 웹·앱 **동일** |
| 앱에서 여는 방법 | **시스템 인증 세션** (`openAuthSessionAsync`). 웹뷰에 가두지 않는다 (§4-5) |
| 세션 토큰 | **불투명 난수** 256비트. DB 에는 SHA-256 해시만 저장 |
| 전달 | `Authorization: Bearer` — **계약서 §01 그대로** |
| 수명 | 12시간, refresh 토큰 없음 |
| 클라이언트 저장 | `sessionStorage`. **`localStorage` 금지** (§4-4) |
| 신규 테이블 | `session` 1개 (§5-3 협의 대상) |

**계약서 변경 4건** — 요청 바디 `{access_token}` → `{code}`, `expires_in` 추가, `POST /auth/logout` 신설, 에러 2건. **§01 공통 규약은 손대지 않는다** (§12).

### 0-1. 결정 넷 — 성격이 다르다

| # | 무엇 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | 카카오 인증을 어떻게 받나 | **고정 — 인가 코드** | 선택이 아니라 제약. JS SDK 에 클라이언트가 토큰을 받는 메서드가 없다 (§2-1) |
| 2 | 콜백을 어디서 받나 | **프론트** | 서버가 JSON 을 돌려줄 수 있어 계약서 모양이 유지된다 (§2-2) |
| 3 | 세션 토큰 형식 | **사실상 결정 — 불투명 난수** | 로그아웃·탈퇴·아이 파기 3곳이 즉시 무효화를 요구해 JWT 가 탈락한다 (§4-2) |
| 4 | 세션 전달 | **헤더 (Bearer)** | 계약서 §01 이고, `client.ts`·`sse.ts` 가 이미 Bearer 전제로 구현돼 있다 (§4-1) |

> **2번을 백엔드로 두면 3·4번이 따라 바뀐다.** 콜백이 백엔드면 응답이 리다이렉트라 JSON 을 실을 수 없어 세션을 쿠키로 내려야 하고, CSRF 방어와 "웹·API 같은 사이트 배포" 제약이 함께 붙는다. 게다가 `sse.ts` 가 `EventSource` 를 쓰지 않고 fetch 스트리밍 + Authorization 헤더로 구현된 이유(`apps/web/CLAUDE.md` §3)와도 어긋난다. 지금 안은 그 전부를 피한다.

---

## 1. 범위

**다루는 것** — 로그인·로그아웃 계약, 웹·앱의 진입 방식, `auth_identity` 생성 규칙, 세션 토큰 형식과 수명, 계정 스코프 동의 게이트와의 순서.

**다루지 않는 것**

| 무엇 | 어디로 |
| --- | --- |
| 동의 문구·개인정보 처리방침·이용약관 | `safety/` — NF-04 확정이 선행 조건 |
| 아이 스코프 권한 (`requireConnected` · `requireOwner` · `requireWriter`) | 노션 「테크스팩 보완 제안 — 보호자 권한과 동의」 §2-1 |
| 보호자 초대·연결 | 같은 문서 §2-3 |
| 계정 탈퇴·파기 배치 | 같은 문서 §2-5. 파기 시 카카오 연결 끊기만 §7-2 에 남긴다 |

**1차 배포는 카카오만.** `auth_identity.provider` enum 4종은 유지하되 카카오 외에는 구현하지 않는다 (노션 논의 ⑬).

### 1-1. 🔶 이번 검토에서 새로 발견된 것 — 별도 이슈가 필요하다

인증(누구인가)이 아니라 **권한(무엇을 할 수 있나)** 영역이라 이 문서에서 다루지 않는다. 다만 **어느 문서에도 없던 것**이라 기록해 잃어버리지 않게 한다.

| # | 무엇 | 왜 구멍인가 |
| --- | --- | --- |
| 1 | **간접 식별자에서 소유 아이를 역추적해 검사한다** — `run_id` · `memory_id` · `suggestion_id` 만 받는 API | 노션 권한표는 `/children/{cid}/*` 만 다룬다. 계약서에는 `GET /runs/{rid}/events` 처럼 child_id 가 경로에 없는 엔드포인트가 있다 |
| 2 | **AI 도구의 아이 범위는 서버가 주입한다.** 모델이 넘긴 child_id 를 믿지 않는다 | Agent 가 `memory.search` 를 직접 부르는 구조에서 도구 인자를 신뢰하면 **프롬프트 인젝션이 곧 권한 상승**이 된다 |
| 3 | **동의 철회 시 실행 중 작업을 외부 전달·저장 직전에 재검사한다** | NF-06 부분 결과 구조에서 시작 시점 검사만으로는 부족하다 |
| 4 | **SSE 재연결이 새 run 을 만들지 않는다.** 이벤트 ID 로 재개 커서를 잡는다 | 갱신 후 스트림을 다시 열 때 AI 실행이 중복되면 비용과 기록이 이중으로 쌓인다 |

---

## 2. 카카오 인증

### 2-1. SDK 토큰 전달은 웹에서 구현되지 않는다

계약서 §04 는 `POST /auth/{provider}` 요청을 `{"access_token": "..."}` 로 정의한다. **웹 클라이언트가 그 값을 가질 방법이 없다.**

카카오 JavaScript SDK 의 `Kakao.Auth` 가 제공하는 메서드는 다음이 전부다.

| 메서드 | 하는 일 |
| --- | --- |
| `authorize(settings)` | **인가 코드를 등록된 redirect URI 로 보낸다.** 토큰을 주지 않는다 |
| `setAccessToken(token)` | 이미 발급된 토큰을 SDK 에 넣는다 |
| `getAccessToken()` | 저장된 토큰을 읽는다 |
| `getStatusInfo()` · `logout()` · `cleanup()` · `getAppKey()` · `selectShippingAddress()` | — |

**클라이언트가 토큰을 발급받는 메서드가 존재하지 않는다.** SDK v1 의 `Kakao.Auth.login()` 은 현재 레퍼런스에 없다. `setAccessToken()` 이 따로 있다는 것 자체가 토큰이 서버에서 내려온다는 뜻이다.

> **네이티브 SDK 는 토큰을 직접 준다** (`loginWithKakaoTalk`). 하지만 §4-5 의 구조에서는 네이티브 SDK 를 쓰지 않으므로 이 문서 범위 밖이다. 도입하게 되면 `{access_token}` 입력과 `app_id` 대조가 함께 필요해진다 (§10-3).

### 2-2. 콜백은 프론트가 받는다

`redirect_uri` 를 **프론트 경로**로 등록한다. 카카오가 프론트로 `?code=...` 를 붙여 보내면, 프론트가 그 code 를 서버에 POST 한다.

```
① FE   authUrl 생성 (client_id · redirect_uri · response_type=code · state)
        ↓
②      웹  → 브라우저가 그대로 이동
        앱  → 네이티브가 시스템 인증 세션으로 연다 (§4-5)
        ↓
③ 사용자  카카오 로그인 (세션이 살아 있으면 화면 없이 통과)
        ↓
④      프론트 콜백 ?code=...&state=...
        ↓
⑤ FE   state 를 자기가 만든 값과 대조 (§7-1). 불일치면 중단
        ↓
⑥ FE → POST /api/v1/auth/kakao  { "code": "..." }
        ↓
⑦ 서버 → POST https://kauth.kakao.com/oauth/token
          grant_type=authorization_code&client_id=...&client_secret=...
          &redirect_uri=...&code=...
        ← { access_token, ... }
        ↓
⑧ 서버 → GET https://kapi.kakao.com/v1/user/access_token_info
        ← { id, expires_in, app_id }          회원번호만 쓴다
        ↓
⑨ 서버   auth_identity 에서 (kakao, id) 조회 → 없으면 parent + auth_identity 생성
        ↓
⑩ 서버   카카오 토큰 폐기 (§7-2) · session 행 생성
        ↓
⑪ FE ←  200 { token, expires_in, is_new, parent, consent_required }
```

**②를 빼면 웹과 앱이 완전히 같다.** 화면 코드는 한 벌이고 분기는 "지금 웹뷰 안인가" 한 곳뿐이다.

**콜백을 백엔드로 두지 않은 이유** — 백엔드가 받으면 응답이 브라우저 리다이렉트가 되어 JSON 을 실을 수 없다. 세션을 쿠키로 내려야 하고, 그러면 CSRF 이중 제출과 "웹·API 같은 사이트 배포" 제약이 붙는다. 프론트가 받으면 **계약서의 `POST /auth/{provider}` 모양과 Bearer 규약이 그대로 유지된다.**

**⑧ 을 `/v2/user/me` 가 아니라 `access_token_info` 로 하는 이유** — 회원번호 하나만 필요하다. `/v2/user/me` 는 `kakao_account`(이메일·프로필)까지 실어 오는데 NF-04 는 최소 수집을 요구한다. 받아놓고 안 쓰는 것보다 **애초에 안 받는 것**이 낫다 — 받는 순간 `privacy_account` 동의 문구에 그 항목을 적어야 한다.

---

## 3. 엔드포인트

경로·헤더·시간 표기는 [계약서 §01](./api-interface-v1.html)을 따른다. `POST /auth/{provider}` 는 인증 헤더 규칙의 **유일한 예외**다 — 로그인 전에는 세션이 없다.

### 3-1. `POST /auth/{provider}` — 계약서에 있음, 바디와 응답만 보강

`provider` ∈ `kakao` · `apple` · `google` · `naver`. **구현은 `kakao` 만.** 그 외는 `422 validation_failed`.

**요청**

```json
{
  "code": "V1a2bC3d..."
}
```

계약서의 `{access_token}` 을 `{code}` 로 바꾼다 (§2-1). **웹·앱이 같은 바디를 보낸다.**

**응답 200**

```json
{
  "token": "Ky8vN2pRt7...",
  "expires_in": 43200,
  "is_new": true,
  "parent": {
    "id": "p1",
    "nickname": null
  },
  "consent_required": ["service_terms", "privacy_account"]
}
```

| 필드 | 뜻 |
| --- | --- |
| `token` | 세션 토큰. **불투명 난수** (§4-2). 이후 `Authorization: Bearer` 로 보낸다 |
| `expires_in` | 초. **계약서에 없어서 추가한다** — 없으면 FE 가 만료를 미리 알 수 없어 매번 401 을 맞고 나서야 재로그인한다 |
| `is_new` | 이번 호출에서 `parent` 가 생성됐는가. FE 는 이 값으로 온보딩 분기를 태운다 |
| `parent.nickname` | **신규는 항상 `null`.** 카카오에서 가져오지 않는다 (§4-6) |
| `consent_required` | 계정 스코프 중 아직 `granted` 가 아닌 것. **비어 있지 않으면 FE 는 동의 화면 외 어디로도 갈 수 없다** |

`Idempotency-Key` 는 **받지 않는다.** 인가 코드는 1회용이라 재사용하면 카카오가 거절하고, 같은 회원번호로 두 번 불려도 `UNIQUE (provider, provider_user_id)` 가 두 번째 생성을 막는다.

세션이 만료됐을 때 FE 가 다시 부르는 것도 이 엔드포인트다. **별도의 재발급 엔드포인트를 만들지 않는다** (§4-3).

**응답에 `Cache-Control: no-store` 를 붙인다.** 토큰이 바디에 있다.

### 3-2. `POST /auth/logout` — 🔶 신설

요청 바디 없음. `Authorization` 헤더로 세션을 식별한다. **응답 204.**

`session` 행을 **삭제**한다. 그 순간부터 그 토큰으로는 아무것도 못 한다.

**계약서 28개 목록에 이것이 없다.** 없으면 클라이언트가 토큰을 버리는 것으로 흉내낼 뿐이고, 서버에서는 그 토큰이 만료까지 계속 유효하다. 즉시 무효화(§4-2)를 쓰려면 반드시 있어야 한다.

카카오 쪽 로그아웃(`POST /v1/user/logout`)은 **부르지 않는다.** 우리 서비스에서 나가는 것과 카카오 계정에서 로그아웃하는 것은 다른 행위다.

---

## 4. 세션

### 4-1. 계약서 §01 과 기존 프론트 코드를 그대로 쓴다

> 인증 `Authorization: Bearer <token>` — 예외 없음. 인증 없는 엔드포인트를 만들지 않는다 (NF-09)

이 줄은 **커버리지 규칙**이다("모든 엔드포인트가 인증을 요구한다"). 이 문서가 `token` 을 불투명 문자열로 정의하는 것으로 그대로 성립한다. **§01 을 고치지 않는다.**

그리고 프론트가 이미 이 전제로 구현돼 있다 — `apps/web/src/lib/api/client.ts` 가 Bearer 를 붙이고, `sse.ts` 는 **`EventSource` 를 쓰지 않고** fetch 스트리밍으로 돌린다. `apps/web/CLAUDE.md` §3 이 이유를 적어뒀다:

> `EventSource` 를 쓰지 않는다. Authorization 헤더를 못 붙여서 NF-09 를 깬다.

쿠키로 옮기면 `client.ts` 와 `sse.ts` 를 둘 다 고쳐야 한다. **헤더 유지가 기존 코드와 맞는다.**

### 4-2. 왜 JWT 가 아닌가

계약서는 `token` 이 JWT 인지 불투명 문자열인지 적지 않았다. **불투명으로 정한다.**

이 서비스는 **즉시 무효화**를 세 곳에서 요구한다 — 로그아웃, 계정 탈퇴(`parent.deleted_at`), 아이 파기(`410 child_deleted`). stateless JWT 는 서명이 유효한 동안 서버가 거절할 방법이 없어서, "탈퇴했는데 만료까지는 여전히 접근된다" 같은 창이 생긴다. 아동 정보를 다루면서 그 창을 만들 이유가 없다.

DB 조회 1회를 아끼는 것이 JWT 의 이점이지만, 세션 조회는 PK 인덱스 단건 읽기라 실질 비용이 거의 없다. **무효화를 포기할 만한 대가가 아니다.**

JWT 의 다른 대표 이점(서비스 간 stateless 전달)도 이 프로젝트에는 해당되지 않는다 — CLAUDE.md §6 이 백엔드와 AI 를 **한 서비스로 묶기로** 정했고, Agent 는 같은 프로세스의 `app/agents/` 폴더다. **토큰을 건네줄 상대가 없다.**

부수 효과로 관리할 것이 줄어든다: 서명 키가 없고, 디코딩 가능한 payload 가 없어 클레임에 무엇을 넣을지 고민할 일도 없다.

### 4-3. 토큰과 수명

| | 값 |
| --- | --- |
| 생성 | CSPRNG 256비트 → base64url (43자) |
| DB 저장 | **SHA-256 해시.** 원문은 저장하지 않는다 (§7-2) |
| 수명 | **12시간** (`expires_in: 43200`) |
| 만료 시 | `401 unauthenticated` → FE 가 §2-2 를 다시 탄다 |

**refresh 토큰을 만들지 않는다.** 카카오 세션이 살아 있으면 authorize 가 화면 없이 code 를 돌려준다 — 카카오는 이를 위해 `prompt=none` 을 제공한다(세션이 있으면 즉시 코드 발급, 없으면 `consent_required` 에러). **재로그인 자체가 조용하다.**

> ⚠️ **숨긴 iframe 으로 갱신하는 방식은 쓸 수 없다.** 브라우저의 서드파티 쿠키 차단(Safari ITP, Chrome) 때문에 iframe 안에서 카카오 쿠키가 실리지 않는다. 웹은 전체 페이지 리다이렉트, 앱은 시스템 인증 세션이 뜬다. 체감은 `M-03` 으로 확인하고 12시간을 조정한다.

### 4-4. 클라이언트 저장 위치

| 위치 | 판정 |
| --- | --- |
| **`sessionStorage`** | ✅ 권장. 새로고침을 견디고, 탭을 닫으면 사라져 표면이 작다 |
| 메모리 (모듈 변수) | XSS 에는 가장 안전하지만 **새로고침마다 재로그인**이 걸린다 |
| `localStorage` | ✕ **금지.** XSS 한 건이 12시간짜리 토큰을 그대로 넘긴다 |

**🚨 현재 코드가 이 규칙을 어기고 있다.** [`apps/web/src/stores/session.ts`](../../apps/web/src/stores/session.ts) 가 Zustand persist 로 토큰을 **localStorage** 에 영속 저장한다.

```ts
storage: createJSONStorage(() => localStorage),
partialize: (s) => ({ token: s.token, activeChildId: s.activeChildId }),   // ← token
```

`createJSONStorage(() => sessionStorage)` 로 바꾸면 된다. `activeChildId` 는 계속 저장해도 된다 — 화면 선택값일 뿐 권한 근거가 아니고(§1-1), `/me` 결과로 유효성을 다시 확인한다.

### 4-5. 앱에서 로그인을 여는 방법 — 시스템 인증 세션

**앱은 React Native(Expo) 웹뷰 셸이다.** 화면·상태·API 호출은 전부 `apps/web` 에 있고, 셸은 웹뷰를 띄우는 일만 한다.

`apps/mobile/CLAUDE.md` §4 가 규칙을 못박고 있다.

> 허용 출처 밖은 웹뷰에 가두지 않는다. (…) **소셜 로그인**·약관 페이지가 웹뷰 안에서 열리면 사용자가 **주소창을 못 봐서 피싱과 구분할 수 없다.**

그래서 **카카오 로그인 페이지를 웹뷰 허용 목록에 넣지 않는다.** 대신 **시스템 인증 세션**을 쓴다 — iOS 는 `ASWebAuthenticationSession`, Android 는 Custom Tabs 다. **주소창이 보이므로 위 우려가 해소되고**, 앱의 등록된 스킴으로 리다이렉트되면 창이 자동으로 닫히며 URL 을 앱에 돌려준다. RFC 8252 가 네이티브 앱에 권장하는 방식이다.

```
① 웹뷰의 웹에서 "카카오로 로그인"
     window.ReactNativeWebView.postMessage(JSON.stringify({ type: "oauth", url: authUrl }))
        ↓
② 셸이 WebBrowser.openAuthSessionAsync(authUrl, "yukameo://oauth")
        ↓
③ 시스템 인증 세션에서 카카오 로그인
     ★ 주소창 보임 · 카카오톡 전환도 OS 가 처리
        ↓
④ 카카오 → 프론트 콜백 → yukameo://oauth?code=...&state=...
        ↓
⑤ 창 자동 닫힘. URL 이 셸로 반환
        ↓
⑥ 셸이 code·state 를 웹뷰에 injectJavaScript 로 전달 (값만)
        ↓
⑦ 웹이 §2-2 의 ⑤~⑪ 을 그대로 탄다
```

**셸이 지키는 것** — `apps/mobile/CLAUDE.md` §1 의 "API 를 부르지 않는다 · 토큰을 들고 있지 않다 · 네이티브 기능은 브릿지로 값만 넘기고 UI 는 웹에 둔다" 를 그대로 따른다. 셸이 만지는 건 **1회용 인가 코드**이지 토큰이 아니고, `POST /auth/kakao` 는 웹이 부른다.

**웹뷰인지 아는 방법** — `App.tsx` 가 이미 `applicationNameForUserAgent={"YukameoApp/<version>"}` 를 붙인다. 웹이 UA 로 분기하면 된다.

| 필요한 것 | 값 · 비고 |
| --- | --- |
| 앱 스킴 | `yukameo` (`app.json` 에 이미 있음) → `yukameo://oauth` |
| 패키지 | `expo-web-browser` — **아직 의존성에 없다.** `pnpm exec expo install expo-web-browser` (SDK 57 에 맞는 버전을 고르게 하려면 `pnpm add` 를 쓰지 않는다) |
| Redirect URI | **`https://<도메인>/oauth/callback` 하나만 콘솔에 등록**한다. 콜백 페이지가 `state` 에 담긴 표시를 보고 앱에서 온 것이면 `yukameo://oauth?code=...` 로 이동시킨다 |

> **왜 스킴을 콘솔에 직접 등록하지 않는가** — 카카오 콘솔이 커스텀 스킴 Redirect URI 를 받는지 확인하지 못했다. https 하나만 등록하고 콜백 페이지가 중계하면 그 질문을 피할 수 있고, 운영·로컬 등록도 하나로 끝난다. **콘솔이 스킴을 받아준다면 중계 페이지를 빼도 된다** (§10-4).

**이 구조에서는 네이티브 카카오 SDK 가 필요 없다.** 카카오톡 전환은 시스템 인증 세션 안에서 OS 가 처리하고, 셸이 받는 것은 코드뿐이다. 따라서 앱↔웹뷰 토큰 전달(handoff) 설계도, `{access_token}` 입력도 만들지 않는다.

### 4-6. 보호자 표시 이름

`parent.nickname` 은 **온보딩에서 보호자가 직접 입력한다.** 카카오 프로필에서 가져오지 않는다.

가져오면 카카오 동의 화면에 항목이 하나 늘고, `privacy_account` 동의 문구에 "카카오 프로필 닉네임"을 적어야 한다. 표시 이름은 보호자 목록·owner 이관 화면·기록 작성자 표시에만 쓰이므로 직접 받는 편이 값도 정확하다.

→ 노션 논의 사항 ⑫ 는 이 결정으로 닫힌다.

---

## 5. 스키마

### 5-1. `auth_identity` — 노션 확정안 그대로

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK |
| `parent_id` | uuid | FK → `parent.id`, NOT NULL |
| `provider` | enum | `kakao / apple / google / naver`, NOT NULL |
| `provider_user_id` | text | 카카오 회원번호. NOT NULL |
| `linked_at` | timestamptz | NOT NULL, default now() |

`UNIQUE (provider, provider_user_id)`

카카오 회원번호는 Long 이지만 **text 로 저장한다.** Apple 은 문자열 식별자를 주므로 제공자가 늘 때 컬럼 타입이 바뀌면 안 된다.

**계정 자동 통합 없음.** 이메일을 받지도 않을뿐더러 바뀌는 값이라 식별자로 쓸 수 없다.

> ✅ **이미 구현돼 있다.** PR #11 (현재 `feat/be-7-db-setting-and-migration` 브랜치) 의 `identity/models.py` 에 위 표와 **필드·제약이 정확히 일치**하는 `AuthIdentity` 가 있다.

### 5-2. 🚨 `parent.nickname` 충돌

같은 브랜치의 `Parent.nickname` 이 **`nullable=False`** 다. **NOT NULL 이면 로그인 시점에 `parent` 행을 만들 수 없다.**

`consent.parent_id` 가 NOT NULL 이고 최초 로그인 직후 `service_terms`·`privacy_account` 를 받아야 한다 (§6-1). 동의를 기록하려면 `parent` 가 **이미 있어야** 하므로 온보딩까지 미룰 수 없다. 그런데 표시 이름은 카카오에서 가져오지 않기로 했으므로 (§4-6) 채울 값이 없다.

| 안 | 판정 |
| --- | --- |
| **`nickname` 을 nullable 로** | ✅ 온보딩에서 채운다. "아직 입력 안 함"이 `NULL` 로 표현된다 |
| 빈 문자열 | ✕ "온보딩 안 함"과 "빈 이름이 잘못 저장됨"을 구분할 수 없고, `length > 0` CHECK 제약도 걸 수 없다 |
| 카카오 닉네임 사용 | ✕ 논의 ⑫ 를 뒤집고 동의 항목이 는다 |

→ **PR #8 이 develop 에 머지되기 전이면 모델과 마이그레이션을 같이 고치면 되고 새 리비전이 안 붙는다.** ([PR #8 코멘트](https://github.com/kakaotechcampus-4/ktc4-pusan-3/pull/8#pullrequestreview-5148979830))

### 5-3. `session` — 신설 🔶 협의 대상

노션 「보호자 권한과 동의」의 신설 테이블 목록에 없다. **김명성님 확정 대상**이다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK |
| `parent_id` | uuid | FK → `parent.id`, NOT NULL |
| `token_hash` | bytea | **UNIQUE, NOT NULL.** SHA-256(토큰 원문) |
| `expires_at` | timestamptz | NOT NULL |
| `created_at` | timestamptz | NOT NULL, default now() |

```sql
CREATE UNIQUE INDEX ON session (token_hash);
CREATE INDEX ON session (parent_id);
CREATE INDEX ON session (expires_at);
```

**폐기는 행 삭제로 한다.** `revoked_at` 을 두지 않는 이유 — 세션은 증빙이 아니다. `consent` 가 append-only 인 것과 정반대로, 남은 행은 유출 표면일 뿐이다.

| 언제 | 무엇을 지우나 |
| --- | --- |
| 로그아웃 | 그 행 1건 |
| 계정 탈퇴 · 아이 파기 | `parent_id` 로 전부 |
| 만료 | 배치로 `expires_at < now()` 전부 |

수명이 12시간이라 행이 오래 쌓이지 않는다. 아이 파기 대상 목록(NF-14)에 `auth_identity` 와 함께 포함된다.

### 5-4. `state` 는 테이블도 서버 저장도 필요 없다

콜백을 프론트가 받으므로 **`state` 검증도 프론트가 한다** (§7-1). 서버는 `code` 만 받는다. 앱 경로에서는 `state` 에 "앱에서 시작함" 표시를 함께 담아 콜백 페이지가 스킴으로 중계할지 판단한다 (§4-5).

별도 테이블을 두면 만료 정리 배치가 하나 더 생기고 로그인 시도마다 쓰기가 발생한다. **6명 10주 규모에서 얻는 것이 없다.**

---

## 6. 동의 게이트와 미들웨어 순서

### 6-1. 로그인은 동의보다 먼저다

동의를 받으려면 누가 동의하는지 알아야 하고, 그러려면 `parent` 행이 먼저 있어야 한다. 그래서 **로그인 자체는 동의 없이 성공한다.** 대신 응답의 `consent_required` 가 비어 있지 않으면 FE 는 동의 화면 외 어디로도 가지 못하고, 서버는 `/auth/*` 를 제외한 모든 엔드포인트를 미들웨어에서 막는다.

프론트 차단만 믿지 않는다 — 화면을 우회해 API 를 직접 부르면 그만이다. **NF-10 은 "동의 없는 아동정보 저장 경로가 코드에 존재하지 않는다"이고, 그 강제 지점은 API 계층이다.**

### 6-2. 순서 (NF-11)

```
① authenticate            Bearer 토큰 → SHA-256 → session 조회 → parent_id. 만료·없음이면 거절
② requireAccountConsent   service_terms · privacy_account 최신 행이 granted 인가
③ requireConnected / requireOwner / requireWriter    아이 스코프
④ requireChildConsent(scope)   child_basic · child_health
```

| 경로 | 통과해야 하는 것 |
| --- | --- |
| `POST /auth/{provider}` | 없음 |
| `POST /auth/logout` | ① |
| `GET /me` · `POST /consents` · `GET /consents` | ① |
| 그 외 전부 | ① ② + (아이 스코프면) ③ ④ |

`GET /me` 와 `/consents` 가 ② 를 건너뛰는 이유 — 동의 화면 자체가 이 둘을 부른다.

핸들러가 직접 권한 쿼리를 쓰지 않는다. 미들웨어 한 곳에서만 판정한다.

**CSRF 방어는 필요 없다.** 쿠키를 쓰지 않으므로 브라우저가 자동으로 자격증명을 붙이지 않는다.

---

## 7. 보안 규칙

### 7-1. `state` 검증 — 프론트가 한다

프론트가 authorize 를 부를 때 난수 `state` 를 만들어 `sessionStorage` 에 두고, 콜백에서 쿼리의 `state` 와 대조한다. **불일치·누락이면 서버에 code 를 보내지 않는다.**

빼면 공격자가 **자기 카카오 계정의 인가 코드**로 만든 콜백 URL 을 피해자에게 열게 해서 피해자 브라우저를 공격자 계정으로 로그인시킬 수 있다. 그 뒤 피해자가 입력한 아이 정보가 공격자 계정에 쌓인다.

**서버는 `state` 를 보지 않는다.** 프론트가 자기가 만든 값과 대조하는 것으로 충분하고, 서버가 관여하면 저장소가 하나 더 필요해진다 (§5-4).

앱 경로에서는 시스템 인증 세션이 반환한 URL 의 `state` 를 **웹이** 대조한다 — 셸은 값을 옮기기만 한다 (§4-5).

### 7-2. 저장하지 않는 것

| 무엇 | 왜 |
| --- | --- |
| 카카오 access_token · refresh_token | 교환 직후 회원번호만 얻고 폐기한다. 카카오 API 를 더 부르지 않으므로 보관하면 유출 표면만 는다 |
| 이메일 · 프로필 이미지 · 닉네임 | NF-04 최소 수집. `access_token_info` 는 주지도 않는다 |
| **세션 토큰 원문** | 해시만 저장한다 (§4-3) |
| 인가 코드 | 1회용이고 교환 즉시 무효다 |

**예외 — 파기 배치.** 아이·계정 파기 시 카카오 연결을 끊어야 하는데 토큰이 없으므로 어드민 키로 대상을 지정한다.

```
POST https://kapi.kakao.com/v1/user/unlink
Authorization: KakaoAK {SERVICE_APP_ADMIN_KEY}
Content-Type: application/x-www-form-urlencoded;charset=utf-8

target_id_type=user_id&target_id={provider_user_id}
```

어드민 키는 앱 전체 권한이다. **서버 .env 에만 두고 파기 배치 외 어떤 경로에서도 부르지 않는다.**

### 7-3. 로그

로그에 **카카오 회원번호·인가 코드·카카오 토큰·세션 토큰을 남기지 않는다.** 식별이 필요하면 `parent_id` 만 쓴다. NF-05 의 "원문 대신 `memory_id`" 를 인증 영역으로 확장한 것이다.

카카오 호출 실패를 로깅할 때 응답 본문을 통째로 찍으면 토큰이 섞여 들어간다. 상태 코드와 카카오 에러 코드만 남긴다.

### 7-4. 비밀 관리 (NF-09)

| 값 | 어디에 | 클라이언트 번들 |
| --- | --- | --- |
| `KAKAO_REST_API_KEY` | 서버 .env | ✕ |
| `KAKAO_CLIENT_SECRET` | 서버 .env | ✕ |
| `KAKAO_ADMIN_KEY` | 서버 .env | ✕ |
| JavaScript 키 | 프론트 | ○ — 공개 전제로 설계된 값 |

불투명 토큰에는 **서명 키가 없다.**

`apps/mobile/CLAUDE.md` §4 가 못박은 대로 **셸이 아는 비밀은 없어야 정상이다** — `EXPO_PUBLIC_*` 는 앱 번들에 그대로 들어간다. 이 설계에서 셸은 authUrl 을 웹에서 받아 열기만 하므로 키를 알 필요가 없다.

🚨 **이 저장소는 public 이다** (CLAUDE.md §9). 1단계에서 학생 API 토큰 8건이 실제로 유출됐고, 노트북·`docs/*.md` 본문에서도 나왔다. 이 문서를 포함해 **어떤 문서에도 실제 키를 붙여넣지 않는다** — 특히 §11 환경변수 표는 값을 채우고 싶어지는 자리다. 한 번 커밋된 비밀은 지워도 히스토리에 남으므로 **유일한 조치는 폐기(rotate)** 이고, 실수했다면 즉시 담임 매니저에게 알린다.

### 7-5. XSS 방어 — 헤더 방식을 고른 대가

토큰이 JS 에서 접근 가능하므로 **XSS 한 건이 세션을 넘긴다.**

이 서비스의 XSS 는 막연한 위험이 아니라 **경로가 특정된다** — 기관 공지 붙여넣기·OCR 로 들어온 **외부 텍스트가 LLM 을 거쳐 화면에 렌더링**된다 (F-07 · F-14).

**레버리지 순서대로** 지킨다.

| # | 무엇 | 왜 이 순서인가 |
| --- | --- | --- |
| 1 | **LLM 출력과 공지 원문을 HTML 로 렌더링하지 않는다.** `dangerouslySetInnerHTML` 금지, 마크다운은 raw HTML 비활성 (`rehype-raw` 금지) | React 는 기본적으로 이스케이프한다. 구멍은 이 둘뿐이고 막는 비용이 거의 0이다 |
| 2 | **토큰을 `localStorage` 에 두지 않는다** (§4-4) | 저장소를 훑는 가장 흔한 공격을 무력화한다 |
| 3 | **세션 수명 12시간** | 털려도 유효 기간이 그만큼이다 |
| 4 | **CSP** — `Report-Only` 로 위반을 모은 뒤 강제로 전환 | XSS 가 나도 `connect-src` 로 외부 유출을 막는다. Next.js 는 인라인 스크립트를 쓰므로 처음부터 강제하면 화면이 깨진다 |
| 5 | **웹뷰 허용 출처를 넓히지 않는다** (§4-5) | 카카오 로그인을 웹뷰에 넣지 않는 것이 `apps/mobile/CLAUDE.md` §4 이고, XSS 관점에서도 같은 방향이다 |

> 1번은 **파트 경계를 넘는 규칙**이다. 프론트가 지키지만 어겼을 때 깨지는 것은 인증이다. CLAUDE.md §2 절대 규칙에 한 줄로 올리는 것을 제안한다 — *"외부 텍스트와 LLM 출력을 HTML 로 렌더링하지 않는다."*

---

## 8. 에러

계약서 §01 의 에러 형식을 그대로 쓴다. 아래 2개는 이 문서에서 **신설**한다.

| HTTP | code | 언제 | 신설 |
| --- | --- | --- | --- |
| 400 | `validation_failed` | `code` 누락 | |
| 401 | `unauthenticated` | 세션 토큰 없음·만료·이미 삭제됨 | |
| 401 | `invalid_provider_token` | 인가 코드 교환 실패 — 만료·재사용·`redirect_uri` 불일치 | 🆕 |
| 403 | `consent_required` | 계정 스코프 미동의 | |
| 404 | `not_found` | `deleted_at` 이 찍힌 parent (→ §10-1) | |
| 422 | `validation_failed` | `provider` 가 enum 밖 | |
| 502 | `oauth_provider_error` | 카카오 API 5xx·타임아웃 | 🆕 |

`invalid_provider_token` 은 **실패 사유를 구분하지 않는다.** 초대 코드 실패를 `404 invalid_invite` 로 통일한 것과 같은 이유다.

`401 unauthenticated` 를 받은 FE 는 **재로그인 경로로 간다** (§4-3). `invalid_provider_token` 을 받으면 그 경로도 실패한 것이므로 실제 로그인 화면으로 보낸다.

`502 oauth_provider_error` 는 **기본값으로 대체하지 않는다.** 카카오가 죽었을 때 로그인을 통과시키는 경로는 존재하지 않는다 — `503 llm_unavailable` 이 모델 실패를 기본값으로 넘기지 않는 것과 같은 원칙이다.

카카오 타임아웃은 **3초**로 둔다. NF-06 의 20초는 Agent 파이프라인 예산이고 로그인은 그 예산 밖이다.

---

## 9. 테스트

노션 「보호자 권한과 동의」 §5-1 의 P-01~P-12 와 같은 층(유닛)이다. **LLM 없이 결정적으로 검증된다.**

| # | 무엇을 잡는가 | 기대 |
| --- | --- | --- |
| A-01 | 처음 보는 회원번호의 `code` 로 로그인 | `parent` + `auth_identity` + `session` 각 1행, `is_new: true`, `nickname: null` |
| A-02 | 같은 회원번호로 재로그인 | 기존 `parent`, `is_new: false`, **`auth_identity` 행 추가 없음**, `session` 은 새 행 |
| A-03 | 빈 바디 · `code` 누락 | `400 validation_failed` |
| A-04 | 카카오가 코드 교환을 거절 (만료·재사용) | `401 invalid_provider_token`, `parent` **미생성** |
| A-05 | 동의 전에 `POST /children` | `403 consent_required` |
| A-06 | 동의 전에 `GET /me` | **200** — 동의 화면이 부르는 경로 (§6-2) |
| A-07 | 만료된 세션 토큰으로 호출 | `401 unauthenticated` |
| A-08 | 🚨 **로그아웃 직후 같은 토큰으로 호출** | `401 unauthenticated` — **지연 없이 즉시** (§4-2) |
| A-09 | 카카오 API 가 500 | `502 oauth_provider_error`. 로그인 통과 경로 없음 |
| A-10 | 같은 회원번호로 동시 요청 2건 | `UNIQUE` 로 `auth_identity` 한 건만 생성, 둘 다 같은 `parent_id` |
| A-11 | `POST /auth/apple` | `422 validation_failed` |
| A-12 | 로그인 후 `session` 테이블 조회 | **응답 `token` 과 같은 문자열이 어디에도 없다** (해시 저장 확인) |
| A-13 | `parent.deleted_at` 이 찍힌 계정의 세션 | `401` 또는 `404 not_found` — 만료를 기다리지 않는다 |

**A-08 이 이 목록의 이유다.** 실패하면 JWT 대신 불투명 토큰을 고른 이유가 사라진다.

카카오 호출은 유닛 층에서 **스텁으로 대체**한다. `POST /oauth/token` 과 `access_token_info` 두 응답 모양만 고정하면 A-01·A-02·A-04·A-09 가 검증된다.

**`state` 검증은 프론트 책임**이라 이 층에 없다. 프론트 테스트로 따로 잡는다.

### 수동 확인 3건 (자동화 불가)

| # | 무엇 | 누가 |
| --- | --- | --- |
| M-01 | 웹에서 **새로고침** → `sessionStorage` 로 세션이 유지되는가 (§4-4) | 고태영 |
| M-02 | 🚨 **앱에서 `openAuthSessionAsync` 로 카카오 로그인 → 앱 복귀 → 웹뷰 세션 생성** — 실기기 iOS·Android 각각. **카카오톡 전환 로그인도 함께 확인** (§4-5) | 고태영 |
| M-03 | 카카오 세션이 살아 있을 때 **재로그인 체감** → 12시간을 조정할지 판단 (§4-3) | 고태영 |

**M-02 가 이 문서에서 가장 중요한 확인 항목이다.** 스킴 등록·중계 페이지·브릿지가 한 번에 맞물려야 하고, 실기기 없이는 확인되지 않는다.

배포 전 AI 보안 리뷰(고태영)에 두 항목을 추가한다 — **`/auth/*` 예외가 1개(`POST /auth/{provider}`)뿐인지**, **`dangerouslySetInnerHTML` 과 raw HTML 마크다운이 코드에 없는지** (§7-5 1번).

---

## 10. 열린 결정

| # | 무엇 | 왜 지금 못 정하나 | 누구 |
| --- | --- | --- | --- |
| 1 | **탈퇴 유예기간 중 재로그인** — 복구인가 신규인가 | 유예기간 N일(노션 논의 ⑤)이 미정. 그전까지 `404 not_found` 로 막아둔다 | 팀 · 9월 2주 |
| 2 | **세션 수명 12시간이 맞는지** | `M-03` 결과에 달렸다 | 고태영 |
| 3 | **네이티브 카카오 SDK 도입 여부** | §4-5 구조에서는 필요 없다. `M-02` 가 실패하거나 카카오톡 전환 UX 가 불만족스러우면 재검토. 도입하면 `{access_token}` 입력과 `app_id` 대조가 함께 필요해진다 | 고태영 · 김명성 |
| 4 | **Redirect URI 에 커스텀 스킴을 직접 등록할 수 있는지** | 카카오 콘솔이 받아주는지 확인 못 했다. 받아주면 §4-5 의 중계 페이지를 뺄 수 있다 | 고태영 |
| 5 | **Apple 로그인 병행** | iOS 배포 시 App Store 심사 규정 확인 필요 (논의 ⑭). 스키마는 이미 대비돼 있다 | 박재형 |
| 6 | **법정대리인 확인 방식** | 개인정보보호법 제22조의2 는 동의와 별도로 "확인" 의무를 둔다. **카카오 OAuth 로는 확인되지 않는다.** 현재 수단은 `consent.guardian_attested` 자기확인뿐 (논의 ①) | 팀 · **10월 3주 배포 전 필수** |

> 6번은 이 문서가 해결하지 못하는 문제다. 로그인이 아무리 정확해도 "로그인한 사람이 법정대리인인가"는 답하지 않는다.

---

## 11. 환경 변수

`.env.example` 에 키 이름만 넣고 값은 비운다. `.env` 는 커밋하지 않는다.

**서버 (`apps/api`)**

```
KAKAO_REST_API_KEY=      # 카카오 개발자 콘솔 > 앱 키 > REST API 키
KAKAO_CLIENT_SECRET=     # 콘솔 > 카카오 로그인 > 보안 에서 활성화 후 발급
KAKAO_REDIRECT_URI=      # 프론트 콜백. 콘솔 등록값과 정확히 일치해야 한다 (운영·로컬 각각)
KAKAO_ADMIN_KEY=         # 어드민 키. 파기 배치에서만 사용
KAKAO_API_TIMEOUT=3      # 초
SESSION_TTL=43200        # 초 (12시간)
```

**웹 (`apps/web`)** — `NEXT_PUBLIC_*` 는 브라우저 번들에 박힌다. 공개돼도 되는 값만.

```
NEXT_PUBLIC_KAKAO_JS_KEY=        # JavaScript 키
NEXT_PUBLIC_KAKAO_REDIRECT_URI=  # 서버의 KAKAO_REDIRECT_URI 와 같은 값
```

**앱 (`apps/mobile`)** — 추가 없음. 셸은 authUrl 을 웹에서 받으므로 **키를 알 필요가 없다** (§7-4).

---

## 12. 계약서에 반영해야 할 것 🔶

이 문서는 계약서 **§01 공통 규약을 바꾸지 않는다.** `Authorization: Bearer <token> — 예외 없음` 이 그대로 유효하고, `token` 이 불투명 문자열이라는 정의가 추가될 뿐이다.

변경은 4건이다.

| 계약서 현재 | 바뀌어야 할 것 |
| --- | --- |
| `POST /auth/{provider}` 요청 `{access_token}` | **`{code}`** — 웹 클라이언트가 access_token 을 가질 방법이 없다 (§2-1) |
| 응답 | **`expires_in` 추가** (§3-1) |
| 인증·동의 엔드포인트 4개 | **`POST /auth/logout` 신설** (§3-2) |
| 에러 표 | `invalid_provider_token` · `oauth_provider_error` **2건 추가** (§8) |

CLAUDE.md §8 의 결정 방식상 **영역 간 인터페이스 = 관련 Owner 협의**다. 김명성(백엔드) · 고태영(프론트) 합의 후 계약서를 고친다. **합의 전까지 이 문서는 제안 상태로 읽을 것.**

**§5-3 `session` 테이블 신설**과 **§5-2 `parent.nickname` nullable 전환**도 같은 협의 대상이다.
