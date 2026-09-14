# 카카오 OAuth 로그인 v1

문서 목적: 카카오 소셜 로그인의 요청·응답 계약, 세션 규칙, 웹·앱의 진입과 복귀, 동의 게이트와의 순서를 고정한다.

기준 브랜치: `docs/be-18-auth-kakao`
작성일: 2026-09-09
담당: 이도헌
선행 문서: [`docs/api/api-interface-v1.html`](./api-interface-v1.html) §01 공통 규약 · §04 인증·동의 — 그 문서의 인증 절을 대체한다

> 이 문서는 [#19](https://github.com/kakaotechcampus-4/ktc4-pusan-3/pull/19) 리뷰에서 고태영님 제안을 반영해 **서버 주도 흐름**으로 다시 쓴 것이다. 이전 안(클라이언트가 카카오 인가 코드를 직접 받는 방식)이 왜 안 되는지는 §2-1 에 남겼다.

---

## 0. 30초 요약

| 정한 것 | 값 |
| --- | --- |
| 로그인 시작 | **서버가 연다.** 클라이언트는 `start_url` 로 이동만 한다 (§2-2) |
| 카카오 콜백 | **서버가 받는다.** `redirect_uri` 는 **API 오리진 하나만** 콘솔에 등록 (§2-1) |
| 클라이언트가 받는 것 | 우리 서버가 발급한 **1회용 코드**. 카카오 인가 코드가 아니다 |
| 교환 | `POST /auth/{provider}` `{ code, bind }` → 세션 (§3-4) |
| `bind` | 클라이언트가 만든 비밀. **1회용 코드가 오가는 홉을 지킨다** (§7-2) |
| 신규 회원 | 세션 대신 `{ status: "consent_required", consent_code }`. **동의 전에는 `parent` 를 만들지 않는다** (§6-1) |
| 세션 토큰 | **불투명 난수** 256비트, DB 에는 SHA-256 해시. 12시간, refresh 없음 |
| 전달 | `Authorization: Bearer` — 계약서 §01 그대로 |
| 신규 테이블 | `session` · `auth_handoff` 2개 (§5-3 · §5-4) |
| 무인증 엔드포인트 | **5개.** §01 에 예외로 명시해야 한다 (§3) |

**계약서 §04 는 사실상 재작성**이다 (§12).

### 0-1. 무엇이 제약이고 무엇이 선택인가

| | 무엇 | 근거 |
| --- | --- | --- |
| **제약** | 인가 코드 방식 | 카카오 JS SDK 에 클라이언트가 토큰을 받는 메서드가 없다 (§2-1) |
| **제약** | 서버가 콜백을 받는다 | **preview 배포는 도메인이 매번 달라** 클라이언트 콜백 URL 을 콘솔에 등록할 수 없다 (§2-1) |
| **선택** | 세션을 Bearer 로 전달 | 계약서 §01 이고 `client.ts`·`sse.ts` 가 이미 그 전제 (§4-1) |
| **선택** | 불투명 토큰 (JWT 아님) | 로그아웃·탈퇴·아이 파기 3곳이 즉시 무효화를 요구 (§4-2) |
| **선택** | 동의 전 `parent` 미생성 | 계약서를 조이는 방향. 팀 결정 대상 (§6-1) |
| **선택** | 수명 12시간 · `sessionStorage` | 측정한 값이 아니다. `M-02` 후 조정 (§4-3) |

---

## 1. 범위

**다루는 것** — 로그인 시작·콜백·교환·가입·로그아웃, 웹과 앱의 복귀 경로, 세션 형식과 수명, 동의 게이트와의 순서.

**다루지 않는 것**

| 무엇 | 어디로 |
| --- | --- |
| 동의 문구·개인정보 처리방침·이용약관 | `safety/` — NF-04 확정이 선행 조건 |
| 아이 스코프 권한 (`requireConnected` · `requireOwner` · `requireWriter`) | 노션 「테크스팩 보완 제안 — 보호자 권한과 동의」 §2-1 |
| 보호자 초대·연결 | 같은 문서 §2-3 |
| 계정 탈퇴·파기 배치 | 같은 문서 §2-5. 파기 시 카카오 연결 끊기만 §7-4 에 남긴다 |
| 앱 셸의 인앱 인증 세션 구현 | 프론트 이슈 ([#26](https://github.com/kakaotechcampus-4/ktc4-pusan-3/issues/26) 에서 이관) |
| 아이 스코프 권한에서 이번에 발견한 구멍 4건 | **부록 A** — 별도 이슈 대상 |

**1차 배포는 카카오만.** `provider` enum 4종은 유지하되 카카오 외에는 구현하지 않는다 (노션 논의 ⑬). 이 흐름은 provider 별로 모양이 같아서 apple·google·naver 를 한 틀로 묶을 수 있다.

---

## 2. 흐름

### 2-1. 왜 서버가 시작하고 서버가 받는가

**① 클라이언트는 카카오 토큰을 받을 수 없다.** 카카오 JavaScript SDK 의 [`Kakao.Auth`](https://developers.kakao.com/sdk/reference/js/release/Kakao.Auth.html) 에 **토큰을 발급받는 메서드가 없다.** `authorize()` 는 인가 코드를 등록된 redirect URI 로 보낼 뿐이고, 토큰 교환은 서버 몫이다.

**② 클라이언트가 인가 코드를 받는 방식도 배포에서 막힌다.** 그러려면 **클라이언트 콜백 URL 을 카카오 콘솔에 등록**해야 하는데, 카카오는 `redirect_uri` 완전 일치를 요구하고 **preview 배포는 도메인이 매번 다르다.** 등록할 방법이 없다.

→ **`redirect_uri` 를 API 오리진 하나로 고정한다.** 서버가 시작하고 서버가 받는다.

**부수 효과 — `app_id` 대조가 필요 없다.** 서버가 자기 `client_id`·`client_secret` 으로 직접 교환하므로 **다른 앱이 발급받은 토큰이 들어올 통로 자체가 없다.**

### 2-2. 전체 흐름

```
① FE  → GET /auth/kakao/status                     ← 00 화면 진입 시 prefetch
        ← { ready: true, start_url: "https://<API>/api/v1/auth/kakao" }
        ↓
② FE    bind 비밀 생성(256비트) → sessionStorage 보관
        location = start_url + "?client=web&bind=<비밀>"
        ↓
③ 서버  bind 형식 검증 · state 난수 생성
        Set-Cookie: oauth_state   (state · client · bind 해시, 10분)
        302 → kauth.kakao.com/oauth/authorize?...&state=<state>
        ↓
④ 사용자 카카오 로그인
        ↓
⑤ 카카오 → GET /auth/kakao/callback?code=...&state=...
        ↓
⑥ 서버  state 대조 (timing-safe) → 불일치면 인가 코드 교환 전에 중단
        POST kauth/oauth/token 으로 교환 → GET access_token_info 로 회원번호
        카카오 토큰 폐기 (§7-4)
        auth_identity 조회 → auth_handoff 행 생성 (1회용 코드 · bind 해시 · TTL 2분)
        oauth_state 쿠키 만료
        302 → 복귀 URL + "?code=<1회용 코드>"        복귀 URL 은 §2-3
        ↓
⑦ FE    복귀 경로에서 code 수신
        POST /auth/kakao { code, bind }
        ↓
⑧ 서버  auth_handoff 를 DELETE … RETURNING 으로 소비 · bind 해시 대조 · 만료 확인
        기존 회원 → 200 { token, expires_in, is_new: false, parent, consent_required }
        신규      → 200 { status: "consent_required", consent_code }
        ↓
⑨ 신규만 POST /auth/kakao/signup { consent_code, bind, consents: [...] }
        → parent · auth_identity · consent 를 한 트랜잭션에 생성 → 세션
```

**⑦ 의 `code` 는 카카오 인가 코드가 아니다.** 우리 서버가 발급한 1회용 코드다. 카카오 인가 코드는 ⑥ 에서 서버가 이미 소비했다.

**⑧ 을 `access_token_info` 로 하는 이유** — 회원번호 하나만 필요하다. `/v2/user/me` 는 `kakao_account`(이메일·프로필)까지 실어 오는데 NF-04 는 최소 수집을 요구한다. 받아놓고 안 쓰는 것보다 **애초에 안 받는 것**이 낫다 — 받는 순간 `privacy_account` 동의 문구에 그 항목을 적어야 한다.

### 2-3. 🔶 앱 복귀 URL 형식

**서버가 복귀 대상을 고른다. 클라이언트는 URL 을 지정하지 못한다.** 열거값 하나만 받는다.

| 시작 파라미터 | 값 | 기본 |
| --- | --- | --- |
| `client` | `web` \| `app` | `web` |

허용값 밖이면 **시작 시점에 `400`** 으로 끊는다. 카카오 동의 화면까지 걷게 한 뒤 실패시키지 않는다.

복귀 URL 은 **서버 환경변수**에서 온다.

```
AUTH_RETURN_URL_WEB=https://<도메인>/auth/callback
AUTH_RETURN_URL_APP=yukameo://auth
```

| | 성공 | 실패 |
| --- | --- | --- |
| `client=web` | `https://<도메인>/auth/callback?code=<1회용>` | `…/auth/callback?error=<코드>` |
| `client=app` | `yukameo://auth?code=<1회용>` | `yukameo://auth?error=<코드>` |

**쿼리 모양을 웹·앱 동일하게 둔다** — 프론트가 파싱 코드를 한 벌만 만들면 된다.

**왜 `return_to` 가 아니라 `client` 인가** — 값이 URL 이 아니라 열거값이다. `return_to` 라는 이름은 URL 을 기대하게 만들고, 나중에 누군가 `return_to=https://…` 를 넣으려 한다. 그 순간 **오픈 리다이렉트**가 된다. 이름으로 막는다.

**커스텀 스킴은 카카오 콘솔에 등록하지 않는다.** `yukameo://auth` 는 **우리 서버가 302 하는 대상**이지 카카오의 `redirect_uri` 가 아니다. 카카오는 API 오리진만 안다. 앱 스킴은 `app.json` 의 `scheme: "yukameo"` 로 이미 잡혀 있다.

`client` 값은 `state` 와 함께 `oauth_state` 쿠키에 담아 콜백까지 들고 간다 (§5-5).

> **로그인 후 원래 보던 화면으로 돌아가는 것**은 서버가 관여하지 않는다. 프론트가 시작 전에 `sessionStorage` 에 두었다가 복원한다. 서버가 경로를 받으면 그것도 오픈 리다이렉트 표면이 된다.

---

## 3. 엔드포인트

경로·시간 표기는 [계약서 §01](./api-interface-v1.html)을 따른다.

🚨 **무인증 엔드포인트가 5개 생긴다.** 계약서 §01 의 "인증 `Bearer` — 예외 없음" 에 **예외 목록을 명시**해야 한다.

| 엔드포인트 | 인증 | 응답 형식 |
| --- | --- | --- |
| `GET /auth/{provider}/status` | 없음 | JSON |
| `GET /auth/{provider}` | 없음 | **302** |
| `GET /auth/{provider}/callback` | 없음 | **302** |
| `POST /auth/{provider}` | 없음 | JSON |
| `POST /auth/{provider}/signup` | 없음 | JSON |
| `POST /auth/logout` | **Bearer** | 204 |

🚨 **302 로 답하는 둘은 공통 에러 봉투를 쓸 수 없다.** 실패도 리다이렉트로 나간다 (§8-2). 이것도 §01 예외로 적는다.

### 3-1. `GET /auth/{provider}/status`

```json
{ "ready": true, "start_url": "https://<API 오리진>/api/v1/auth/kakao" }
```

| 필드 | 뜻 |
| --- | --- |
| `ready` | **이 provider 로 로그인을 시작할 수 있는 서버 설정이 갖춰졌는가.** `client_id`·`client_secret`·콜백 URL 이 모두 있으면 `true` |
| `start_url` | 시작 엔드포인트의 **절대 URL**. `KAKAO_CALLBACK_URL` 의 오리진에서 파생한다 |

**`ready` 가 필요한 이유 — 죽은 버튼을 만들지 않는다.** 설정이 안 잡힌 환경에서 "카카오로 시작하기"를 누르면 카카오 동의 화면까지 걸어간 뒤 실패하거나, 더 나쁘면 카카오 에러 페이지에 사용자가 버려진다. `ready: false` 면 프론트가 버튼을 비활성화하고 "아직 연결 전"이라고 말한다.

**`start_url` 을 서버가 내려주는 이유** — 프론트가 `NEXT_PUBLIC_API_BASE_URL` 로 조립할 수도 있지만, **그 값과 `KAKAO_CALLBACK_URL` 의 오리진이 어긋난 배포에서 `state` 쿠키가 조용히 깨진다.** 시작과 콜백이 다른 오리진이면 쿠키가 콜백에 실리지 않는다. 서버가 등록된 콜백 URL 에서 파생해 내려주면 두 값이 어긋날 수 없다.

🚨 **`missing_keys` 같은 진단 필드는 개발 환경에서만 채운다.** 프로덕션에서 무인증 엔드포인트가 "어떤 설정이 비었는지"를 알려주면 정찰에 쓰인다. 프로덕션은 `ready: false` 만 내린다.

프론트는 **00 화면 진입 시 prefetch** 한다. 버튼을 누른 뒤 조회하면 이동 전에 왕복이 한 번 낀다.

### 3-2. `GET /auth/{provider}` — 로그인 시작

| 쿼리 | 값 | 필수 |
| --- | --- | --- |
| `client` | `web` \| `app` (§2-3) | 아니오 (기본 `web`) |
| `bind` | base64url 43자 (256비트) | **예** |

서버가 하는 일 — `bind` **형식 검증** → `state` 난수 생성 → `oauth_state` 쿠키 발급 → 카카오로 302.

🚨 **`bind` 는 길이·문자셋을 검증한다.** "보냈다"만 확인하면 `bind=1` 로도 통과해서 §7-2 가 무의미해진다. 형식이 안 맞으면 **카카오 동의까지 걷게 하지 말고 여기서 거절한다.**

**응답 302** → `https://kauth.kakao.com/oauth/authorize?client_id=…&redirect_uri=…&response_type=code&state=<state>`

```
Set-Cookie: oauth_state=<…>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth; Max-Age=600
```

### 3-3. `GET /auth/{provider}/callback` — 카카오가 부른다

`?code=…&state=…`, 실패 시 `?error=…&error_description=…`.

서버가 하는 일:

1. `oauth_state` 쿠키와 쿼리 `state` 를 **timing-safe 비교**. 불일치·부재면 **인가 코드 교환 전에 중단**
2. `POST kauth/oauth/token` 으로 교환 → `GET access_token_info` 로 회원번호
3. 카카오 토큰 폐기 (§7-4)
4. `auth_identity` 조회 → `auth_handoff` 행 생성 (§5-4)
5. `oauth_state` 쿠키 만료
6. 복귀 URL 로 302 (§2-3)

```
Cache-Control: no-store
```

### 3-4. `POST /auth/{provider}` — 교환

**요청**

```json
{ "code": "<1회용 코드>", "bind": "<시작 때 만든 비밀>" }
```

계약서의 `{access_token}` 을 대체한다. **웹·앱이 같은 바디를 보낸다.**

**응답 200 — 두 갈래다 (union)**

```json
// 기존 회원
{
  "token": "Ky8vN2pRt7...",
  "expires_in": 43200,
  "is_new": false,
  "parent": { "id": "p1", "nickname": "지은" },
  "consent_required": []
}
```

```json
// 신규 — 아직 parent 가 없다 (§6-1)
{ "status": "consent_required", "consent_code": "<가입 대기표>" }
```

프론트는 **`status` 필드 유무로 분기**한다.

| 필드 | 뜻 |
| --- | --- |
| `token` | 세션 토큰. **불투명 난수** (§4-2) |
| `expires_in` | 초. **계약서에 없어서 추가한다** — 없으면 FE 가 만료를 미리 알 수 없어 매번 401 을 맞고 나서야 재로그인한다 |
| `parent.nickname` | `null` 일 수 있다 (§5-2) |
| `consent_code` | 가입 대기표. §3-5 에서 쓴다. TTL 10분 |

`Cache-Control: no-store`.

### 3-5. `POST /auth/{provider}/signup` — 🔶 신설

**요청**

```json
{
  "consent_code": "<가입 대기표>",
  "bind": "<같은 비밀>",
  "consents": [
    { "scope": "service_terms",   "policy_version": "2026-09-01" },
    { "scope": "privacy_account", "policy_version": "2026-09-01" }
  ]
}
```

**응답 200** — §3-4 의 기존 회원 응답과 같은 모양 (`is_new: true`).

`parent` · `auth_identity` · `consent` 를 **한 트랜잭션에서** 만든다. 필수 스코프가 빠지면 `403 consent_required`.

`bind` 를 여기서도 요구한다 — 클라이언트가 계속 들고 있으므로 비용이 없고, `consent_code` 만으로 계정이 만들어지는 것을 막는다.

### 3-6. `POST /auth/logout`

요청 바디 없음. `Authorization` 헤더로 세션을 식별한다. **응답 204** — `session` 행을 삭제한다.

**계약서 28개 목록에 이것이 없다.** 없으면 클라이언트가 토큰을 버리는 것으로 흉내낼 뿐이고 서버에서는 만료까지 유효하다.

카카오 쪽 로그아웃(`POST /v1/user/logout`)은 **부르지 않는다.** 우리 서비스에서 나가는 것과 카카오 계정에서 로그아웃하는 것은 다른 행위다.

---

## 4. 세션

### 4-1. 계약서 §01 과 기존 프론트 코드를 그대로 쓴다

세션은 `Authorization: Bearer` 로 보낸다. 이 문서가 `token` 을 불투명 문자열로 정의하는 것으로 §01 이 그대로 성립한다.

프론트가 이미 이 전제로 구현돼 있다 — `client.ts` 가 Bearer 를 붙이고, `sse.ts` 는 **`EventSource` 를 쓰지 않고** fetch 스트리밍으로 돌린다. `apps/web/CLAUDE.md` §3 이 이유를 적어뒀다:

> `EventSource` 를 쓰지 않는다. Authorization 헤더를 못 붙여서 NF-09 를 깬다.

세션을 쿠키로 옮기면 `client.ts` 와 `sse.ts` 를 둘 다 고쳐야 한다.

> **쿠키를 아예 안 쓰는 것은 아니다.** `oauth_state` 는 쿠키다(§5-5). 카카오 왕복 동안만 살고 콜백에서 소비된다. **세션 전달**에만 쓰지 않는다.

### 4-2. 왜 JWT 가 아닌가

이 서비스는 **즉시 무효화**를 세 곳에서 요구한다 — 로그아웃, 계정 탈퇴(`parent.deleted_at`), 아이 파기(`410 child_deleted`). stateless JWT 는 서명이 유효한 동안 서버가 거절할 방법이 없다. 아동 정보를 다루면서 그 창을 만들 이유가 없다.

DB 조회 1회가 JWT 의 이점이지만 세션 조회는 PK 인덱스 단건 읽기라 실질 비용이 거의 없다. JWT 의 다른 이점(서비스 간 stateless 전달)도 해당되지 않는다 — CLAUDE.md §6 이 백엔드와 AI 를 **한 서비스로 묶기로** 정했고 Agent 는 같은 프로세스의 폴더다. **토큰을 건네줄 상대가 없다.**

### 4-3. 토큰과 수명

| | 값 |
| --- | --- |
| 생성 | CSPRNG 256비트 → base64url (43자) |
| DB 저장 | **SHA-256 해시.** 원문은 저장하지 않는다 |
| 수명 | **12시간** |
| 만료 시 | `401 unauthenticated` → FE 가 §2-2 를 다시 탄다 |
| 클라이언트 저장 | **`sessionStorage`** 의 `auth.token` (§5-6). `localStorage` 금지 (§7-7 2번) |

**refresh 토큰을 만들지 않는다.** 카카오 세션이 살아 있으면 authorize 가 화면 없이 통과한다 — 카카오가 이를 위해 `prompt=none` 을 제공한다(세션이 있으면 즉시 코드 발급, 없으면 `consent_required` 에러). 재로그인 자체가 조용하다.

> ⚠️ **숨긴 iframe 갱신은 쓸 수 없다.** 브라우저의 서드파티 쿠키 차단 때문에 iframe 안에서 카카오 쿠키가 실리지 않는다. 웹은 전체 페이지 리다이렉트를, 앱은 인앱 인증 세션을 탄다. 체감은 `M-02` 로 확인하고 12시간을 조정한다.

**🚨 현재 코드가 저장 규칙을 어기고 있다.** [`apps/web/src/stores/session.ts`](../../apps/web/src/stores/session.ts) 가 Zustand persist 로 토큰을 **localStorage** 에 영속 저장한다. `createJSONStorage(() => sessionStorage)` 로 바꾼다. `activeChildId` 는 계속 저장해도 된다 — 화면 선택값일 뿐 권한 근거가 아니고, `/me` 결과로 유효성을 다시 확인한다.

### 4-4. 보호자 표시 이름

`parent.nickname` 은 **카카오에서 가져오지 않는다** (노션 논의 ⑫). 온보딩에서도 받지 않는다 — 보호자 닉네임을 받는 화면은 **10 설정의 `PATCH /parents`** 뿐이다.

**값이 없으면 화면이 "보호자"로 폴백한다.** 근거는 §5-2.

---

## 5. 스키마

**저장 규칙 한 장.** 흐름에 등장하는 비밀은 네 개다. 아래 절들은 전부 이 표의 적용이다.

| 값 | 만드는 쪽 | 원문이 있는 곳 | 해시가 있는 곳 | 서버 DB 원문 |
| --- | --- | --- | --- | --- |
| `state` | 백엔드 | 브라우저 쿠키 | 없음 — 서버가 저장하지 않는다 (§5-5) | 없음 |
| `bind` | 프론트 | 프론트 `sessionStorage` (§5-6) | `oauth_state` 쿠키 → `auth_handoff.bind_hash` | 없음 |
| 1회용 코드 | 백엔드 | 복귀 URL 쿼리 (일시) | `auth_handoff.code_hash` | 없음 |
| 세션 토큰 | 백엔드 | 프론트 `sessionStorage` (§5-6) | `session.token_hash` | 없음 |
| 카카오 access token | 카카오 | — | — | 폐기 (§7-4) |

**서버는 어떤 비밀도 원문으로 보관하지 않는다.** 대조가 필요한 값은 받은 값을 그때그때 해시해 저장된 해시와 비교한다. DB 덤프가 유출돼도 살아 있는 자격증명이 되지 않게 하려는 것이다.

`state` 만 해시조차 하지 않는다. 저장소가 **브라우저 쿠키**이고 서버는 아무것도 들고 있지 않아서, 비교할 두 값(쿼리 `state` 와 쿠키 `state`)이 **모두 같은 요청에 실려 온다.**

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

### 5-2. 🚨 `parent.nickname` 은 nullable 이어야 한다

같은 브랜치의 `Parent.nickname` 이 **`nullable=False`** 인데, 이 흐름에서는 값을 채울 시점이 없다.

`POST /auth/{provider}/signup` 이 `parent` 를 만드는데 그때 받는 것은 **동의뿐**이다. 보호자 닉네임은 카카오에서 가져오지 않고(논의 ⑫), 동의 화면에서도 받지 않는다.

| 안 | 판정 |
| --- | --- |
| **`nickname` 을 nullable 로** | ✅ 10 설정에서 채운다. "아직 입력 안 함"이 `NULL` 로 표현된다 |
| 동의 화면에서 함께 받는다 | ✕ **동의의 명확성이 흐려진다.** 법적 고지를 읽고 체크하는 화면에 무관한 입력이 같은 제출 버튼에 묶이면 "무엇에 동의한 것인가"를 다투기 나빠진다. 리스크 ④(법정대리인 동의)가 걸린 화면이라 특히 그렇다 |
| 빈 문자열 | ✕ "아직 입력 안 함"과 "빈 이름이 잘못 저장됨"을 구분할 수 없고, `length > 0` CHECK 제약도 걸 수 없다 |
| 카카오 프로필 닉네임을 기본값으로 | ✕ NF-04 최소 수집과 부딪힌다 |

**계약서와 프론트가 이미 nullable 이다** — §04 응답 예시가 `parent: { id: "p1", nickname: null }` 이고 `apps/web/src/lib/api/types.ts` 도 `string | null` 이다. NOT NULL 로 두면 그 둘을 함께 되돌려야 한다.

→ **PR #8 이 develop 에 머지되기 전이면 모델과 마이그레이션을 같이 고치면 되고 새 리비전이 안 붙는다.** ([PR #8 코멘트](https://github.com/kakaotechcampus-4/ktc4-pusan-3/pull/8#pullrequestreview-5148979830))

### 5-3. `session` — 신설 🔶 협의 대상

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

**폐기는 행 삭제로 한다.** 세션은 증빙이 아니다. `consent` 가 append-only 인 것과 정반대로, 남은 행은 유출 표면일 뿐이다.

| 언제 | 무엇을 지우나 |
| --- | --- |
| 로그아웃 | 그 행 1건 |
| 계정 탈퇴 · 아이 파기 | `parent_id` 로 전부 |
| 만료 | 배치로 `expires_at < now()` 전부 |

### 5-4. `auth_handoff` — 신설 🔶 협의 대상

1회용 코드를 담는다. **Redis 가 아니라 Postgres 로 한다.**

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK |
| `code_hash` | bytea | **UNIQUE, NOT NULL.** SHA-256(1회용 코드) |
| `bind_hash` | bytea | NOT NULL. SHA-256(클라이언트 비밀) (§7-2) |
| `provider` | enum | NOT NULL |
| `parent_id` | uuid | FK. **기존 회원일 때만** |
| `provider_user_id` | text | **신규일 때만.** signup 에서 `auth_identity` 를 만들 때 쓴다 |
| `expires_at` | timestamptz | NOT NULL. 교환용 2분 · 가입 대기표 10분 |
| `created_at` | timestamptz | NOT NULL, default now() |

```sql
CREATE UNIQUE INDEX ON auth_handoff (code_hash);
CREATE INDEX ON auth_handoff (expires_at);
```

`CHECK`: `parent_id` 와 `provider_user_id` 중 **정확히 하나**만 NOT NULL.

**원자적 소비는 `DELETE … RETURNING`** 으로 한다. Redis `GETDEL` 과 같은 보장이고 트랜잭션 안에 들어간다.

```sql
DELETE FROM auth_handoff
WHERE code_hash = :code_hash
  AND expires_at > now()
RETURNING *;
```

| 요청 | 결과 |
| --- | --- |
| 첫 번째 | 행을 삭제하며 반환 → 성공 |
| 두 번째 | 행이 이미 없다 → `401 invalid_handoff` (A-04) |
| 만료 후 | `expires_at > now()` 에 걸린다 → `401 invalid_handoff` (A-12) |

한 문장이라 **"조회했는데 그 사이 남이 썼다"는 틈이 없다.** 먼저 `SELECT` 하고 나중에 `DELETE` 하면 그 틈이 생긴다.

반환된 행의 `bind_hash` 를 받은 `bind` 원문의 해시와 비교한다. **불일치면 소비된 것으로 두고 실패시킨다** — 롤백해서 재시도 기회를 주지 않는다 (§7-2).

**Postgres 로 하는 근거** — 로그인 빈도에 TTL 2분이면 동시 존재 행이 사실상 없다. 인프라가 t3.medium 1대(4GB)에 DB·백엔드·프론트를 함께 올리는 구성이라 Redis 를 지금 얹을 이유가 없다. 나중에 Redis 가 들어오면 이 테이블만 옮기면 된다.

만료 행은 배치로 지운다. **배치가 늦어도 위 SQL 의 `expires_at > now()` 가 막는다** — 배치는 청소지 방어가 아니다.

### 5-5. `oauth_state` 는 쿠키다 — 테이블을 만들지 않는다

카카오 왕복(③↔⑤) 동안만 살고 콜백에서 소비된다. 10분짜리 값이라 테이블을 두면 만료 정리 배치가 하나 더 생기고 로그인 시도마다 쓰기가 발생한다.

담는 것 — `state` 난수 · `client`(§2-3) · `bind` 해시.

**서버는 `state` 를 어디에도 저장하지 않는다. 쿠키가 서버 대신 값을 들고 있다.** 그래서 로그인 시작마다 쓰기가 발생하지 않고, **카카오 동의 화면에서 이탈한 시도는 흔적을 남기지 않는다.** 행이 생기는 것은 카카오 인증이 실제로 끝난 뒤(`auth_handoff`)뿐이다.

쿠키가 노출돼도 `bind` 원문은 얻을 수 없다 — 해시만 담는다.

```
Set-Cookie: oauth_state=<…>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth; Max-Age=600
```

`SameSite=Lax` 쿠키는 **크로스 사이트 최상위 GET 이동**에 실린다. 카카오 → 콜백이 그 경우라 정상 동작한다. `Path` 를 `/api/v1/auth` 로 좁혀 다른 요청에 실리지 않게 한다.

🚨 **시작과 콜백이 같은 오리진이어야 한다.** 다르면 쿠키가 콜백에 실리지 않는다. `start_url` 을 서버가 `KAKAO_CALLBACK_URL` 에서 파생해 내려주는 이유가 이것이다 (§3-1). **로컬은 포트가 달라도 쿠키가 공유돼 우연히 통과하니 배포 기준으로 확인해야 한다.**

### 5-6. 프론트 `sessionStorage` — 클라이언트가 들고 있는 두 값

서버 테이블은 아니지만 **대조의 한쪽 끝이라 여기서 같이 고정한다.**

| 키 | 값 | 언제 넣나 | 언제 지우나 |
| --- | --- | --- | --- |
| `auth.bind` | `bind` **원문** | 로그인 시작 직전 (§2-2 ②) | 교환·가입 성공 직후 |
| `auth.token` | 세션 토큰 **원문** | 교환·가입 성공 직후 | 로그아웃 시 |

탭을 닫으면 사라진다. 앱도 같다 — WebView 가 사는 동안 유지되고 앱을 완전히 종료하면 사라진다. `localStorage` 를 쓰지 않는 이유는 §7-7.

**`bind` 는 교환에 성공하면 바로 지운다.** 남겨둘 이유가 없고, 남은 값은 다음 로그인에서 재사용될 위험만 만든다.

### 5-7. 만료 한눈에

| 값 | 수명 | 어디에 박히나 | 어떻게 사라지나 |
| --- | --- | --- | --- |
| `oauth_state` 쿠키 | **10분** | 쿠키 `Max-Age=600` | 콜백에서 검사 후 즉시 만료 (§7-1) |
| `bind` 원문 | 교환까지 | 없음 — 프론트가 판단 | 교환 성공 후 `sessionStorage` 에서 제거 |
| 1회용 코드 | **2분** | `auth_handoff.expires_at` | 교환 시 `DELETE`, 또는 배치 |
| 가입 대기표 | **10분** | 같은 컬럼 | signup 시 `DELETE`, 또는 배치 |
| 세션 토큰 | **12시간** | `session.expires_at` | 로그아웃 · 탈퇴 · 만료 배치 |
| 카카오 access token | — | 저장하지 않음 | 회원번호 조회 직후 폐기 |
| 카카오 인가 코드 | 카카오가 정함 | 저장하지 않음 | 교환 즉시 무효 |

🔶 **수명 숫자 중 측정으로 정한 것은 없다.** 12시간은 `M-02` 뒤에 조정한다 (§10 3번).

---

## 6. 동의 게이트

### 6-1. 🔶 동의 전에는 `parent` 를 만들지 않는다 — 팀 결정 대상

계약서 §04 는 첫 로그인에 `parent` 를 만들고 `consent_required` 로 화면만 막는다. 그러면 **사용자가 동의 화면에서 이탈했을 때 동의하지 않은 계정이 DB 에 남는다.**

CLAUDE.md 의 "필수 동의가 비어 있으면 그 아래 어떤 저장도 일어나지 않는다"를 **계정 생성에도 적용한다.** 계정 생성도 저장이다.

| | 기존 회원 | 신규 |
| --- | --- | --- |
| `POST /auth/{provider}` | 세션 발급 | `{ status: "consent_required", consent_code }` — **아직 아무것도 안 만든다** |
| `POST /auth/{provider}/signup` | — | `parent` · `auth_identity` · `consent` 를 **한 트랜잭션에** |

**계약서를 조이는 방향이라 팀 결정이 필요하다.** 나중에 풀기는 쉽고 조이기는 어려운 항목이다.

### 6-2. 미들웨어 순서 (NF-11)

```
① authenticate            Bearer 토큰 → SHA-256 → session 조회 → parent_id
② requireAccountConsent   service_terms · privacy_account 최신 행이 granted 인가
③ requireConnected / requireOwner / requireWriter    아이 스코프
④ requireChildConsent(scope)   child_basic · child_health
```

| 경로 | 통과해야 하는 것 |
| --- | --- |
| `/auth/*` (§3 의 무인증 5개) | 없음 |
| `POST /auth/logout` | ① |
| `GET /me` · `POST /consents` · `GET /consents` | ① |
| 그 외 전부 | ① ② + (아이 스코프면) ③ ④ |

`GET /me` 와 `/consents` 가 ② 를 건너뛰는 이유 — 아이 단위 동의 화면이 이 둘을 부른다. 계정 단위 동의는 §6-1 에서 이미 끝나 있다.

핸들러가 직접 권한 쿼리를 쓰지 않는다. 미들웨어 한 곳에서만 판정한다.

**CSRF 방어는 필요 없다.** 세션을 쿠키로 보내지 않으므로 브라우저가 자동으로 자격증명을 붙이지 않는다.

---

## 7. 보안 규칙

### 7-1. `state` — 카카오 왕복을 지킨다

**없으면 무슨 일이 생기나.** 공격자가 자기 카카오 계정으로 로그인을 시작해 얻은 인가 코드로 콜백 URL 을 만들어 피해자에게 열게 한다. 서버는 그것을 정상 로그인으로 처리하고 **피해자 브라우저가 공격자 계정으로 로그인된다.** 그 뒤 피해자가 입력하는 아이 정보가 전부 공격자 계정에 쌓인다. **카카오 왕복 구간(③↔⑤)의 로그인 CSRF** 다 — 같은 결과를 ⑥→⑦ 홉에서 노리는 공격은 §7-2 가 막는다.

콜백에서 비교하는 두 값의 **출처가 다르다는 것이 방어의 전부**다.

```http
GET /auth/kakao/callback?code=<카카오 인가 코드>&state=<카카오가 되돌려준 값>
Cookie: oauth_state=<우리가 ③ 에서 심은 값>
```

쿼리는 **카카오**가 붙였고 `Cookie` 헤더는 **브라우저**가 붙였다. 프론트 코드는 관여하지 않는다.

지켜야 할 것이 셋이다.

1. **timing-safe 비교를 쓴다.** 앞에서부터 비교하다 다른 글자에서 멈추면 걸린 시간이 값을 흘린다.
2. **불일치·부재면 인가 코드 교환 전에 끊는다.** 교환부터 하고 나중에 검사하면 공격자의 코드가 이미 소비된 뒤다 (A-08).
3. **검사가 끝나면 쿠키를 즉시 만료시킨다.** 성공이든 실패든 한 번 쓰고 버린다.

### 7-2. 🚨 `bind` — 1회용 코드가 오가는 홉을 지킨다

`state` 는 카카오 왕복만 지킨다. **서버 → 클라이언트로 1회용 코드가 돌아오는 홉(⑥→⑦)은 못 막는다.**

없으면 공격자가 **자기 카카오 로그인으로 얻은 1회용 코드**를 링크나 딥링크로 피해자에게 던져 **피해자를 공격자 계정에 로그인**시킬 수 있다. 그 뒤 피해자가 입력하는 아이 정보가 전부 공격자 계정에 쌓인다.

막는 방법 — 클라이언트가 시작할 때 랜덤 비밀(256비트)을 `bind` 로 싣고, 서버는 그 **해시**를 `auth_handoff` 에 묶는다. 교환(§3-4)과 가입(§3-5)에서 같은 값을 다시 제시해야 세션을 내준다.

**PKCE 를 우리 서버-클라이언트 홉에 적용한 것**이다. 카카오 쪽 검증(`state`)과는 별개이고, 둘 다 필요하다.

🚨 **형식 검증을 시작 시점에 한다** (§3-2). "보냈다"만 확인하면 `bind=1` 로도 통과해 이 절이 무의미해진다.

### 7-3. 오픈 리다이렉트를 만들지 않는다

복귀 대상은 **서버 환경변수에서 온다.** 클라이언트는 `client=web|app` 열거값만 고른다 (§2-3). 허용값 밖이면 시작 시점에 `400`.

로그인 후 원래 화면 복원도 서버가 관여하지 않는다 — 프론트가 `sessionStorage` 로 처리한다.

### 7-4. 저장하지 않는 것

| 무엇 | 왜 |
| --- | --- |
| 카카오 access_token · refresh_token | 교환 직후 회원번호만 얻고 폐기한다. 카카오 API 를 더 부르지 않으므로 보관하면 유출 표면만 는다 |
| 이메일 · 프로필 이미지 · 닉네임 | NF-04 최소 수집. `access_token_info` 는 주지도 않는다 |
| **세션 토큰 · 1회용 코드 · `bind` 원문** | 전부 해시만 저장한다 |
| 카카오 인가 코드 | 1회용이고 교환 즉시 무효다 |

**예외 — 파기 배치.** 아이·계정 파기 시 카카오 연결을 끊어야 하는데 토큰이 없으므로 어드민 키로 대상을 지정한다.

```
POST https://kapi.kakao.com/v1/user/unlink
Authorization: KakaoAK {SERVICE_APP_ADMIN_KEY}
Content-Type: application/x-www-form-urlencoded;charset=utf-8

target_id_type=user_id&target_id={provider_user_id}
```

어드민 키는 앱 전체 권한이다. **서버 .env 에만 두고 파기 배치 외 어떤 경로에서도 부르지 않는다.**

### 7-5. 로그

**카카오 회원번호·인가 코드·카카오 토큰·1회용 코드·`bind`·세션 토큰을 남기지 않는다.** 식별이 필요하면 `parent_id` 만 쓴다. NF-05 의 "원문 대신 `memory_id`"를 인증 영역으로 확장한 것이다.

🚨 **콜백 URL 전체를 로깅하지 않는다** — 쿼리에 인가 코드가 들어 있다. 복귀 URL 도 마찬가지로 1회용 코드가 실린다. 카카오 호출 실패는 상태 코드와 카카오 에러 코드만 남긴다.

### 7-6. 비밀 관리 (NF-09)

| 값 | 어디에 | 클라이언트 번들 |
| --- | --- | --- |
| `KAKAO_REST_API_KEY` · `KAKAO_CLIENT_SECRET` · `KAKAO_ADMIN_KEY` | 서버 .env | ✕ |
| `AUTH_RETURN_URL_WEB` · `AUTH_RETURN_URL_APP` | 서버 .env | ✕ |

불투명 토큰에는 **서명 키가 없다.**

**프론트도 앱 셸도 카카오 키를 알 필요가 없다.** 서버가 시작 URL 을 내려주므로 `NEXT_PUBLIC_KAKAO_*` 가 필요 없고, 셸은 그 URL 을 열기만 한다 — `apps/mobile/CLAUDE.md` §4 의 "셸이 아는 비밀은 없어야 정상"이 그대로 유지된다.

🚨 **이 저장소는 public 이다** (CLAUDE.md §9). 1단계에서 학생 API 토큰 8건이 실제로 유출됐고, 노트북·`docs/*.md` 본문에서도 나왔다. **어떤 문서에도 실제 키를 붙여넣지 않는다** — 특히 §11 환경변수 표는 값을 채우고 싶어지는 자리다. 한 번 커밋된 비밀은 지워도 히스토리에 남으므로 **유일한 조치는 폐기(rotate)** 이고, 실수했다면 즉시 담임 매니저에게 알린다.

### 7-7. XSS 방어 — 헤더 방식을 고른 대가

토큰이 JS 에서 접근 가능하므로 **XSS 한 건이 세션을 넘긴다.**

이 서비스의 XSS 는 막연한 위험이 아니라 **경로가 특정된다** — 기관 공지 붙여넣기·OCR 로 들어온 **외부 텍스트가 LLM 을 거쳐 화면에 렌더링**된다 (F-07 · F-14).

| # | 무엇 | 왜 이 순서인가 |
| --- | --- | --- |
| 1 | **LLM 출력과 공지 원문을 HTML 로 렌더링하지 않는다.** `dangerouslySetInnerHTML` 금지, 마크다운은 raw HTML 비활성 (`rehype-raw` 금지) | React 는 기본적으로 이스케이프한다. 구멍은 이 둘뿐이고 막는 비용이 거의 0이다 |
| 2 | **토큰을 `localStorage` 에 두지 않는다** (§4-3) | 저장소를 훑는 가장 흔한 공격을 무력화한다 |
| 3 | **세션 수명 12시간** | 털려도 유효 기간이 그만큼이다 |
| 4 | **CSP** — `Report-Only` 로 위반을 모은 뒤 강제로 전환 | XSS 가 나도 `connect-src` 로 외부 유출을 막는다. Next.js 는 인라인 스크립트를 쓰므로 처음부터 강제하면 화면이 깨진다 |

> 1번은 **파트 경계를 넘는 규칙**이다. 프론트가 지키지만 어겼을 때 깨지는 것은 인증이다. CLAUDE.md §2 절대 규칙에 한 줄로 올리는 것을 제안한다 — *"외부 텍스트와 LLM 출력을 HTML 로 렌더링하지 않는다."*

---

## 8. 에러

### 8-1. JSON 엔드포인트 — 계약서 §01 봉투를 쓴다

| HTTP | code | 언제 | 신설 |
| --- | --- | --- | --- |
| 400 | `validation_failed` | `code`·`bind` 누락, `bind` 형식 불량 | |
| 401 | `invalid_handoff` | 1회용 코드가 없음·만료·이미 사용됨, 또는 **`bind` 불일치** | 🆕 |
| 401 | `unauthenticated` | 세션 토큰 없음·만료·이미 삭제됨 | |
| 403 | `consent_required` | 필수 동의 스코프가 빠짐 (§3-5) | |
| 404 | `not_found` | `deleted_at` 이 찍힌 parent (→ §10-1) | |
| 422 | `validation_failed` | `provider` 가 enum 밖 | |
| 502 | `oauth_provider_error` | 카카오 API 5xx·타임아웃, 코드 교환 실패 | 🆕 |

`invalid_handoff` 는 **실패 사유를 구분하지 않는다.** 만료·재사용·`bind` 불일치를 구분해 알려주면 공격자에게 정보를 준다. 초대 코드 실패를 `404 invalid_invite` 로 통일한 것과 같은 이유다.

`502 oauth_provider_error` 는 **기본값으로 대체하지 않는다.** 카카오가 죽었을 때 로그인을 통과시키는 경로는 존재하지 않는다 — `503 llm_unavailable` 이 모델 실패를 기본값으로 넘기지 않는 것과 같은 원칙이다.

카카오 타임아웃은 **3초**로 둔다. NF-06 의 20초는 Agent 파이프라인 예산이고 로그인은 그 예산 밖이다.

### 8-2. 🚨 리다이렉트 엔드포인트 — 봉투를 쓸 수 없다

`GET /auth/{provider}` 와 `/callback` 은 302 로 답한다. **실패도 리다이렉트로 나가므로 공통 에러 봉투가 성립하지 않는다.** 계약서 §01 에 예외로 적어야 한다.

복귀 URL 에 **에러 코드만** 싣는다.

```
https://<도메인>/auth/callback?error=invalid_state
yukameo://auth?error=oauth_denied
```

🚨 **문구를 싣지 않는다.** 서버 메시지를 URL 에 그대로 실으면 **공격자가 프론트 화면에 임의 문구를 띄우는 통로**가 된다(피싱 문구 주입). 그리고 문구를 서버가 정하면 프론트가 화면 톤을 맞출 수 없다. **사용자에게 보일 문장은 프론트가 만든다.**

| `error` | 언제 |
| --- | --- |
| `invalid_state` | `state` 불일치·부재·만료 |
| `invalid_client` | `client` 값이 허용값 밖 (§2-3) |
| `invalid_bind` | `bind` 형식 불량 (§3-2) |
| `oauth_denied` | 사용자가 카카오 동의 화면에서 취소 |
| `oauth_provider_error` | 카카오 API 실패 |

---

## 9. 테스트

노션 「보호자 권한과 동의」 §5-1 의 P-01~P-12 와 같은 층(유닛)이다. **LLM 없이 결정적으로 검증된다.**

| # | 무엇을 잡는가 | 기대 |
| --- | --- | --- |
| A-01 | 처음 보는 회원번호 → 교환 | `{ status: "consent_required", consent_code }`, **`parent` 미생성** |
| A-02 | 이어서 signup | `parent`·`auth_identity`·`consent` 각 1행, 세션 발급, `is_new: true` |
| A-03 | 기존 회원 → 교환 | 바로 세션. `auth_identity` 행 추가 없음 |
| A-04 | 🚨 **같은 1회용 코드를 두 번 교환** | 두 번째 `401 invalid_handoff` |
| A-05 | 🚨 **`bind` 를 틀리게 보내 교환** | `401 invalid_handoff`, 세션 미발급 (§7-2) |
| A-06 | `bind` 없이 시작 (`GET /auth/kakao`) | `302 …?error=invalid_bind` — **카카오로 보내지 않는다** |
| A-07 | `bind=1` 처럼 형식 불량으로 시작 | 같음 (§3-2) |
| A-08 | 🚨 **`state` 불일치로 콜백** | `302 …?error=invalid_state`, **인가 코드 교환을 시도하지 않는다** |
| A-09 | `oauth_state` 쿠키 없이 콜백 | 같음 |
| A-10 | `client` 가 허용값 밖 | `302 …?error=invalid_client` |
| A-11 | `client=app` 로 시작 | 복귀가 `yukameo://auth?code=…` (§2-3) |
| A-12 | 만료된 1회용 코드 | `401 invalid_handoff` |
| A-13 | signup 에서 필수 스코프 누락 | `403 consent_required`, **`parent` 미생성** |
| A-14 | 만료된 세션 토큰으로 호출 | `401 unauthenticated` |
| A-15 | 🚨 **로그아웃 직후 같은 토큰으로 호출** | `401 unauthenticated` — **지연 없이 즉시** (§4-2) |
| A-16 | 카카오 API 가 500 | `302 …?error=oauth_provider_error`, `parent` 미생성 |
| A-17 | 사용자가 카카오에서 취소 | `302 …?error=oauth_denied` |
| A-18 | 저장 확인 | `session`·`auth_handoff` 어디에도 **원문 문자열이 없다** (해시 저장) |
| A-19 | `parent.deleted_at` 이 찍힌 계정의 세션 | `401` 또는 `404 not_found` — 만료를 기다리지 않는다 |
| A-20 | `ready: false` 환경에서 `/status` | 프로덕션 모드면 **`missing_keys` 가 응답에 없다** (§3-1) |

**A-05 · A-08 · A-15 가 이 목록의 이유다.** A-05 가 실패하면 §7-2 가 무의미하고, A-08 이 실패하면 로그인 CSRF 가 열리며, A-15 가 실패하면 JWT 대신 불투명 토큰을 고른 이유가 사라진다.

카카오 호출은 유닛 층에서 **스텁으로 대체**한다. `POST /oauth/token` 과 `access_token_info` 두 응답 모양만 고정하면 A-01·A-03·A-16 이 검증된다.

### 수동 확인 2건 (자동화 불가)

| # | 무엇 | 누가 |
| --- | --- | --- |
| M-01 | 🚨 **시작과 콜백이 같은 오리진인지 배포 환경에서 확인.** 다르면 `state` 쿠키가 콜백에 실리지 않는다. **로컬은 포트가 달라도 우연히 통과한다** (§5-5) | 김명성 · 고태영 |
| M-02 | **앱에서 로그인 → `yukameo://auth` 복귀 → 웹뷰 세션 생성** — 실기기 iOS·Android 각각 | 고태영 |

배포 전 AI 보안 리뷰(고태영)에 세 항목을 추가한다 — **무인증 엔드포인트가 §3 의 5개뿐인지**, **복귀 URL 에 문구가 실리지 않는지**(§8-2), **`dangerouslySetInnerHTML` 과 raw HTML 마크다운이 코드에 없는지**(§7-7 1번).

---

## 10. 열린 결정

| # | 무엇 | 왜 지금 못 정하나 | 누구 |
| --- | --- | --- | --- |
| 1 | **탈퇴 유예기간 중 재로그인** — 복구인가 신규인가 | 유예기간 N일(노션 논의 ⑤)이 미정. 그전까지 `404 not_found` 로 막아둔다 | 팀 · 9월 2주 |
| 2 | **동의 전 `parent` 미생성** (§6-1) | 계약서를 조이는 방향이라 팀 결정이 필요하다. 나중에 풀기는 쉽고 조이기는 어렵다 | 김명성 · 박재형 |
| 3 | **세션 수명 12시간이 맞는지** | `M-02` 결과에 달렸다. 측정한 값이 아니다 | 고태영 |
| 4 | **Apple 로그인 병행** | iOS 배포 시 App Store 심사 규정 확인 필요 (논의 ⑭). 이 흐름은 provider 별 모양이 같아 추가가 쉽다 | 박재형 |
| 5 | **법정대리인 확인 방식** | 개인정보보호법 제22조의2 는 동의와 별도로 "확인" 의무를 둔다. **카카오 OAuth 로는 확인되지 않는다.** 현재 수단은 `consent.guardian_attested` 자기확인뿐 (논의 ①) | 팀 · **10월 3주 배포 전 필수** |

> 5번은 이 문서가 해결하지 못하는 문제다. 로그인이 아무리 정확해도 "로그인한 사람이 법정대리인인가"는 답하지 않는다.

---

## 11. 환경 변수

`.env.example` 에 키 이름만 넣고 값은 비운다. `.env` 는 커밋하지 않는다.

**서버 (`apps/api`)**

```
KAKAO_REST_API_KEY=          # 콘솔 > 앱 키 > REST API 키
KAKAO_CLIENT_SECRET=         # 콘솔 > 카카오 로그인 > 보안 에서 활성화 후 발급
KAKAO_CALLBACK_URL=          # API 오리진 절대 URL. 콘솔 등록값과 정확히 일치 (운영·로컬 각각)
KAKAO_ADMIN_KEY=             # 어드민 키. 파기 배치에서만 사용
KAKAO_API_TIMEOUT=3          # 초
AUTH_RETURN_URL_WEB=         # 예: https://<도메인>/auth/callback
AUTH_RETURN_URL_APP=         # 예: yukameo://auth
SESSION_TTL=43200            # 초 (12시간)
HANDOFF_TTL=120              # 초 (2분)
SIGNUP_TICKET_TTL=600        # 초 (10분)
OAUTH_STATE_TTL=600          # 초 (10분)
```

**웹 (`apps/web`) · 앱 (`apps/mobile`)** — **추가 없음.** 서버가 `start_url` 을 내려주므로 카카오 키를 알 필요가 없다 (§7-6).

---

## 12. 계약서에 반영해야 할 것 🔶

계약서 **§04 인증·동의는 사실상 재작성**이다. §01 도 손대야 한다.

| | 무엇 |
| --- | --- |
| **신설** | `GET /auth/{provider}/status` · `GET /auth/{provider}` · `GET /auth/{provider}/callback` · `POST /auth/{provider}/signup` · `POST /auth/logout` |
| **변경** | `POST /auth/{provider}` — 요청 `{access_token}` → `{code, bind}`, 응답이 **union** (세션 또는 `consent_required`), `expires_in` 추가 |
| **§01 예외 ①** | 인증 없는 엔드포인트 **5개를 명시** (§3) |
| **§01 예외 ②** | **302 엔드포인트 2개는 공통 에러 봉투를 쓰지 않는다** (§8-2) |
| **에러** | `invalid_handoff` · `oauth_provider_error` 추가. 리다이렉트 에러 코드 5종 (§8-2) |

CLAUDE.md §8 의 결정 방식상 **영역 간 인터페이스 = 관련 Owner 협의**다. 김명성(백엔드) · 고태영(프론트) 합의 후 계약서를 고친다. **합의 전까지 이 문서는 제안 상태로 읽을 것.**

**§5-3 `session` · §5-4 `auth_handoff` 신설**과 **§5-2 `parent.nickname` nullable 전환**, **§6-1 동의 전 `parent` 미생성**도 같은 협의 대상이다.

---

## 부록 A. 🔶 이 검토에서 발견한 권한 구멍 — 별도 이슈 대상

**이 문서의 계약이 아니다.** 인증(누구인가)이 아니라 **권한(무엇을 할 수 있나)** 영역이다. 어느 문서에도 없던 것이라 이슈가 생길 때까지만 여기 둔다. **이슈로 옮기면 이 부록을 지운다.**

| # | 무엇 | 왜 구멍인가 |
| --- | --- | --- |
| 1 | **간접 식별자에서 소유 아이를 역추적해 검사한다** — `run_id` · `memory_id` · `suggestion_id` 만 받는 API | 노션 권한표는 `/children/{cid}/*` 만 다룬다. 계약서에는 `GET /runs/{rid}/events` 처럼 child_id 가 경로에 없는 엔드포인트가 있다 |
| 2 | **AI 도구의 아이 범위는 서버가 주입한다.** 모델이 넘긴 child_id 를 믿지 않는다 | Agent 가 `memory.search` 를 직접 부르는 구조에서 도구 인자를 신뢰하면 **프롬프트 인젝션이 곧 권한 상승**이 된다 |
| 3 | **동의 철회 시 실행 중 작업을 외부 전달·저장 직전에 재검사한다** | NF-06 부분 결과 구조에서 시작 시점 검사만으로는 부족하다 |
| 4 | **SSE 재연결이 새 run 을 만들지 않는다.** 이벤트 ID 로 재개 커서를 잡는다 | 갱신 후 스트림을 다시 열 때 AI 실행이 중복되면 비용과 기록이 이중으로 쌓인다 |
