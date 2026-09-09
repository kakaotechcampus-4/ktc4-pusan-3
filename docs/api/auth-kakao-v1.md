# 카카오 OAuth 로그인 v1

문서 목적: 카카오 소셜 로그인의 요청·응답 계약, 세션 규칙, 동의 게이트와의 순서를 고정한다.

기준 브랜치: `feat/be-auth-kakao`
작성일: 2026-09-07
담당: 김명성
선행 문서: [`docs/api/api-interface-v1.html`](./api-interface-v1.html) §04 인증·동의 — 그 문서의 `POST /auth/{provider}` 한 줄을 카카오 기준으로 구체화한다

---

## 0. 30초 요약

**계약서 v1 의 방향을 유지한다.** 계약서가 비워둔 칸(토큰 형식·수명·무효화)만 채운다.

| 정한 것 | 값 |
| --- | --- |
| 카카오 인증 | **클라이언트 SDK 가 access_token 을 받아 서버에 넘긴다** — 계약서 요청 `{access_token}` 그대로 |
| 서버가 카카오에 부르는 것 | `GET /v1/user/access_token_info` **1회뿐**. 회원번호와 app_id 만 얻는다 |
| 세션 토큰 형식 | **불투명 난수.** JWT 아님 — 즉시 무효화가 필요하다 (§4-1) |
| 전달 방식 | `Authorization: Bearer <token>` — **계약서 §01 그대로** |
| 세션 수명 | 12시간. 만료되면 401 → FE 가 SDK 로 토큰을 다시 받는다 (§4-2) |
| 클라이언트 저장 | **메모리 전용.** `localStorage` 금지 (§4-3) |
| 보호자 표시 이름 | 카카오에서 가져오지 않는다. 온보딩에서 직접 입력 (노션 논의 ⑫) |
| 저장하지 않는 것 | 카카오 토큰 · 이메일 · 프로필 · 닉네임 · **세션 토큰 원문** |
| 신규 테이블 | `session` 1개 (§5-3 협의 대상) |
| 🚨 충돌 | PR #11 의 `parent.nickname` 이 NOT NULL — 그대로면 **로그인이 불가능하다** (§5-2) |

**계약서 변경은 3건뿐이다** — `POST /auth/logout` 신설, 응답에 `expires_in` 추가, 에러 2건 추가. §01 공통 규약은 손대지 않는다 (§12).

---

## 1. 범위

**다루는 것** — 로그인·로그아웃 계약, `auth_identity` 생성 규칙, 세션 토큰 형식과 수명, XSS 방어, 계정 스코프 동의 게이트와의 순서.

**다루지 않는 것**

| 무엇 | 어디로 |
| --- | --- |
| 동의 문구·개인정보 처리방침·이용약관 | `safety/` — NF-04 확정이 선행 조건이라 아직 쓸 수 없다 |
| 아이 스코프 권한 (`requireConnected` · `requireOwner` · `requireWriter`) | 노션 「테크스팩 보완 제안 — 보호자 권한과 동의」 §2-1 |
| 보호자 초대·연결 (`POST /invites`) | 같은 문서 §2-3 |
| 계정 탈퇴·파기 배치 | 같은 문서 §2-5. 이 문서는 파기 시 카카오 연결 끊기 호출만 §7-2 에 남긴다 |

**1차 배포는 카카오만.** `auth_identity.provider` enum 은 `kakao / apple / google / naver` 4종을 그대로 두되 카카오 외에는 구현하지 않는다 (노션 논의 ⑬). 경로를 `/auth/kakao` 로 굳히지 않고 `/auth/{provider}` 를 유지하는 이유가 이것이다 — 제공자가 늘어도 계약서 목록이 바뀌지 않는다.

### 1-1. 🔶 이번 검토에서 새로 발견된 것 — 별도 이슈가 필요하다

인증(누구인가)이 아니라 **권한(무엇을 할 수 있나)** 영역이라 이 문서에서 다루지 않는다. 다만 **어느 문서에도 없던 것**이라 여기 기록해 잃어버리지 않게 한다.

| # | 무엇 | 왜 구멍인가 |
| --- | --- | --- |
| 1 | **간접 식별자에서 소유 아이를 역추적해 검사한다** — `run_id` · `memory_id` · `suggestion_id` 만 받는 API | 노션 권한표는 `/children/{cid}/*` 경로만 다룬다. 그런데 계약서에는 `GET /runs/{rid}/events` 처럼 **child_id 가 경로에 없는** 엔드포인트가 있다. 검사가 빠지면 남의 아이 run 을 구독할 수 있다 |
| 2 | **AI 도구의 아이 범위는 서버가 주입한다.** 모델이 넘긴 child_id 를 믿지 않는다 | Agent 가 `memory.search` 를 직접 부르는 구조(CLAUDE.md §4)에서, 도구 인자를 그대로 신뢰하면 **프롬프트 인젝션이 곧 권한 상승**이 된다. 공지 붙여넣기·OCR 로 외부 텍스트가 들어오는 서비스라 남 얘기가 아니다 |
| 3 | **동의 철회 시 실행 중 작업을 외부 전달·저장 직전에 재검사한다** | 20초 부분 결과 구조(NF-06)에서 철회가 중간에 들어오면 이미 시작된 Agent 가 결과를 뱉는다. 시작 시점 검사만으로는 부족하다 |
| 4 | **SSE 재연결이 새 run 을 만들지 않는다.** 이벤트 ID 로 재개 커서를 잡는다 | 인증이 만료돼 갱신한 뒤 스트림을 다시 열 때 AI 실행이 중복 생성되면, 비용과 기록이 함께 이중으로 쌓인다 |

