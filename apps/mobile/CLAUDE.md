# apps/mobile — React Native 웹뷰 셸

Owner: 고태영 (프론트 리드)

> [최상위 CLAUDE.md](../../CLAUDE.md) §7 의 "모바일: React Native 웹뷰" 가 이 폴더다.
> 화면·상태·API 호출은 **전부 [`apps/web`](../web) 에 있다.** 여기는 네이티브만 할 수 있는 것을 맡는다.

---

## 1. 이 앱이 하는 일 / 안 하는 일

**한다** — 웹뷰로 `apps/web` 을 띄운다 · Android 물리 뒤로가기 → 웹뷰 히스토리 · 외부 링크를 시스템 브라우저로 · 연결 실패 화면과 재시도 · safe area 처리.

**안 한다** — 화면을 그리지 않는다. API 를 부르지 않는다. 토큰을 들고 있지 않다. 상태를 관리하지 않는다.

🚨 **여기에 화면을 만들면 화면이 두 벌이 된다.** 웹에서 되는 것은 웹에서 한다.
네이티브 기능(푸시·카메라 권한 등)이 필요해지면 **웹뷰 브릿지로 값만 넘기고 UI 는 웹에 둔다.**

---

## 2. 스택 · 버전

| | 버전 | 비고 |
| --- | --- | --- |
| Expo SDK | **57** (`expo@57.0.20`) | |
| React Native | 0.86.3 | |
| React | 19.2.3 | |
| react-native-webview | 13.16.1 | |
| react-native-safe-area-context | 5.7.0 | |
| TypeScript | 6.0.3 | |

### ⚠️ 네이티브 패키지는 npm 최신 ≠ 맞는 버전

npm 최신은 `react-native@0.87.1` · `react-native-webview@14.0.1` 이지만, **SDK 57 이 검증한 조합은 위 표**다.
네이티브 모듈은 SDK 가 고정한 버전에서 벗어나면 빌드가 깨진다.

🚨 **패키지를 `pnpm add` 로 넣지 말고 `pnpm exec expo install <pkg>` 로 넣는다.** SDK 에 맞는 버전을 골라준다.
`pnpm deps:check` (= `expo install --check`) 로 어긋난 버전을 확인하고, `pnpm doctor` 로 전체 점검한다.

### pnpm 설정

`pnpm-workspace.yaml` 의 `nodeLinker: hoisted` 는 **지우면 안 된다.**
Expo autolinking 이 pnpm 의 격리된 심볼릭 링크 구조에서 네이티브 모듈을 못 찾는다.

---

## 3. 구조

```
App.tsx           웹뷰 셸 (로딩 · 실패 · 뒤로가기 · 외부 링크)
index.ts          registerRootComponent
src/config.ts     WEB_URL · 허용 출처 판정
app.json          Expo 설정 (name · scheme · bundle id)
```

---

## 4. 규칙

- **허용 출처 밖은 웹뷰에 가두지 않는다.** `onShouldStartLoadWithRequest` 에서 `isInternalUrl()` 로 거르고, 아니면 `Linking.openURL` 로 시스템 브라우저에 넘긴다. 소셜 로그인·약관 페이지가 웹뷰 안에서 열리면 사용자가 주소창을 못 봐서 피싱과 구분할 수 없다.
- **`cacheEnabled={false}`** 는 SSE(`/runs/{rid}/events`) 때문이다. 켜면 진행 이벤트가 버퍼링돼서 04 오버레이가 멈춘 것처럼 보인다.
- **safe area 는 네이티브 셸이 먹는다** (`SafeAreaView`). 웹의 `pt-safe`/`pb-safe` 는 모바일 브라우저 직접 접속용이라 웹뷰 안에서는 0 이 된다 — 정상이다.
- 🚨 **`EXPO_PUBLIC_*` 는 앱 번들에 그대로 들어간다.** 디컴파일하면 보인다. 이 셸이 아는 비밀은 **없어야 정상**이다 (§9 · NF-09).

---

## 5. 명령어

```bash
pnpm start           # Expo 개발 서버 (Expo Go / 개발 빌드)
pnpm android         # Android 로 열기
pnpm ios             # iOS 로 열기 (macOS 필요)
pnpm typecheck       # tsc --noEmit
pnpm deps:check      # expo install --check — SDK 와 어긋난 패키지 버전 확인
pnpm doctor          # expo-doctor 전체 점검
```

**최초 세팅** — `cp .env.example .env.local`.

🚨 **실기기로 개발할 때 `localhost` 는 안 된다.** 폰 입장에서 localhost 는 폰 자신이다.
`EXPO_PUBLIC_WEB_URL` 에 개발 PC 의 LAN IP 를 넣는다 (예: `http://192.168.0.10:3000`).
그리고 `apps/web` 을 `pnpm dev -- -H 0.0.0.0` 으로 띄워 외부에서 붙을 수 있게 한다.
