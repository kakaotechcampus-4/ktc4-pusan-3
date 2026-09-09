# 카카오 OAuth 로그인 v1

문서 목적: 카카오 소셜 로그인의 요청·응답 계약, 세션 규칙, 동의 게이트와의 순서를 고정한다.

기준 브랜치: `docs/be-18-auth-kakao`
작성일: 2026-09-09
담당: 이도헌
선행 문서: [`docs/api/api-interface-v1.html`](./api-interface-v1.html) §01 공통 규약 · §04 인증·동의 — 그 문서의 `POST /auth/{provider}` 를 카카오 기준으로 구체화하고, 구현 불가능한 부분을 교정한다

---

## 0. 30초 요약

| 정한 것 | 값 |
| --- | --- |
| 카카오 인증 | **인가 코드 방식.** 서버가 `client_secret` 으로 code 를 교환한다 |
| 왜 선택이 아닌가 | **카카오 JS SDK 에 클라이언트가 access_token 을 받는 메서드가 없다** (§2-1). 계약서의 `{access_token}` 요청은 웹에서 구현되지 않는다 |
| 세션 토큰 | **불투명 난수** 256비트. DB 에는 SHA-256 해시만 저장 |
| 전달 | **httpOnly 쿠키.** 콜백이 리다이렉트라 JSON 을 줄 수 없다 (§4-1) |
| 세션 수명 | 14일 슬라이딩. refresh 토큰 없음 |
| CSRF | 이중 제출 — `csrf_token` 쿠키 + `X-CSRF-Token` 헤더 (§7-5) |
| 배포 제약 | 웹과 API 를 **같은 사이트**로 (§4-4). 인프라 계획과 이미 일치 |
| 신규 테이블 | `session` 1개 (§5-3 협의 대상) |

**계약서 변경이 큽니다** — `POST /auth/{provider}` 가 `GET /auth/{provider}/login` + `/callback` 으로 바뀌고, §01 인증 줄이 Bearer 에서 쿠키로 바뀝니다 (§12).

### 0-1. 결정은 셋이고 성격이 다르다

논의 시간을 어디에 쓸지가 여기서 갈린다.

| # | 무엇 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | 카카오 인증을 어떻게 받나 | **고정 — 인가 코드** | 선택이 아니라 제약. JS SDK 에 클라이언트가 토큰을 받는 메서드가 없다 (§2-1) |
| 2 | 세션 토큰 형식 | **사실상 결정 — 불투명 난수** | 로그아웃·탈퇴·아이 파기 3곳이 즉시 무효화를 요구해 JWT 가 탈락한다 (§4-2) |
| 3 | **세션을 어떻게 담나 (쿠키 vs 헤더)** | **남은 결정 — 지금은 쿠키** | 아래 |

**3번이 쿠키로 기운 이유** — 인가 코드로 바뀌면서 두 선택지가 대등하지 않게 됐다. 콜백은 카카오가 브라우저를 우리 서버로 보내는 **리다이렉트**라 JSON 응답을 실을 수 없다. 서버가 할 수 있는 건 `Set-Cookie` 하고 프론트로 보내는 것뿐이다.

- **쿠키** → 콜백에서 심고 끝난다
- **헤더** → 콜백에서 쿠키를 심고 → 프론트가 그 쿠키로 토큰을 받아가는 API 를 **한 번 더** 부른다

헤더로 가려면 어차피 쿠키를 한 번 거쳐야 한다. 그럴 거면 거기서 끝내는 편이 낫다.

**3번을 뒤집는 조건은 하나다 — 프론트를 분리 배포하는 경우** (§4-4). 그러면 같은 사이트가 아니라 쿠키가 실리지 않는다. 되돌리는 비용은 싸다: `session` 테이블과 토큰 의미는 그대로고 미들웨어가 읽는 위치와 프론트만 바뀐다.

> **그래서 팀에 물어야 할 것은 "인증 방식을 정해달라"가 아니라 "프론트 배포 위치를 정해달라"다.** 그것이 3번을 결정한다. 1번은 이미 닫혀 있고 2번은 근거가 명확하다.

---

## 1. 범위

**다루는 것** — 로그인·로그아웃 계약, `auth_identity` 생성 규칙, 세션 쿠키와 수명, CSRF 방어, 계정 스코프 동의 게이트와의 순서.

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

## 2. 왜 인가 코드인가 — 선택이 아니라 제약

### 2-1. 계약서 방식은 웹에서 구현되지 않는다

계약서 §04 는 `POST /auth/{provider}` 요청을 `{"access_token": "..."}` 로 정의한다. **웹 클라이언트가 그 값을 가질 방법이 없다.**