1·2 는 `docs/api/` (김명성), 3 은 `safety/` (박재형), 4 는 `agents/`·`api/` 협의 대상이다.

---

## 2. 로그인 플로우

```
① FE   카카오 SDK 로 로그인 (웹: JavaScript 키 · RN 웹뷰: 같은 웹을 띄운다)
        ← 카카오 access_token
        ↓
② FE → POST /api/v1/auth/kakao  { "access_token": "..." }
        ↓
③ 서버 → GET https://kapi.kakao.com/v1/user/access_token_info
          Authorization: Bearer {카카오 access_token}
        ← { "id": 3123456789, "expires_in": 21599, "app_id": 1234 }
        ↓
④ 서버   app_id 가 우리 앱인가?  아니면 → 401 invalid_provider_token  (§7-1)
        ↓
⑤ 서버   auth_identity 에서 (kakao, id) 조회
          있으면 → 그 parent_id                        is_new = false
          없으면 → parent + auth_identity 생성          is_new = true
        ↓
⑥ 서버   카카오 access_token 을 폐기한다. 어디에도 저장하지 않는다 (§7-2)
        ↓
⑦ 서버   session 행 생성 · consent_required 계산
        ↓
⑧ FE ←  200 { token, expires_in, is_new, parent, consent_required }
        ↓
⑨ FE   token 을 메모리에만 둔다. 이후 요청에 Authorization 헤더로 실어 보낸다
```

### 왜 서버가 `access_token_info` 만 부르는가

`GET /v2/user/me` 를 부르면 회원번호 외에 `kakao_account`(이메일·프로필)까지 응답에 실려 온다. 우리가 필요한 건 **회원번호 하나**뿐이고, NF-04 는 최소 수집을 요구한다. 받아놓고 안 쓰는 것보다 **애초에 안 받는 것**이 낫다 — 받는 순간 `privacy_account` 동의 문구에 그 항목을 적어야 한다.

`access_token_info` 는 회원번호와 `app_id` 를 함께 주므로 §7-1 의 앱 대조까지 이 한 번의 호출로 끝난다.

### 이 방식의 대가 — 반드시 알고 쓸 것

계약서 v1 을 유지하기로 한 결정에 따라 **카카오 access_token 이 클라이언트에 존재한다.** 서버가 인가 코드를 교환하는 방식이었다면 카카오 토큰이 프론트에 아예 없었을 것이다.

이 선택의 대가는 **다른 앱이 발급받은 카카오 토큰을 우리 서버에 들이밀 수 있다**는 것이다. 카카오 입장에서는 유효한 토큰이므로 `access_token_info` 는 200 을 준다. 막는 것은 §7-1 의 `app_id` 대조 하나뿐이다. **이 검사를 빼면 아무 카카오 앱 개발자나 우리 서비스의 임의 계정으로 로그인할 수 있다.**

→ §9 의 **A-06** 이 이 검사를 지키는 테스트다. 지우지 말 것.

### 이 방식의 이득 — refresh 토큰이 필요 없다

같은 구조가 이점도 하나 준다. **클라이언트가 카카오 세션을 이미 들고 있으므로**, 우리 세션이 만료되면 SDK 로 카카오 토큰을 다시 받아 ② 를 한 번 더 부르면 된다. 사용자 입력이 없다.

즉 **카카오 SDK 가 refresh 토큰 역할을 대신한다.** 그래서 이 문서에는 refresh 토큰도, 토큰 회전도 없다 — 계약서 응답에 `refresh_token` 이 없는 것이 누락이 아니라 이 구조와 짝이 맞는 것이다.

이것이 §4-2 의 짧은 수명과 §4-3 의 메모리 전용 저장을 가능하게 한다. 둘 다 "다시 받으면 된다"에 기대고 있다.

---

## 3. 엔드포인트

경로·헤더·시간 표기는 [계약서 §01 공통 규약](./api-interface-v1.html)을 따른다. `POST /auth/{provider}` 는 인증 헤더 규칙의 **유일한 예외**다 — 로그인 전에는 세션이 없다.

### 3-1. `POST /auth/{provider}` — 계약서에 있음, 응답만 보강

`provider` ∈ `kakao` · `apple` · `google` · `naver`. **구현은 `kakao` 만.** 그 외 값은 `422 validation_failed`.

**요청** — 계약서 그대로

