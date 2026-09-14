# 목 서버로 화면 확인하기 (v1)

> `apps/web/CLAUDE.md` §7 에서 옮겨 온 조회표다. 목 시나리오의 **의미**와 규칙, 목이 있는 이유는 그쪽에 있다.

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