카카오 JavaScript SDK 의 `Kakao.Auth` 가 제공하는 메서드는 다음이 전부다.

| 메서드 | 하는 일 |
| --- | --- |
| `authorize(settings)` | **인가 코드를 서비스 서버 URI 로 보낸다.** 토큰을 주지 않는다 |
| `setAccessToken(token)` | 이미 발급된 토큰을 SDK 에 넣는다 |
| `getAccessToken()` | 저장된 토큰을 읽는다 |
| `getStatusInfo()` · `logout()` · `cleanup()` · `getAppKey()` · `selectShippingAddress()` | — |

**클라이언트가 토큰을 발급받는 메서드가 존재하지 않는다.** SDK v1 의 `Kakao.Auth.login()` 은 현재 레퍼런스에 없다. `setAccessToken()` 이 따로 있다는 것 자체가 토큰이 서버에서 내려온다는 뜻이다.

남는 경로를 전부 따져도 결론은 같다.

| 경로 | 판정 |
| --- | --- |
| 브라우저가 직접 `POST /oauth/token` | CORS 로 막힐 가능성이 크고, 되더라도 `client_secret` 을 프론트에 넣어야 해 **NF-09 위반** |
| 네이티브 SDK 로 토큰 수령 | 앱이 **웹뷰 껍데기**라 해당 없음 (§4-5) |

→ **토큰 교환을 서버가 한다는 것은 고정이다.** 그 아래 세부(세션 형식·전달·수명)는 여전히 우리가 정한다.

### 2-2. 부수 효과 — 방어 지점이 하나 사라진다

SDK 토큰 전달 방식이었다면 **다른 앱이 발급받은 카카오 토큰을 우리 서버에 들이밀 수 있고**, 카카오는 유효한 토큰이라 200 을 준다. 막는 장치가 `app_id` 대조 하나뿐이라 그게 빠지면 인증이 성립하지 않는다.

인가 코드는 우리 `client_secret` 으로 교환하므로 **받은 토큰이 정의상 우리 앱 것**이다. 이 경로가 구조적으로 사라진다. OIDC `id_token` 의 `aud` 검증도 같은 이유로 필요 없어진다.

### 2-3. 로그인 플로우

```
① FE  → GET /api/v1/auth/kakao/login?return_to=/home
        ↓
② 서버   state 난수 생성 → oauth_state 쿠키에 심고 302
        Location: https://kauth.kakao.com/oauth/authorize
                  ?client_id=...&redirect_uri=...&response_type=code&state=...
        ↓
③ 사용자  카카오 로그인 (세션이 살아 있으면 화면 없이 통과)
        ↓
④ 카카오 → GET /api/v1/auth/kakao/callback?code=...&state=...
        ↓
⑤ 서버   state 를 oauth_state 쿠키와 대조 (불일치·만료면 거절)
        ↓
⑥ 서버 → POST https://kauth.kakao.com/oauth/token
          grant_type=authorization_code&client_id=...&client_secret=...
          &redirect_uri=...&code=...
        ← { access_token, expires_in, refresh_token, ... }
        ↓
⑦ 서버 → GET https://kapi.kakao.com/v1/user/access_token_info
        ← { id, expires_in, app_id }          회원번호만 쓴다
        ↓
⑧ 서버   auth_identity 에서 (kakao, id) 조회 → 없으면 parent + auth_identity 생성
        ↓
⑨ 서버   카카오 토큰 폐기 (§7-2) · session 행 생성
        ↓
⑩ 서버 → 302 프론트로. Set-Cookie: session · csrf_token
        ↓
⑪ FE  → GET /api/v1/me 로 상태를 읽는다 (is_new · consent_required)
```

**⑦ 을 `/v2/user/me` 가 아니라 `access_token_info` 로 하는 이유** — 회원번호 하나만 필요하다. `/v2/user/me` 는 `kakao_account`(이메일·프로필)까지 실어 오는데, NF-04 는 최소 수집을 요구한다. 받아놓고 안 쓰는 것보다 **애초에 안 받는 것**이 낫다 — 받는 순간 `privacy_account` 동의 문구에 그 항목을 적어야 한다.

---

## 3. 엔드포인트

경로·시간 표기는 [계약서 §01](./api-interface-v1.html)을 따른다. `login` 과 `callback` 은 인증 규칙의 **유일한 예외**다 — 로그인 전에는 세션이 없다.

### 3-1. `GET /auth/{provider}/login`

`provider` ∈ `kakao` · `apple` · `google` · `naver`. **구현은 `kakao` 만.** 그 외는 `422 validation_failed`.