```json
{
  "access_token": "carLxSDf..."
}
```

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
| `expires_in` | 초. **계약서에 없어서 추가한다** — 없으면 FE 가 만료를 미리 알 방법이 없어 매번 401 을 맞고 나서야 재발급한다 |
| `is_new` | 이번 호출에서 `parent` 가 생성됐는가. FE 는 이 값으로 온보딩 분기를 태운다 |
| `parent.nickname` | **신규는 항상 `null`.** 카카오에서 가져오지 않는다 (§4-5) |
| `consent_required` | 계정 스코프 중 아직 `granted` 가 아닌 것. **비어 있지 않으면 FE 는 동의 화면 외 어디로도 갈 수 없다** |

`Idempotency-Key` 는 **받지 않는다.** 같은 카카오 회원번호로 두 번 불려도 `UNIQUE (provider, provider_user_id)` 가 두 번째 생성을 막고 기존 `parent` 를 그대로 돌려준다 — 이 엔드포인트는 그 자체로 멱등하다.

세션이 만료됐을 때 FE 가 다시 부르는 것도 이 엔드포인트다. **별도의 재발급 엔드포인트를 만들지 않는다** (§2 이득 절).

### 3-2. `POST /auth/logout` — 🔶 신설

요청 바디 없음. `Authorization` 헤더로 세션을 식별한다. **응답 204.**

`session` 행을 **삭제**한다. 그 순간부터 그 토큰으로는 아무것도 못 한다.

**계약서 28개 목록에 이것이 없다.** 없으면 클라이언트가 토큰을 버리는 것으로 로그아웃을 흉내낼 뿐이고, 서버에서는 그 토큰이 만료까지 계속 유효하다. 즉시 무효화(§4-1)를 쓰려면 반드시 있어야 한다.

카카오 쪽 로그아웃(`POST /v1/user/logout`)은 **부르지 않는다.** 우리 서비스에서 나가는 것과 카카오 계정에서 로그아웃하는 것은 다른 행위이고, 사용자가 요청한 적 없는 쪽까지 끊으면 다음 로그인이 불필요하게 번거로워진다.

---

## 4. 세션

### 4-1. 왜 JWT 가 아닌가

계약서는 `token` 이 JWT 인지 불투명 문자열인지 적지 않았다. **불투명으로 정한다.**

이 서비스는 **즉시 무효화**를 세 곳에서 요구한다 — 로그아웃, 계정 탈퇴(`parent.deleted_at`), 아이 파기(`410 child_deleted`). stateless JWT 는 서명이 유효한 동안 서버가 거절할 방법이 없어서, "탈퇴했는데 만료까지는 여전히 접근된다" 같은 창이 생긴다. 아동 정보를 다루면서 그 창을 만들 이유가 없다.

DB 조회 1회를 아끼는 것이 JWT 의 이점이지만, 세션 조회는 PK 인덱스 단건 읽기라 실질 비용이 거의 없다. **무효화를 포기할 만한 대가가 아니다.**

JWT 의 다른 대표 이점(서비스 간 stateless 전달)도 이 프로젝트에는 해당되지 않는다 — CLAUDE.md §6 이 백엔드와 AI 를 **한 서비스로 묶기로** 정했고, Agent 는 같은 프로세스의 `app/agents/` 폴더다. **토큰을 건네줄 상대가 없다.**

부수 효과로 관리할 것이 줄어든다: 서명 키가 없고, 디코딩 가능한 payload 가 없어 클레임에 무엇을 넣을지 고민할 일도 없다.

### 4-2. 토큰과 수명

| | 값 |
| --- | --- |
| 생성 | CSPRNG 256비트 → base64url (43자). 추측 불가 |
| DB 저장 | **SHA-256 해시.** 원문은 저장하지 않는다 (§7-2) |
| 수명 | **12시간** (`expires_in: 43200`) |
| 만료 시 | `401 unauthenticated` → FE 가 SDK 로 카카오 토큰을 받아 `POST /auth/kakao` 를 다시 부른다 |

**12시간으로 짧게 두는 근거** — §2 의 이득 절 때문이다. 재로그인이 사용자 입력 없이 끝나므로 수명을 짧게 두는 UX 비용이 낮다. 그리고 짧은 수명은 §7-5 의 XSS 위험을 직접 줄인다 — 털린 토큰의 유효 기간이 그만큼이다.

슬라이딩 연장을 두지 않는다. 12시간마다 재로그인 경로를 타면 되고, 연장을 넣으면 읽기 요청이 쓰기로 바뀐다.

토큰 원문 대신 해시를 저장하므로 **DB 덤프가 유출돼도 살아있는 세션을 그대로 넘겨주지 않는다.** 비밀번호를 해시로 저장하는 것과 같은 이유다.

### 4-3. 🚨 클라이언트 저장 위치 — `localStorage` 를 쓰지 않는다

헤더 방식을 고른 대가는 **토큰이 JS 에서 접근 가능하다**는 것이다 (§7-5). 그 대가를 가장 크게 줄이는 한 가지가 저장 위치다.

| 위치 | 판정 |
| --- | --- |
| **메모리 (모듈 스코프 변수)** | ✅ 권장. XSS 가 저장소를 훑어도 나오지 않는다 |
| `sessionStorage` | 차선. 탭을 닫으면 사라져 `localStorage` 보다 표면이 작다 |
| `localStorage` | ✕ **금지.** XSS 한 건이 토큰을 그대로 넘긴다 |

