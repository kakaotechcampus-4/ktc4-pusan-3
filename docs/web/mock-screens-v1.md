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
| **가입 (이름 + 동의 → 경로 고르기)** | 초기화 후 `/?scenario=consent` → 카카오로 시작하기 |
| 가입에서 켠 선택 동의(위치)가 10 설정에 반영되는지 | 가입에서 `[선택] 위치정보` 를 켜고 → `/child/c1/settings` |
| 00-1 경로 고르기 (새로 등록 / 초대로 참여) | `/start?scenario=consent` — 🚨 `consent` 시나리오에서만 아이가 0명이다 (아래) |
| 초대 코드 입력 | `/invite?scenario=consent` — 아무 8자(`MKGRAND1`)나 넣고 **코드 확인하기** |
| 초대 확인 단계 (어느 아이인지 · 관계 고르기) | 위에서 확인을 누르면 나온다. 🚨 **확인은 코드를 쓰지 않는다** — 그만두고 다시 넣어도 된다 |
| 초대 · 이미 사용된 코드 | 위 화면에 `MKWASTED` |
| 초대 · 기한 지난 코드 | 위 화면에 `MKPAST12` |
| 초대 · 이미 아이가 있음 | 위 화면에 `MKTAKEN2`, 또는 `consent` 가 아닌 시나리오에서 아무 코드나 |
| 초대 · 시도 제한(429) | 위 화면에서 틀린 코드를 5번 |
| 01 아이 만들기 | `/onboarding` — `consent` 가 아닌 시나리오에서는 `/me` 에 아이가 1명이라 00-1 이 홈으로 보낸다 |
| 02 아이 정보 (전부 선택) | `/child/c1/onboarding` — 관계 · 성별 · 키 · 몸무게 · 알레르기 |
| 03 홈 | `/child/c1/home` |
| 03 홈 · 빈 상태 | `/child/c1/home?scenario=empty` |
| 04 진행 · 저장 결과 | 03 에서 한 줄 적고 남기기 (04 는 별도 주소가 없다) |
| 04 실패 · 원문 복원 | `/child/c1/home?scenario=failed` 에서 한 줄 남기기 |
| 04 부분 결과 | `/child/c1/home?scenario=partial` 에서 한 줄 남기기 |
| 04 결과 못 받음 (저장 여부 모름) | `/child/c1/home?scenario=disconnected` 에서 한 줄 남기기 |
| 05 제안 후보 | `/child/c1/suggestions?agents=food,activity` |
| 05 · 근거 부족 (일반 추천 1건 + 질문 1개) | 위 주소에 `&scenario=scarcity` |
| 05 · 기록 0건 (일반 추천 2건 + 질문 1개) | 위 주소에 `&scenario=empty` |
| 05 · 알레르기 미상 guard · 오래된 근거 | 위 주소에 `&scenario=stale` |
| 06 승인 시트 | 05 에서 "이걸로" (시트라 주소가 없다) |
| 07 기록 · 기억 | `/child/c1/memories` (`?tab=profile` · `?tab=feedback` · `?domain=food`) |
| 08 사진으로 적기 | 03 홈의 카메라 버튼 → 시트에서 **어떤 사진인지 고르고** → 최근 사진·촬영·앨범 |
| 08 · 알림장으로 읽기 | 시트에서 "알림장·식단표" 를 고른다 (아무 이미지 파일이나 넣으면 된다) |
| 08 · 아이 활동 사진으로 읽기 | 시트에서 "아이 활동 사진" 을 고른다 — 태그가 **하나도 안 골라진** 채로 시작한다 |
| 08 · 최근 사진 줄 | 목을 켜면 가짜 썸네일이 채워진다. 실제로는 `apps/mobile` 셸이 꽂는다 (웹은 갤러리를 못 읽는다) |
| 08 · 직접 들어온 경우 | `/child/c1/photos` — 넘겨받은 사진이 없어서 고르는 칸이 선다 |
| 08 · 읽어낼 게 없는 사진 | `/child/c1/photos?scenario=photo_unreadable` |
| 08 · 한 달치 식단표 | `?scenario=photo_meal_plan` — 항목 21건. 잘 읽은 것이 접히고, 펼치면 목록 안에서 스크롤한다 |
| 08 · 고른 종류와 다르게 읽힘 | `?scenario=photo_lane_mismatch` — **화면에는 차이가 없다.** 계약 테스트용이다 (추측이 선언을 덮지 않는지) |
| 08 · 캘린더의 하루에서 들어온 경우 | `/child/c1/photos?date=2026-09-12` 또는 09 하루 패널의 "사진으로 적기" |
| 09 캘린더 | `/child/c1/calendar` (`?date=YYYY-MM-DD`) |
| 로그인 실패 문구 | `/auth/callback?error=invalid_state` |
| 디자인 시스템 | `/design-system` |

🚨 **`consent` 시나리오에서만 아이가 0명이다.** 목의 `/me` 는 다른 시나리오에서 늘 아이 1명을
돌려주고, 00-1 과 초대 화면은 **아이가 없는 계정에서만** 선다 (있으면 홈으로 되돌린다).
그 시나리오에서 아이가 생기는 길은 실서버와 같은 둘뿐이다 — `POST /children` 과 초대 수락
(`mocks/handlers/membership.ts`). 새로고침해도 유지되고, 탭을 닫으면 사라진다.

🚨 **초대 실패는 시나리오가 아니라 입력값으로 갈린다.** 만료·재사용·중복 등록은 실서버에서도
그냥 일어나는 일이라, 시나리오로 만들 이유가 없다 (08 의 두 lane 과 같은 판단).

화면만 빨리 보려면 값을 직접 심어도 된다. 🚨 **`bind` 를 빼면 화면은 떠도 제출이 400 이다** — 서버가 형식을 검증하는 게 정상 동작이다.

```js
sessionStorage.setItem("icatch.oauth.consent_code", "cc_mock");
sessionStorage.setItem("icatch.oauth.provider", "kakao");
sessionStorage.setItem("icatch.oauth.bind", "dev".padEnd(43, "x"));
location.replace("/auth/consent");
```