| 쿼리 | 뜻 |
| --- | --- |
| `return_to` | 로그인 후 돌아갈 **프론트 경로**. 생략하면 `/`. **허용 목록 밖이면 거절한다** — 오픈 리다이렉트가 된다 |

**응답 302** — `Location: https://kauth.kakao.com/oauth/authorize?...&state=<난수>`

```
Set-Cookie: oauth_state=<난수>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth; Max-Age=600
```

`return_to` 는 `state` 와 함께 이 쿠키에 담는다. 별도 테이블을 두지 않는다 (§5-4).

### 3-2. `GET /auth/{provider}/callback`

카카오가 부른다. `?code=...&state=...`, 실패 시 `?error=...&error_description=...`.

**성공하면 302** 로 `return_to` 에 보내고 쿠키 2개를 심는다.

```
Set-Cookie: session=Ky8vN2p...; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=1209600
Set-Cookie: csrf_token=9fA2b...; Secure; SameSite=Lax; Path=/; Max-Age=1209600
Set-Cookie: oauth_state=; Max-Age=0
Cache-Control: no-store
```

**응답 바디에 토큰이 없다.** 브라우저가 쿠키를 알아서 붙이므로 프론트는 토큰을 만질 일이 없다.

실패하면 프론트의 에러 화면으로 302 한다. **`code` 나 토큰을 URL 에 실어 보내지 않는다** — 브라우저 히스토리·리퍼러·서버 로그에 남는다.

### 3-3. `POST /auth/logout`

요청 바디 없음. 쿠키로 세션을 식별한다. **응답 204** + 두 쿠키를 `Max-Age=0` 으로 만료시킨다.

`session` 행을 **삭제**한다. 그 순간부터 그 쿠키로는 아무것도 못 한다.

**계약서 28개 목록에 이것이 없다.** 없으면 클라이언트가 쿠키를 버리는 것으로 흉내낼 뿐이고 서버에서는 만료까지 유효하다.

카카오 쪽 로그아웃(`POST /v1/user/logout`)은 **부르지 않는다.** 우리 서비스에서 나가는 것과 카카오 계정에서 로그아웃하는 것은 다른 행위다.

> **`/auth/refresh` 는 없다.** 세션이 14일 슬라이딩이라 재발급 엔드포인트가 필요 없다 (§4-3).

---

## 4. 세션

### 4-1. 왜 쿠키인가

**콜백이 브라우저 리다이렉트라 JSON 응답을 줄 수 없다.** 서버가 `Set-Cookie` 하고 프론트로 보내는 것이 이 흐름의 자연스러운 끝이다.

헤더(Bearer) 방식을 고수하면 콜백 이후 토큰을 프론트에 넘길 방법이 문제가 된다. URL 파라미터는 쓸 수 없다(히스토리·리퍼러·로그에 남는다). 결국 **쿠키를 한 번 심고 → 프론트가 그것으로 토큰을 받아가는 API 를 또 부르는** 2단계가 된다. 쿠키를 쓸 거면 거기서 끝내는 편이 낫다.

부수적으로 XSS 내성도 얻는다. 이 서비스는 기관 공지 붙여넣기·OCR 로 들어온 **외부 텍스트가 LLM 을 거쳐 화면에 렌더링**되는 경로(F-07·F-14)가 있어 XSS 표면이 실재한다. httpOnly 면 스크립트가 세션을 읽지 못한다.

대가는 CSRF 방어(§7-5)와 같은 사이트 배포(§4-4) 두 가지다.

### 4-2. 왜 JWT 가 아닌가

이 서비스는 **즉시 무효화**를 세 곳에서 요구한다 — 로그아웃, 계정 탈퇴(`parent.deleted_at`), 아이 파기(`410 child_deleted`). stateless JWT 는 서명이 유효한 동안 서버가 거절할 방법이 없다. 아동 정보를 다루면서 그 창을 만들 이유가 없다.

DB 조회 1회가 JWT 의 이점이지만 세션 조회는 PK 인덱스 단건 읽기라 실질 비용이 거의 없다. JWT 의 다른 이점(서비스 간 stateless 전달)도 해당되지 않는다 — CLAUDE.md §6 이 백엔드와 AI 를 **한 서비스로 묶기로** 정했고 Agent 는 같은 프로세스의 폴더다. **토큰을 건네줄 상대가 없다.**

부수 효과로 서명 키가 없고, 디코딩 가능한 payload 가 없어 클레임 관리도 필요 없다.

### 4-3. 토큰과 수명