**메모리 전용이 가능한 이유** — 보통 메모리 저장을 못 하는 이유는 "새로고침하면 토큰이 사라진다"인데, 이 구조에서는 **401 처리 경로와 새로고침 복구 경로가 같다.** 둘 다 "SDK 로 카카오 토큰을 받아 `POST /auth/kakao` 를 다시 부른다"로 끝난다. 경로를 한 번만 만들면 된다.

> **확인 필요** — 웹 JS SDK 로 재로그인할 때 **kauth.kakao.com 리다이렉트가 한 번 끼는지**. 끼면 새로고침마다 화면이 튀므로 UX 판단이 필요하고, 그때는 `sessionStorage` 로 타협한다. 고태영님 확인 항목 (§9 M-02).

**🚨 현재 코드가 이 규칙을 어기고 있다.** [`apps/web/src/stores/session.ts`](../../apps/web/src/stores/session.ts) 가 Zustand persist 로 토큰을 영속 저장한다.

```ts
storage: createJSONStorage(() => localStorage),
partialize: (s) => ({ token: s.token, activeChildId: s.activeChildId }),   // ← token
```

고칠 것은 `partialize` 에서 `token` 을 빼는 한 줄이지만, **빼는 순간 새로고침에서 인증이 사라지므로 복구 경로와 같이 가야 한다.** 두 작업을 분리하면 그 사이에 앱이 로그인 상태를 유지하지 못한다.

`activeChildId` 는 계속 저장해도 된다 — 화면 선택값일 뿐 권한 근거가 아니고(§1-1 참고), `/me` 결과로 유효성을 다시 확인한다.

### 4-4. RN 웹뷰

앱은 **웹뷰 껍데기**다 — 위 웹을 그대로 띄우므로 토큰 처리도 웹과 동일하다. 쿠키를 쓰지 않으므로 웹뷰 쿠키 저장소 설정은 필요 없다.

**다만 현재 셸 설정으로는 카카오 로그인이 웹뷰 밖으로 튕긴다.** [`apps/mobile/src/config.ts`](../../apps/mobile/src/config.ts) 가 우리 origin 만 웹뷰 안에 두고 나머지는 시스템 브라우저로 넘긴다.

```ts
export function isInternalUrl(url: string): boolean {
  return new URL(url).origin === ALLOWED_ORIGIN;   // 우리 도메인만
}
```

`kauth.kakao.com` 이 다른 origin 이라 **로그인 페이지가 시스템 브라우저에서 열리고, 거기서 로그인해도 웹뷰로 돌아오지 않는다.** 앱에서 로그인이 아예 완결되지 않는다.

필요한 작업은 두 가지로 본다.

| 무엇 | 왜 |
| --- | --- |
| 카카오 인증 도메인을 **웹뷰 내부 허용 목록에 추가** | 로그인이 웹뷰 안에서 끝나면 앱↔브라우저 연결 문제가 아예 생기지 않는다 |
| `kakaotalk://` · `intent://` 스킴을 `onShouldStartLoadWithRequest` 에서 가로채 `Linking.openURL` 로 넘긴다 | 카카오톡 간편로그인이 앱 전환을 쓴다. 웹뷰는 이 스킴을 기본 처리하지 못한다 |

그 외 출처는 계속 시스템 브라우저로 넘긴다 — 웹뷰 안에서 임의 사이트가 열리면 그 페이지의 스크립트가 같은 웹뷰 컨텍스트에 들어온다 (§7-5 5번).

> **이 방향을 먼저 시험할 것.** 시스템 브라우저로 로그인하고 앱으로 되돌리는 설계(일회용 교환 코드 · App Link 등록 · 거래 테이블)는 **위 두 가지가 막혔을 때의 대안**이다. 순서를 바꾸면 안 해도 될 일을 몇 주 한다. RFC 8252 가 임베디드 웹뷰를 경계하는 것은 **남의 로그인 화면을 감싸는 서드파티 앱** 맥락이고, 우리는 우리 웹을 띄우는 1st-party 껍데기다.

> **네이티브 화면에서 API 를 직접 부르는 경로가 생겨도 이 설계는 그대로 동작한다.** 헤더 방식이라 쿠키의 `SameSite` 제약이 없다. 다만 그때는 토큰을 메모리가 아니라 **Keychain/Keystore(SecureStore)** 에 두어야 하고, `AsyncStorage` 는 `localStorage` 와 같은 이유로 금지다.

### 4-5. 보호자 표시 이름

`parent.nickname` 은 **온보딩에서 보호자가 직접 입력한다.** 카카오 프로필에서 가져오지 않는다.

가져오면 카카오 동의 화면에 항목이 하나 늘고, `privacy_account` 동의 문구에 "카카오 프로필 닉네임"을 적어야 한다. 표시 이름은 보호자 목록·owner 이관 화면·기록 작성자 표시에만 쓰이므로 직접 받는 편이 값도 정확하다(카카오 닉네임이 실명인 경우가 드물다).

