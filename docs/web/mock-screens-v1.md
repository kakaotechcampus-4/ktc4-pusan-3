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
localStorage.removeItem("icatch.session");
localStorage.removeItem("icatch.mock.scenario");
location.replace("/");
```

| 보고 싶은 화면 | 어떻게 |
| --- | --- |
| 00 로그인 | `/` |
| 00 로그인 · 버튼 비활성 | `/?scenario=auth_unready` |
| **가입 동의** | 초기화 후 `/?scenario=consent` → 카카오로 시작하기 |
| 01 아이 만들기 | 로그인 후 `/onboarding` (목의 `/me` 는 항상 아이가 1명이라 로그인만으로는 안 닿는다) |
| 02 이야기 하나 | `/child/c1/onboarding` |
| 03 홈 | `/child/c1/home` |
| 03 홈 · 빈 상태 | `/child/c1/home?scenario=empty` |
| 04 진행 · 저장 결과 | 03 에서 한 줄 적고 남기기 (04 는 별도 주소가 없다) |
| 04 실패 · 원문 복원 | `/child/c1/home?scenario=failed` 에서 한 줄 남기기 |
| 04 부분 결과 | `/child/c1/home?scenario=partial` 에서 한 줄 남기기 |
| 05 제안 후보 | `/child/c1/suggestions?agents=food,activity` |
| 05 · 근거 부족 (일반 추천 1건 + 질문 1개) | 위 주소에 `&scenario=scarcity` |
| 05 · 기록 0건 (일반 추천 2건 + 질문 1개) | 위 주소에 `&scenario=empty` |
| 05 · 알레르기 미상 guard · 오래된 근거 | 위 주소에 `&scenario=stale` |
| 06 승인 시트 | 05 에서 "이걸로" (시트라 주소가 없다) |
| 07 기록 · 기억 | `/child/c1/memories` (`?tab=profile` · `?tab=feedback` · `?domain=food`) |
| 08 사진으로 적기 | `/child/c1/photos` — 아무 이미지 파일이나 고르면 알림장으로 읽는다 |
| 08 · 아이 활동 사진 | `/child/c1/photos?scenario=photo_activity` (태그가 **하나도 안 골라진** 채로 시작한다) |
| 08 · 읽어낼 게 없는 사진 | `/child/c1/photos?scenario=photo_unreadable` |
| 08 · 캘린더의 하루에서 들어온 경우 | `/child/c1/photos?date=2026-09-12` 또는 09 하루 패널의 "사진으로 적기" |
| 09 캘린더 | `/child/c1/calendar` (`?date=YYYY-MM-DD`) |
| 로그인 실패 문구 | `/auth/callback?error=invalid_state` |
| 디자인 시스템 | `/design-system` |

화면만 빨리 보려면 값을 직접 심어도 된다. 🚨 **`bind` 를 빼면 화면은 떠도 제출이 400 이다** — 서버가 형식을 검증하는 게 정상 동작이다.

```js
sessionStorage.setItem("icatch.oauth.consent_code", "cc_mock");
sessionStorage.setItem("icatch.oauth.provider", "kakao");
sessionStorage.setItem("icatch.oauth.bind", "dev".padEnd(43, "x"));
location.replace("/auth/consent");
```