| | 값 |
| --- | --- |
| 생성 | CSPRNG 256비트 → base64url (43자) |
| DB 저장 | **SHA-256 해시.** 원문은 저장하지 않는다 |
| 수명 | **14일** |
| 슬라이딩 | 요청 시 남은 기간이 **7일 미만이면** 14일로 연장하고 쿠키를 다시 내린다 |

**왜 14일인가 — 짧게 둘 이유가 사라졌다.** 헤더 방식이었다면 XSS 로 토큰이 털릴 수 있어 수명을 짧게 두는 것이 방어였다. 쿠키(httpOnly)에서는 그 위험이 없고, 불투명 토큰이라 탈퇴·파기 시 **즉시 무효화**도 된다. 짧게 둘 때 얻는 것이 거의 없다.

반대로 짧게 두면 **재로그인이 `kauth.kakao.com` 리다이렉트를 탄다.** 화면이 한 번 전환되고 앱 상태가 초기화되므로, 이걸 드물게 만드는 편이 낫다.

슬라이딩을 "남은 기간 7일 미만"으로 제한하는 이유 — 매 요청마다 `expires_at` 을 갱신하면 읽기만 하는 요청까지 전부 쓰기가 된다. 이 규칙이면 활성 사용자당 최대 주 1회만 쓴다.

토큰 원문 대신 해시를 저장하므로 **DB 덤프가 유출돼도 살아있는 세션을 그대로 넘겨주지 않는다.**

### 4-4. 🚨 배포 제약 — 같은 사이트여야 한다

**웹과 API 를 같은 등록 가능 도메인 아래에 배포해야 한다.** `SameSite=Lax` 쿠키는 크로스 사이트 요청에 실리지 않으므로, 웹이 `xxx.vercel.app` 이고 API 가 우리 도메인이면 **로그인이 아예 동작하지 않는다.**

노션 「인프라 제안」이 이미 그 구성이다 — 9월 2~3주에 "서버용 compose 작성(DB + 백엔드 + 프론트 전부 컨테이너)"과 "Nginx + HTTPS + 도메인 연결"이 있다. **추가 작업이 없다.**

```
https://<도메인>/          → Next.js
https://<도메인>/api/v1/*  → FastAPI
```

> ⚠️ 같은 문서에 "**혹은 프론트는 분리**"라는 여지와 "t3.medium 에서 Next.js 빌드는 메모리가 빠듯할 수 있어"라는 단서가 남아 있다. **프론트를 분리하면 이 설계가 깨진다.** 분리하기로 하면 세션 전달을 헤더 방식으로 되돌려야 하고, 그때는 `session` 테이블과 토큰 의미는 그대로고 미들웨어와 프론트만 바뀐다.

로컬 개발에서 웹 `:3000`, API `:8000` 은 포트만 다르고 같은 사이트라 문제없다. 다만 `Secure` 쿠키는 HTTPS 를 요구하므로 로컬에서는 `COOKIE_SECURE=false` 로 내린다.

### 4-5. RN 웹뷰

앱은 **웹뷰 껍데기**다 — 위 웹을 그대로 띄우므로 쿠키가 동일하게 동작한다. 다만 웹뷰의 쿠키 저장소는 기본값이 브라우저와 달라서, 아래를 놓치면 **앱을 껐다 켤 때마다 로그인 화면이 뜬다.**

| 지킬 것 | 안 지키면 |
| --- | --- |
| iOS `sharedCookiesEnabled={true}` | 웹뷰를 닫을 때 세션이 날아간다 |
| **영속 데이터 저장소** (비영속 금지) | 앱 재시작마다 재로그인 |
| 앱 시작 시 쿠키를 지우는 초기화 코드를 두지 않는다 | "캐시 비우기" 류 코드가 세션까지 지운다 |

**그리고 카카오 로그인이 지금은 앱 밖으로 튕긴다.** [`apps/mobile/src/config.ts`](../../apps/mobile/src/config.ts) 의 `isInternalUrl` 이 우리 origin 만 웹뷰에 두고 나머지를 시스템 브라우저로 넘긴다. `kauth.kakao.com` 이 다른 origin 이라 **로그인 페이지가 앱 밖에서 열리고 돌아오지 않는다.**

| 필요한 작업 | 왜 |
| --- | --- |
| 카카오 인증 도메인을 **웹뷰 내부 허용 목록에 추가** | 로그인이 웹뷰 안에서 끝나면 앱↔브라우저 연결 문제가 생기지 않는다 |
| `kakaotalk://` · `intent://` 스킴을 `onShouldStartLoadWithRequest` 로 가로채 `Linking.openURL` | 카카오톡 간편로그인이 앱 전환을 쓴다. 웹뷰는 이 스킴을 기본 처리하지 못한다 |