→ 노션 논의 사항 ⑫ 는 이 결정으로 닫힌다.

---

## 5. 스키마

### 5-1. `auth_identity` — 노션 확정안 그대로

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK |
| `parent_id` | uuid | FK → `parent.id`, NOT NULL |
| `provider` | enum | `kakao / apple / google / naver`, NOT NULL. 구현은 kakao 만 |
| `provider_user_id` | text | 카카오 회원번호. NOT NULL |
| `linked_at` | timestamptz | NOT NULL, default now() |

`UNIQUE (provider, provider_user_id)`

카카오 회원번호는 Long 이지만 **text 로 저장한다.** Apple 은 문자열 식별자를 주므로 제공자가 늘 때 컬럼 타입이 바뀌면 안 된다.

**계정 자동 통합 없음.** 같은 이메일로 보이는 다른 제공자 계정을 합치지 않는다. 이메일을 받지도 않을뿐더러, 이메일은 바뀌는 값이라 식별자로 쓸 수 없다.

> ✅ **이미 구현돼 있다.** [PR #11](https://github.com/kakaotechcampus-4/ktc4-pusan-3/pull/11) (`feat/be-3-data-models`, 오현식) 의 `apps/api/app/domains/identity/models.py` 에 위 표와 **필드·제약이 정확히 일치**하는 `AuthIdentity` 가 있다. 이 문서는 그것을 그대로 쓴다.

### 5-2. 🚨 `parent.nickname` 충돌 — 지금 고쳐야 한다

같은 PR #11 의 `Parent.nickname` 이 **`nullable=False`** 다. 이 문서 §3-1 은 신규 로그인 응답의 `parent.nickname` 이 `null` 이라고 적었는데, **NOT NULL 이면 로그인 시점에 `parent` 행을 만들 수 없다.**

**왜 로그인 시점에 만들어야 하나** — `consent.parent_id` 가 NOT NULL 이고, 최초 로그인 직후 `service_terms` · `privacy_account` 를 받아야 한다 (§6-1). 동의를 기록하려면 `parent` 가 **이미 있어야** 한다. 온보딩까지 생성을 미룰 수 없다.

그런데 표시 이름은 카카오에서 가져오지 않기로 했으므로 (§4-5 · 노션 논의 ⑫), **로그인 시점에 채울 값이 없다.**

| 안 | 판정 |
| --- | --- |
| **`nickname` 을 nullable 로 바꾼다** | ✅ 온보딩에서 채운다. "아직 입력 안 함"이 `NULL` 로 정직하게 표현된다 |
| 빈 문자열 등 더미값을 넣는다 | ✕ "입력 안 함"과 "빈 이름"을 구분할 수 없다. 보호자 목록·작성자 표시 화면에서 빈칸의 원인을 추적하지 못한다 |
| 카카오 닉네임을 가져와 채운다 | ✕ 논의 ⑫ 결정을 뒤집는다. `privacy_account` 동의 문구에 항목이 늘어난다 |

→ **PR #11 이 머지되기 전에 `nickname` 을 nullable 로 바꾸는 것을 권한다.** 머지 후에는 마이그레이션이 한 번 더 필요하다. 오현식 · 김명성 확인 대상.

### 5-3. `session` — 신설 🔶 협의 대상

노션 「보호자 권한과 동의」의 신설 테이블 목록에 **이 테이블은 없다.** 토큰 형식을 이 문서에서 정하면서 필요해진 것이므로 **김명성님 확정 대상**이다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK |
| `parent_id` | uuid | FK → `parent.id`, NOT NULL |
| `token_hash` | bytea | **UNIQUE, NOT NULL.** SHA-256(토큰 원문). 원문은 저장하지 않는다 |
| `expires_at` | timestamptz | NOT NULL |
| `created_at` | timestamptz | NOT NULL, default now() |

```sql
CREATE UNIQUE INDEX ON session (token_hash);
CREATE INDEX ON session (parent_id);
CREATE INDEX ON session (expires_at);
```

**폐기는 행 삭제로 한다.** `revoked_at` 을 두지 않는 이유 — 세션은 증빙이 아니다. `consent` 가 append-only 인 것과 정반대로, 여기 남은 행은 유출 표면일 뿐이라 지우는 게 맞다.

| 언제 | 무엇을 지우나 |
| --- | --- |
| 로그아웃 | 그 행 1건 |
| 계정 탈퇴 · 아이 파기 | `parent_id` 로 전부 |
| 만료 | 배치로 `expires_at < now()` 전부 |

수명이 12시간이라 행이 오래 쌓이지 않는다. 아이 파기 대상 목록(NF-14)에 `auth_identity` 와 함께 이 테이블도 포함된다.

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

`GET /me` 와 `/consents` 가 ② 를 건너뛰는 이유 — 동의 화면 자체가 이 두 개를 부른다. 여기까지 막으면 사용자가 동의를 할 방법이 없다.

핸들러가 직접 권한 쿼리를 쓰지 않는다. 미들웨어 한 곳에서만 판정한다 — 새 API 를 추가할 때 권한 체크가 빠지는 사고를 구조로 막는다.

**CSRF 방어는 필요 없다.** 쿠키를 쓰지 않으므로 브라우저가 자동으로 자격증명을 붙이지 않는다. 다른 사이트가 우리 API 로 요청을 보내도 `Authorization` 헤더가 실리지 않는다.

---

## 7. 보안 규칙

### 7-1. 🚨 앱 대조 — 이 문서에서 가장 중요한 한 줄

```python
info = kakao.get("/v1/user/access_token_info", token=access_token)
if str(info["app_id"]) != settings.KAKAO_APP_ID:
    raise Unauthorized("invalid_provider_token")
```

**이 검사가 없으면 인증이 성립하지 않는다.** 다른 카카오 앱에서 발급된 토큰도 카카오 API 는 정상 처리하므로, 아무 앱 개발자나 자기 앱 사용자의 토큰으로 우리 서비스에 로그인할 수 있다. §2 의 "이 방식의 대가"가 이것이고, 막는 장치는 이 대조뿐이다.

`KAKAO_APP_ID` 는 카카오 개발자 콘솔의 **앱 ID**(숫자)다. REST API 키·JavaScript 키와 다른 값이니 혼동하지 말 것.

### 7-2. 저장하지 않는 것

| 무엇 | 왜 |
| --- | --- |
| 카카오 access_token · refresh_token | 우리는 카카오 API 를 더 부르지 않는다. 안 쓰는 자격증명을 보관하면 유출 표면만 늘어난다 |
| 이메일 · 프로필 이미지 · 닉네임 | NF-04 최소 수집. `access_token_info` 는 애초에 주지도 않는다 |
| **세션 토큰 원문** | 해시만 저장한다. DB 덤프가 살아있는 세션이 되지 않게 (§4-2) |

**예외 — 파기 배치.** 아이·계정 파기 시 카카오 연결을 끊어야 하는데, 토큰이 없으므로 어드민 키로 대상을 지정한다.

```
POST https://kapi.kakao.com/v1/user/unlink
Authorization: KakaoAK {SERVICE_APP_ADMIN_KEY}
Content-Type: application/x-www-form-urlencoded;charset=utf-8

target_id_type=user_id&target_id={provider_user_id}
```

어드민 키는 앱 전체 권한이다. **서버 .env 에만 두고, 파기 배치 외 어떤 코드 경로에서도 부르지 않는다.**

### 7-3. 로그

로그에 **카카오 회원번호·access_token·세션 토큰을 남기지 않는다.** 식별이 필요하면 `parent_id` 만 쓴다. NF-05 의 "원문 대신 `memory_id`" 를 인증 영역으로 확장한 것이다.

카카오 호출 실패를 로깅할 때 응답 본문을 통째로 찍으면 토큰이 섞여 들어간다. 상태 코드와 카카오 에러 코드만 남긴다. **에러 응답에 요청받은 토큰을 되돌려주지 않는다.**

### 7-4. 비밀 관리 (NF-09)

| 값 | 어디에 | 프론트 번들 |
| --- | --- | --- |
| `KAKAO_APP_ID` | 서버 .env | ✕ |
| `KAKAO_ADMIN_KEY` | 서버 .env | ✕ |
| JavaScript 키 · 네이티브 앱키 | 프론트 | ○ — 공개 전제로 설계된 값이라 노출되어도 된다 |

불투명 토큰에는 **서명 키가 없다.** 토큰이 난수라 서명할 게 없기 때문이다 — JWT 를 안 쓰면서 관리할 비밀이 하나 줄었다.

🚨 **이 저장소는 public 이다.** 1단계에서 학생 토큰 8건이 유출됐고 일부는 `docs/*.md` 본문에서 나왔다. 이 문서를 포함해 어떤 문서에도 실제 키를 붙여넣지 않는다. 한 번 커밋된 비밀은 지워도 히스토리에 남으므로 **유일한 조치는 폐기(rotate)** 다.

### 7-5. XSS 방어 — 헤더 방식을 고른 대가

토큰이 JS 에서 접근 가능하므로, **XSS 한 건이 세션을 넘긴다.** 쿠키(httpOnly)를 쓰지 않기로 한 대가이고, 아래가 그 값이다.

이 서비스의 XSS 는 막연한 위험이 아니라 **경로가 특정된다** — 기관 공지 붙여넣기·OCR 로 들어온 **외부 텍스트가 LLM 을 거쳐 화면에 렌더링**된다 (F-07 · F-14). 공격자가 통제하는 텍스트가 화면까지 도달하는 경로가 설계에 존재한다.

**레버리지 순서대로** 적는다. 위에서부터 지킬 것.

| # | 무엇 | 왜 이 순서인가 |
| --- | --- | --- |
| 1 | **LLM 출력과 공지 원문을 HTML 로 렌더링하지 않는다.** `dangerouslySetInnerHTML` 금지. 마크다운을 쓴다면 **raw HTML 비활성** (`rehype-raw` 를 켜지 말 것) | React 는 기본적으로 이스케이프한다. 구멍은 이 두 곳뿐이고, 막는 비용이 거의 0이다 |
| 2 | **토큰을 `localStorage` 에 두지 않는다** (§4-3) | 저장소를 훑는 가장 흔한 공격을 무력화한다 |
| 3 | **세션 수명 12시간** (§4-2) | 털려도 유효 기간이 그만큼이다 |
| 4 | **CSP** — `Content-Security-Policy-Report-Only` 로 먼저 켜서 위반을 모으고, 정리된 뒤 강제로 전환 | XSS 가 나도 `connect-src` 로 외부 유출을 막는다. Next.js 는 인라인 스크립트를 쓰므로 nonce 설정이 필요해, 처음부터 강제하면 화면이 깨진다 |
| 5 | **웹뷰 `originWhitelist`** 로 우리 도메인만 허용, 외부 링크는 시스템 브라우저로 (§4-4) | 웹뷰 안에서 임의 사이트가 열리면 그 스크립트가 같은 컨텍스트에 들어온다 |

> 1번은 **파트 경계를 넘는 규칙**이다. 프론트(고태영)가 지켜야 하지만 어겼을 때 깨지는 것은 인증이다. CLAUDE.md §2 절대 규칙에 한 줄로 올리는 것을 제안한다 — *"외부 텍스트와 LLM 출력을 HTML 로 렌더링하지 않는다."*

---

## 8. 에러

계약서 §01 의 에러 형식을 그대로 쓴다. 아래 2개는 이 문서에서 **신설**한다.

| HTTP | code | 언제 | 신설 |
| --- | --- | --- | --- |
| 400 | `validation_failed` | `access_token` 누락 | |
| 401 | `unauthenticated` | 세션 토큰 없음·만료·이미 삭제됨 | |
| 401 | `invalid_provider_token` | 카카오 토큰 만료·무효(-401), 또는 **app_id 불일치** | 🆕 |
| 403 | `consent_required` | 계정 스코프 미동의 | |
| 404 | `not_found` | `deleted_at` 이 찍힌 parent (→ §10-1) | |
| 422 | `validation_failed` | `provider` 가 enum 밖 | |
| 502 | `oauth_provider_error` | 카카오 API 5xx·타임아웃 | 🆕 |

`invalid_provider_token` 은 **만료와 앱 불일치를 구분하지 않는다.** 구분해서 알려주면 "이 토큰은 다른 앱 것"이라는 정보를 공격자에게 준다. 초대 코드 실패를 `404 invalid_invite` 로 통일한 것과 같은 이유다.

`401 unauthenticated` 를 받은 FE 는 **SDK 재로그인 경로로 간다** (§4-2). `invalid_provider_token` 을 받으면 그 경로도 실패한 것이므로 실제 로그인 화면으로 보낸다 — 두 코드를 구분해 두는 이유가 이것이다.

`502 oauth_provider_error` 는 **기본값으로 대체하지 않는다.** 카카오가 죽었을 때 로그인을 통과시키는 경로는 존재하지 않는다 — `503 llm_unavailable` 이 모델 실패를 기본값으로 넘기지 않는 것과 같은 원칙이다.

카카오 타임아웃은 **3초**로 둔다. NF-06 의 20초는 Agent 파이프라인 예산이고, 로그인은 그 예산 밖의 단발 호출이다.

---

## 9. 테스트

노션 「보호자 권한과 동의」 §5-1 의 P-01~P-12 와 같은 층(유닛)이다. **LLM 없이 결정적으로 검증된다.**

| # | 무엇을 잡는가 | 기대 |
| --- | --- | --- |
| A-01 | 처음 보는 카카오 회원번호로 로그인 | `parent` + `auth_identity` + `session` 각 1행, `is_new: true`, `nickname: null` |
| A-02 | 같은 회원번호로 재로그인 | 기존 `parent`, `is_new: false`, **`auth_identity` 행 추가 없음**, `session` 은 새 행 |
| A-03 | 동의 전에 `POST /children` 호출 | `403 consent_required` |
| A-04 | 동의 전에 `GET /me` 호출 | **200** — 동의 화면이 부르는 경로 (§6-2) |
| A-05 | 만료된 세션 토큰으로 호출 | `401 unauthenticated` |
| A-06 | 🚨 **다른 app_id 의 카카오 토큰** | `401 invalid_provider_token`, `parent` **미생성** (§7-1) |
| A-07 | 🚨 **로그아웃 직후 같은 토큰으로 호출** | `401 unauthenticated` — **지연 없이 즉시.** §4-1 을 고른 이유 |
| A-08 | 카카오 API 가 500 을 반환 | `502 oauth_provider_error`. 로그인 통과 경로 없음 |
| A-09 | 같은 회원번호로 동시 요청 2건 | `UNIQUE` 로 `auth_identity` 한 건만 생성, 둘 다 같은 `parent_id` 응답 |
| A-10 | `POST /auth/apple` 호출 | `422 validation_failed` |
| A-11 | 로그인 후 `session` 테이블 조회 | **응답 `token` 과 같은 문자열이 어디에도 없다** (해시 저장 확인, §4-2) |
| A-12 | `parent.deleted_at` 이 찍힌 계정의 세션으로 호출 | `401` 또는 `404 not_found` — 만료를 기다리지 않는다 |

**A-06 과 A-07 이 이 목록의 이유다.** A-06 이 실패하면 인증이 없는 것과 같고, A-07 이 실패하면 JWT 대신 불투명 토큰을 고른 이유가 사라진다.

카카오 호출은 유닛 층에서 **스텁으로 대체**한다. `access_token_info` 응답 모양(`id` · `expires_in` · `app_id`)만 고정하면 A-01·A-02·A-06·A-08 이 전부 검증된다.

### 수동 확인 2건 (자동화 불가)

| # | 무엇 | 기대 | 누가 |
| --- | --- | --- | --- |
| M-01 | 웹에서 **새로고침** | 토큰이 메모리에서 사라지지만 SDK 재로그인으로 복구돼, 로그인 화면이 뜨지 않는다 (§4-3) | 고태영 |
| M-02 | M-01 에서 **kauth.kakao.com 리다이렉트가 끼는지** | 끼면 새로고침마다 화면이 튄다 → `sessionStorage` 로 타협 판단 (§4-3) | 고태영 |

배포 전 AI 보안 리뷰(고태영)에 두 항목을 추가한다 — **`/auth/*` 예외가 1개(`POST /auth/{provider}`)뿐인지**, **`dangerouslySetInnerHTML` 과 raw HTML 마크다운이 코드에 없는지** (§7-5 1번).

---

## 10. 열린 결정

| # | 무엇 | 왜 지금 못 정하나 | 누구 |
| --- | --- | --- | --- |
| 1 | **탈퇴 유예기간 중 재로그인** — 같은 카카오 계정이 다시 로그인하면 복구인가 신규인가 | 유예기간 N일(노션 논의 ⑤)이 안 정해졌다. 그 값이 정해져야 "유예 중"이라는 상태가 생긴다. 그전까지는 `404 not_found` 로 막아둔다 | 팀 · 9월 2주 |
| 2 | **토큰 저장 위치 — 메모리 vs `sessionStorage`** | M-02 결과에 달렸다. `localStorage` 금지만 확정 (§4-3) | 고태영 |
| 2-1 | **웹뷰 안에서 카카오 로그인이 되는가** | 허용 목록에 카카오 도메인을 넣고 실제로 붙여봐야 안다. 막히면 시스템 브라우저 + 앱 복귀 설계로 가야 하고 **작업량이 크게 는다** (§4-4) | 고태영 · **먼저 시험할 것** |
| 3 | **Apple 로그인 병행** | iOS 배포 시 App Store 심사 규정 확인 필요 (노션 논의 ⑭). 스키마는 이미 대비돼 있어 추가돼도 이 문서만 늘어난다 | 박재형 |
| 4 | **법정대리인 확인 방식** | 개인정보보호법 제22조의2 는 동의와 별도로 "확인" 의무를 둔다. **카카오 OAuth 로는 법정대리인 여부가 확인되지 않는다.** 현재 수단은 `consent.guardian_attested` 자기확인 체크박스뿐 (노션 논의 ①) | 팀 · **10월 3주 배포 전 필수** |

> 4번은 이 문서가 해결하지 못하는 문제다. 로그인이 아무리 정확해도 "로그인한 사람이 법정대리인인가"는 답하지 않는다. 리스크 ④가 **배포 전 필수**로 잡혀 있는 항목이라 여기 남긴다.

---

## 11. 환경 변수

`.env.example` 에 키 이름만 넣고 값은 비운다. `.env` 는 커밋하지 않는다.

```
KAKAO_APP_ID=            # 카카오 개발자 콘솔 > 앱 설정 > 앱 ID (숫자). REST API 키가 아니다
KAKAO_ADMIN_KEY=         # 어드민 키. 파기 배치에서만 사용
KAKAO_API_TIMEOUT=3      # 초
SESSION_TTL=43200        # 초 (12시간)
```

---

## 12. 계약서에 반영해야 할 것 🔶

이 문서는 계약서 **§01 공통 규약을 바꾸지 않는다.** `Authorization: Bearer <token> — 예외 없음` 이 그대로 유효하고, `token` 이 불투명 문자열이라는 정의가 추가될 뿐이다.

변경은 3건이다.

| 계약서 | 바뀌어야 할 것 |
| --- | --- |
| `POST /auth/{provider}` 응답 | **`expires_in` 추가.** 없으면 FE 가 만료를 미리 알 방법이 없다 (§3-1) |
| 인증·동의 엔드포인트 4개 | **`POST /auth/logout` 신설** — 목록에 없다. 없으면 즉시 무효화를 쓸 수 없다 (§3-2) |
| 에러 표 | `invalid_provider_token` · `oauth_provider_error` **2건 추가** (§8) |

CLAUDE.md §8 의 결정 방식상 **영역 간 인터페이스 = 관련 Owner 협의**다. 김명성(백엔드) · 고태영(프론트) 합의 후 계약서를 고친다. 합의 전까지 이 문서는 **제안 상태**로 읽을 것.

**§5-3 `session` 테이블 신설**과 **§5-2 `parent.nickname` nullable 전환**도 같은 협의 대상이다.