그 외 출처는 계속 시스템 브라우저로 넘긴다 (§7-5 5번).

> **네이티브 화면에서 API 를 직접 부르는 계획이 생기면 이 문서를 먼저 열 것.** 그때는 헤더 방식 병행이 필요하고, 네이티브 SDK 로 카카오 토큰을 직접 받는 경로도 열리므로 `app_id` 대조(§2-2)가 다시 필요해진다.

### 4-6. 보호자 표시 이름

`parent.nickname` 은 **온보딩에서 직접 입력한다.** 카카오 프로필에서 가져오지 않는다.

가져오면 카카오 동의 화면에 항목이 늘고 `privacy_account` 동의 문구에 "카카오 프로필 닉네임"을 적어야 한다. 표시 이름은 보호자 목록·owner 이관·작성자 표시에만 쓰이므로 직접 받는 편이 값도 정확하다.

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
| `csrf_token` | text | NOT NULL. 이중 제출 대조값 (§7-5) |
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

아이 파기 대상 목록(NF-14)에 `auth_identity` 와 함께 포함된다.

### 5-4. `state` 는 테이블을 만들지 않는다

로그인 CSRF 방어용이고 **10분만 살면 된다.** 난수를 `oauth_state` 쿠키(HttpOnly)에 심고 콜백에서 쿼리의 `state` 와 대조하면 끝난다 — 이중 제출과 같은 원리다.

별도 테이블을 두면 만료 정리 배치가 하나 더 생기고, 로그인 시도마다 쓰기가 발생한다. **6명 10주 규모에서 얻는 것이 없다.**

`return_to` 도 이 쿠키에 함께 담는다.

> 콜백은 `kauth.kakao.com` → 우리 서버로 오는 **크로스 사이트 최상위 GET 이동**이다. `SameSite=Lax` 쿠키는 이 경우 정상적으로 실린다.

---

## 6. 동의 게이트와 미들웨어 순서

### 6-1. 로그인은 동의보다 먼저다

동의를 받으려면 누가 동의하는지 알아야 하고, 그러려면 `parent` 행이 먼저 있어야 한다. 그래서 **로그인 자체는 동의 없이 성공한다.** 대신 `GET /me` 의 `consent_required` 가 비어 있지 않으면 FE 는 동의 화면 외 어디로도 가지 못하고, 서버는 `/auth/*` 를 제외한 모든 엔드포인트를 미들웨어에서 막는다.

프론트 차단만 믿지 않는다 — 화면을 우회해 API 를 직접 부르면 그만이다. **NF-10 은 "동의 없는 아동정보 저장 경로가 코드에 존재하지 않는다"이고, 그 강제 지점은 API 계층이다.**

### 6-2. 순서 (NF-11)

```
⓪ verifyCsrf              상태변경 메서드에만. 헤더와 세션의 csrf_token 대조 (§7-5)
① authenticate            session 쿠키 → SHA-256 → session 조회 → parent_id
② requireAccountConsent   service_terms · privacy_account 최신 행이 granted 인가
③ requireConnected / requireOwner / requireWriter    아이 스코프
④ requireChildConsent(scope)   child_basic · child_health
```

| 경로 | 통과해야 하는 것 |
| --- | --- |
| `GET /auth/{provider}/login` · `/callback` | 없음 |
| `POST /auth/logout` | ⓪ ① |
| `GET /me` · `POST /consents` · `GET /consents` | ⓪ ① |
| 그 외 전부 | ⓪ ① ② + (아이 스코프면) ③ ④ |

`GET /me` 와 `/consents` 가 ② 를 건너뛰는 이유 — 동의 화면 자체가 이 둘을 부른다. 여기까지 막으면 동의할 방법이 없다.

핸들러가 직접 권한 쿼리를 쓰지 않는다. 미들웨어 한 곳에서만 판정한다 — 새 API 를 추가할 때 권한 체크가 빠지는 사고를 구조로 막는다.

---

## 7. 보안 규칙

### 7-1. `state` 검증 — 빼면 로그인 CSRF 가 열린다

콜백에서 쿼리의 `state` 를 `oauth_state` 쿠키와 대조한다. 불일치·누락·만료면 `400 invalid_state`.

없으면 공격자가 **자기 카카오 계정의 인가 코드로 만든 콜백 URL** 을 피해자에게 열게 해서, 피해자 브라우저를 공격자 계정으로 로그인시킬 수 있다. 그 뒤 피해자가 입력한 아이 정보가 공격자 계정에 쌓인다.

`return_to` 도 **허용 목록으로 제한한다.** 임의 URL 을 받으면 오픈 리다이렉트가 된다.

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

**콜백 URL 전체를 로깅하지 않는다** — 쿼리에 `code` 가 들어 있다. 카카오 호출 실패도 상태 코드와 에러 코드만 남긴다.

### 7-4. 비밀 관리 (NF-09)

| 값 | 어디에 | 프론트 번들 |
| --- | --- | --- |
| `KAKAO_REST_API_KEY` | 서버 .env | ✕ |
| `KAKAO_CLIENT_SECRET` | 서버 .env | ✕ |
| `KAKAO_ADMIN_KEY` | 서버 .env | ✕ |
| JavaScript 키 | 프론트 | ○ — 공개 전제로 설계된 값 |

불투명 세션에는 **서명 키가 없다.** 토큰이 난수라 서명할 게 없다.

🚨 **이 저장소는 public 이다** (CLAUDE.md §9). 1단계에서 학생 API 토큰 8건이 실제로 유출됐고, 노트북·`docs/*.md` 본문에서도 나왔다. 이 문서를 포함해 **어떤 문서에도 실제 키를 붙여넣지 않는다** — 특히 §11 환경변수 표는 값을 채우고 싶어지는 자리다. 한 번 커밋된 비밀은 지워도 히스토리에 남으므로 **유일한 조치는 폐기(rotate)** 이고, 실수했다면 즉시 담임 매니저에게 알린다.

### 7-5. CSRF — 쿠키를 쓰면 따라오는 비용

쿠키는 브라우저가 자동으로 붙이므로, 다른 사이트가 우리 API 로 요청을 보내게 만들면 그 요청에도 세션이 실린다. **이게 쿠키 방식의 대가이고, 아래가 그 값이다.**

**이중 제출(double submit)** — 로그인 시 `csrf_token` 을 쿠키(HttpOnly 아님)와 `session` 행 양쪽에 넣는다. FE 는 쿠키에서 읽어 상태변경 요청마다 헤더로 보낸다.

```
X-CSRF-Token: 9fA2b...
```

`POST` · `PATCH` · `DELETE` 에 헤더가 없거나 세션의 값과 다르면 `403 csrf_failed`. `GET` 은 검사하지 않는다.

다른 사이트의 스크립트는 우리 도메인의 쿠키를 읽을 수 없으므로 이 헤더를 만들지 못한다. `SameSite=Lax` 와 합쳐 두 겹이 된다.

**XSS 는 여전히 막아야 한다.** httpOnly 는 토큰 탈취를 막을 뿐, XSS 가 나면 공격자가 사용자 브라우저에서 요청을 그대로 보낼 수 있다(쿠키가 자동으로 실린다). 레버리지 순서로 적는다.

| # | 무엇 | 왜 이 순서인가 |
| --- | --- | --- |
| 1 | **LLM 출력과 공지 원문을 HTML 로 렌더링하지 않는다.** `dangerouslySetInnerHTML` 금지, 마크다운은 raw HTML 비활성 (`rehype-raw` 금지) | React 는 기본적으로 이스케이프한다. 구멍은 이 둘뿐이고 막는 비용이 거의 0이다 |
| 2 | **CSP** — `Report-Only` 로 위반을 모은 뒤 강제로 전환 | XSS 가 나도 `connect-src` 로 외부 유출을 막는다. Next.js 는 인라인 스크립트를 쓰므로 처음부터 강제하면 화면이 깨진다 |
| 3 | **`localStorage` 에 세션 관련 값을 두지 않는다** | 현재 [`apps/web/src/stores/session.ts`](../../apps/web/src/stores/session.ts) 의 `partialize` 가 `token` 을 포함한다. 쿠키 전환 시 이 필드 자체가 없어진다 |
| 4 | 웹뷰 `originWhitelist` 로 우리 도메인·카카오 인증 도메인만 허용 (§4-5) | 웹뷰 안에서 임의 사이트가 열리면 그 스크립트가 같은 컨텍스트에 들어온다 |

> 1번은 **파트 경계를 넘는 규칙**이다. 프론트가 지키지만 어겼을 때 깨지는 것은 인증이다. CLAUDE.md §2 절대 규칙에 한 줄로 올리는 것을 제안한다 — *"외부 텍스트와 LLM 출력을 HTML 로 렌더링하지 않는다."*

---

## 8. 에러

계약서 §01 의 에러 형식을 그대로 쓴다. 아래 4개는 이 문서에서 **신설**한다.

| HTTP | code | 언제 | 신설 |
| --- | --- | --- | --- |
| 400 | `invalid_state` | `state` 불일치·누락·만료, `return_to` 가 허용 목록 밖 | 🆕 |
| 400 | `oauth_denied` | 사용자가 카카오 동의 화면에서 취소 (`?error=access_denied`) | 🆕 |
| 401 | `unauthenticated` | 세션 쿠키 없음·만료·이미 삭제됨 | |
| 403 | `csrf_failed` | `X-CSRF-Token` 누락·불일치 (§7-5) | 🆕 |
| 403 | `consent_required` | 계정 스코프 미동의 | |
| 404 | `not_found` | `deleted_at` 이 찍힌 parent (→ §10-1) | |
| 422 | `validation_failed` | `provider` 가 enum 밖 | |
| 502 | `oauth_provider_error` | 카카오 API 5xx·타임아웃, 코드 교환 실패 | 🆕 |

브라우저 리다이렉트로 오는 `login`·`callback` 은 JSON 대신 **프론트 에러 화면으로 302** 하고, 코드만 쿼리에 싣는다(민감정보 없음).

`502 oauth_provider_error` 는 **기본값으로 대체하지 않는다.** 카카오가 죽었을 때 로그인을 통과시키는 경로는 존재하지 않는다 — `503 llm_unavailable` 이 모델 실패를 기본값으로 넘기지 않는 것과 같은 원칙이다.

카카오 타임아웃은 **3초**로 둔다. NF-06 의 20초는 Agent 파이프라인 예산이고 로그인은 그 예산 밖이다.

---

## 9. 테스트

노션 「보호자 권한과 동의」 §5-1 의 P-01~P-12 와 같은 층(유닛)이다. **LLM 없이 결정적으로 검증된다.**

| # | 무엇을 잡는가 | 기대 |
| --- | --- | --- |
| A-01 | 처음 보는 회원번호로 콜백 완료 | `parent` + `auth_identity` + `session` 각 1행, 쿠키 2개 |
| A-02 | 같은 회원번호로 재로그인 | 기존 `parent`, **`auth_identity` 행 추가 없음**, `session` 은 새 행 |
| A-03 | 🚨 **`state` 불일치로 콜백 호출** | `400 invalid_state`, `parent` **미생성** (§7-1) |
| A-04 | `oauth_state` 쿠키 없이 콜백 | `400 invalid_state` |
| A-05 | 만료된(10분 초과) `state` | `400 invalid_state` |
| A-06 | `return_to` 가 외부 URL | `400 invalid_state` — 오픈 리다이렉트 차단 |
| A-07 | 동의 전에 `POST /children` | `403 consent_required` |
| A-08 | 동의 전에 `GET /me` | **200** — 동의 화면이 부르는 경로 (§6-2) |
| A-09 | 🚨 **로그아웃 직후 같은 쿠키로 호출** | `401 unauthenticated` — **지연 없이 즉시** (§4-2) |
| A-10 | `X-CSRF-Token` 없이 `POST` | `403 csrf_failed` |
| A-11 | 다른 세션의 `csrf_token` 값으로 `POST` | `403 csrf_failed` |
| A-12 | 카카오 토큰 교환이 500 | `502 oauth_provider_error`, `parent` 미생성 |
| A-13 | 사용자가 카카오에서 취소 (`?error=access_denied`) | `400 oauth_denied`, 에러 화면으로 302 |
| A-14 | 로그인 후 `session` 테이블 조회 | **쿠키 값과 같은 문자열이 어디에도 없다** (해시 저장 확인) |
| A-15 | `parent.deleted_at` 이 찍힌 계정의 세션 | `401` 또는 `404 not_found` — 만료를 기다리지 않는다 |

**A-03 과 A-09 가 이 목록의 이유다.** A-03 이 실패하면 로그인 CSRF 가 열리고, A-09 가 실패하면 JWT 대신 불투명 토큰을 고른 이유가 사라진다.

카카오 호출은 유닛 층에서 **스텁으로 대체**한다. `POST /oauth/token` 과 `access_token_info` 두 응답 모양만 고정하면 A-01·A-02·A-12 가 검증된다.

### 수동 확인 3건 (자동화 불가)

| # | 무엇 | 누가 |
| --- | --- | --- |
| M-01 | **RN 웹뷰 앱을 완전히 종료했다 다시 켠다** → 세션 유지 (§4-5) | 고태영 |
| M-02 | **웹뷰 안에서 카카오 로그인이 끝나는가** → 허용 목록 추가 후 실제 기기에서 (§4-5) | 고태영 |
| M-03 | **카카오 세션이 살아 있을 때 재로그인 리다이렉트 체감** → 14일 수명을 조정할지 판단 (§4-3) | 고태영 |

배포 전 AI 보안 리뷰(고태영)에 세 항목을 추가한다 — **`/auth/*` 예외가 2개(`login`·`callback`)뿐인지**, **상태변경 엔드포인트에 CSRF 검사가 빠진 곳이 없는지**, **`dangerouslySetInnerHTML` 과 raw HTML 마크다운이 코드에 없는지**.

---

## 10. 열린 결정

| # | 무엇 | 왜 지금 못 정하나 | 누구 |
| --- | --- | --- | --- |
| 1 | **탈퇴 유예기간 중 재로그인** — 복구인가 신규인가 | 유예기간 N일(노션 논의 ⑤)이 미정. 그전까지 `404 not_found` 로 막아둔다 | 팀 · 9월 2주 |
| 2 | **세션 수명 14일이 맞는지** | M-03 결과에 달렸다. 리다이렉트 체감이 없으면 더 짧게 둬도 된다 | 고태영 |
| 3 | **프론트 배포 위치** | 같은 서버면 지금 설계 그대로. 분리하면 §4-4 가 깨져 헤더 방식으로 되돌려야 한다 | 김명성 · 고태영 |
| 4 | **Apple 로그인 병행** | iOS 배포 시 App Store 심사 규정 확인 필요 (논의 ⑭). 스키마는 이미 대비돼 있다 | 박재형 |
| 5 | **법정대리인 확인 방식** | 개인정보보호법 제22조의2 는 동의와 별도로 "확인" 의무를 둔다. **카카오 OAuth 로는 확인되지 않는다.** 현재 수단은 `consent.guardian_attested` 자기확인뿐 (논의 ①) | 팀 · **10월 3주 배포 전 필수** |

> 5번은 이 문서가 해결하지 못하는 문제다. 로그인이 아무리 정확해도 "로그인한 사람이 법정대리인인가"는 답하지 않는다.

---

## 11. 환경 변수

`.env.example` 에 키 이름만 넣고 값은 비운다. `.env` 는 커밋하지 않는다.

```
KAKAO_REST_API_KEY=      # 카카오 개발자 콘솔 > 앱 키 > REST API 키
KAKAO_CLIENT_SECRET=     # 콘솔 > 카카오 로그인 > 보안 에서 활성화 후 발급
KAKAO_REDIRECT_URI=      # 콘솔에 등록한 값과 정확히 일치해야 한다 (운영·로컬 각각 등록)
KAKAO_ADMIN_KEY=         # 어드민 키. 파기 배치에서만 사용
KAKAO_API_TIMEOUT=3      # 초
SESSION_TTL=1209600      # 초 (14일)
SESSION_RENEW_BEFORE=604800   # 초 (7일). 남은 기간이 이보다 적으면 연장
OAUTH_STATE_TTL=600      # 초 (10분)
COOKIE_SECURE=true       # 로컬 개발(HTTP)에서만 false
FRONTEND_ALLOWED_RETURN_PATHS=/,/home,/onboarding   # return_to 허용 목록
```

---

## 12. 계약서에 반영해야 할 것 🔶

이 문서는 계약서 §01·§04 를 **여러 곳 바꾼다.** 인가 코드가 제약이라 피할 수 없다 (§2-1).

| 계약서 현재 | 바뀌어야 할 것 |
| --- | --- |
| §01 인증 `Authorization: Bearer <token>` — 예외 없음 | **세션 쿠키(`session`, httpOnly)로 판정.** 상태변경 요청은 `X-CSRF-Token` 필수. "예외 없음"의 취지(NF-09 — 인증 없는 엔드포인트를 만들지 않는다)는 그대로 유지 |
| §04 `POST /auth/{provider}` 요청 `{access_token}` | **삭제.** `GET /auth/{provider}/login` + `GET /auth/{provider}/callback` 으로 대체 (§3) |
| §04 응답의 `token` 필드 | **삭제.** 세션은 `Set-Cookie` 로 내려간다. `is_new`·`consent_required` 는 `GET /me` 로 읽는다 |
| 엔드포인트 목록 28개 | `POST /auth/logout` **추가** |
| 에러 표 | `invalid_state` · `oauth_denied` · `csrf_failed` · `oauth_provider_error` **4건 추가** |

CLAUDE.md §8 기준 **영역 간 인터페이스 = 관련 Owner 협의**다. 김명성(백엔드) · 고태영(프론트) 합의 후 계약서를 고친다. **합의 전까지 이 문서는 제안 상태로 읽을 것.**

**§5-3 `session` 테이블 신설**과 **§5-2 `parent.nickname` nullable 전환**도 같은 협의 대상이다.
