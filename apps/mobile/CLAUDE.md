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
| expo-media-library | 57.0.5 | 최근 사진 목록 · 권한 (`/legacy` 진입점을 쓴다 — `src/native/recent-photos.ts` 머리말) |
| expo-dev-client | 57.0.19 | 개발 빌드 런처 — Expo Go 로는 사진을 못 본다 (아래) |
| expo-image-manipulator | 57.0.19 | 썸네일 축소 |
| expo-file-system | 57.0.7 | 원본 base64 읽기 |
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
App.tsx                    웹뷰 셸 (로딩 · 실패 · 뒤로가기 · 외부 링크 · 브릿지 배선)
index.ts                   registerRootComponent
src/config.ts              WEB_URL · 허용 출처 판정
src/native/bridge.ts       웹뷰에 꽂는 창구 — 주입 스크립트 · 요청 파싱 · 조각 전송
src/native/recent-photos.ts  최근 사진을 실제로 읽는 곳 (권한 · 썸네일 · 원본)
app.json                   Expo 설정 (name · scheme · bundle id · 권한 플러그인)
```

### 최근 사진 브릿지 (`window.icatch.recentPhotos`)

08 사진 시트의 "최근 사진" 줄은 **웹이 만들 수 없다.** 브라우저는 기기 갤러리 *목록*을 읽는 API 가 없고
(`<input type="file">` 은 OS 피커를 열 뿐이다), 그래서 값은 셸이 꽂아 준다.

**계약의 정본은 웹에 있다** — [`apps/web/src/lib/native/recent-photos.ts`](../web/src/lib/native/recent-photos.ts).
여기는 그 계약을 만족시키는 쪽이고, 모양을 바꾸려면 **웹의 그 파일을 먼저 고친다.**

```
웹  listRecentPhotos(12)  ──postMessage──▶  App.tsx onMessage
                                                 └▶ src/native/recent-photos.ts (권한 · 쿼리 · 썸네일)
웹  ◀──injectJavaScript──  조각 N 개 + 끝맺음  ◀──┘
```

- **큰 값은 나눠 보낸다.** 네이티브 → 웹은 스크립트를 밀어 넣는 것뿐이라 값을 스크립트에 적어야 하는데,
  사진 원본 base64 를 한 번에 넣으면 Android WebView 가 조용히 잘라 먹는다.
- 🚨 **`injectedJavaScriptBeforeContentLoaded` 다.** 웹의 개발용 가짜 브릿지
  (`apps/web/src/mocks/native-bridge.ts`)가 "이미 꽂혀 있으면 안 건드린다" 이므로, 진짜가 먼저 자리를 잡아야
  셸 안에서 가짜 썸네일이 뜨지 않는다.
- 🚨 **권한 거부를 셸이 설명하지 않는다.** 답은 빈 값(`kind: "empty"`)이고, 줄을 그릴지 말지는 웹이 정한다.
  여기에 안내 화면을 만들면 화면이 두 벌이 된다 (§1).
- 🚨 **원본을 줄이지 않는다.** 이 사진으로 알림장 글자를 읽는다. "앨범에서 고르기" 는 원본을 그대로 넘기므로,
  여기만 줄이면 **같은 사진인데 경로에 따라 분석 결과가 달라진다.**
- 🚨 **고른 사진의 주소를 `getAssetInfoAsync(id)` 로 다시 묻지 않는다.** 그 함수는 EXIF 를 열기 때문에
  Android 에서 `ACCESS_MEDIA_LOCATION`(사진이 **찍힌 장소**)을 요구하고, 없으면 호출 자체가 거절된다.
  이 앱은 그 권한을 받을 이유가 없으므로, 목록을 만들 때 쥐고 있던 주소를 셸이 기억해 뒀다가 쓴다.

### 🚨 Expo Go 로는 최근 사진을 볼 수 없다 — 개발 빌드가 필요하다

Expo Go 는 Android 권한 정책이 바뀐 뒤로 미디어 라이브러리 접근을 **아예 막았다.**
권한을 묻기도 전에 호출이 거절된다 (`Expo Go can no longer provide full access to the media library`).

그래서 **이 줄을 눈으로 확인하려면 개발 빌드**를 써야 한다. 나머지 화면은 Expo Go 에서 그대로 보인다 —
브릿지가 없는 것과 같은 상태가 되어 최근 사진 줄만 안 선다.

```bash
pnpm android          # = expo run:android — prebuild + gradle + 설치까지 한 번에
```

- `android/` · `ios/` 는 **생성물이고 커밋하지 않는다** (`.gitignore`). `app.json` 을 고쳤으면 다시 prebuild 한다.
- 에뮬레이터 저장 공간이 빠듯하면 `-PreactNativeArchitectures=x86_64` 로 APK 를 한 ABI 로 줄인다
  (4 ABI 175MB → x86_64 32MB).

---

## 4. 규칙

- **허용 출처 밖은 웹뷰에 가두지 않는다.** `onShouldStartLoadWithRequest` 에서 `isInternalUrl()` 로 거르고, 아니면 `Linking.openURL` 로 시스템 브라우저에 넘긴다. 소셜 로그인·약관 페이지가 웹뷰 안에서 열리면 사용자가 주소창을 못 봐서 피싱과 구분할 수 없다.
- **`cacheEnabled={false}`** 는 SSE(`/runs/{rid}/events`) 때문이다. 켜면 진행 이벤트가 버퍼링돼서 04 오버레이가 멈춘 것처럼 보인다.
- **safe area 는 네이티브 셸이 먹는다** (`SafeAreaView`). 웹의 `pt-safe`/`pb-safe` 는 모바일 브라우저 직접 접속용이라 웹뷰 안에서는 0 이 된다 — 정상이다.
- 🚨 **웹에서 온 메시지를 믿지 않는다.** `parseBridgeRequest()` 를 거치지 않은 값으로 네이티브를 부르지 않는다.
  웹뷰는 우리 페이지만 띄우지만(`isInternalUrl`), 모양이 어긋난 메시지 하나로 셸이 죽으면 안 된다.
- 🚨 **사진 경로 · 파일 이름 · 썸네일을 로그에 찍지 않는다** (최상위 CLAUDE.md §2 개인정보).
  예외 메시지에도 경로가 섞여 들어오므로 `catch` 에서 그대로 찍지 않는다.
- 🚨 **권한은 필요한 것만 묻는다.** `granularPermissions: ["photo"]` — 사진만이다. 기본값은 영상 · 오디오까지
  묻고, 저장 권한(`savePhotosPermission`)과 사진 위치(`isAccessMediaLocationEnabled`)는 끈 채로 둔다.
- 🚨 **`EXPO_PUBLIC_*` 는 앱 번들에 그대로 들어간다.** 디컴파일하면 보인다. 이 셸이 아는 비밀은 **없어야 정상**이다 (§9 · NF-09).

---

## 5. 명령어

```bash
pnpm start           # Expo 개발 서버 (Expo Go / 개발 빌드)
pnpm android         # Android 로 열기
pnpm ios             # iOS 로 열기 (macOS 필요)
pnpm typecheck       # tsc --noEmit (앱 + 테스트 두 벌)
pnpm test            # Node 내장 러너 — 브릿지 왕복 (src/native/*.test.ts)
pnpm deps:check      # expo install --check — SDK 와 어긋난 패키지 버전 확인
pnpm doctor          # expo-doctor 전체 점검
```

**최초 세팅** — `cp .env.example .env.local`.

🚨 **실기기로 개발할 때 `localhost` 는 안 된다.** 폰 입장에서 localhost 는 폰 자신이다.
`EXPO_PUBLIC_WEB_URL` 에 개발 PC 의 LAN IP 를 넣는다 (예: `http://192.168.0.10:3000`).
그리고 `apps/web` 을 `pnpm dev -H 0.0.0.0` 으로 띄워 외부에서 붙을 수 있게 한다
(⚠️ pnpm 10+ 는 `--` 없이 그대로 넘긴다 — `pnpm dev -- -H` 는 `-H` 를 디렉터리로 읽는다).

### WSL 에서 개발하고 Windows 에뮬레이터로 볼 때

포트를 `adb reverse` 로 넘기면 `localhost` 를 그대로 쓸 수 있다. 에뮬레이터는 Windows 쪽에서 돌고
Metro · Next 는 WSL 에서 도는데, WSL2 가 Windows 의 `localhost` 를 WSL 로 넘겨 주기 때문이다.

```bash
adb.exe reverse tcp:8081 tcp:8081   # Metro
adb.exe reverse tcp:3000 tcp:3000   # apps/web
```

`.env.local` 은 `EXPO_PUBLIC_WEB_URL=http://localhost:3000` 그대로 두고,
Expo 는 `pnpm exec expo start --localhost --dev-client` 로 띄운다.

개발 빌드를 WSL 에서 만들고 Windows 에뮬레이터에 넣을 때는 **Windows 쪽 `adb.exe`** 를 쓴다
(WSL 의 adb 는 Windows adb 서버를 못 본다). APK 경로도 Windows 경로로 준다.

```bash
cd android && ./gradlew assembleDebug -PreactNativeArchitectures=x86_64
cp app/build/outputs/apk/debug/app-debug.apk /mnt/c/Users/Public/icatch-debug.apk
adb.exe install -r 'C:\Users\Public\icatch-debug.apk'
adb.exe shell am start -a android.intent.action.VIEW \
  -d "icatch://expo-development-client/?url=http%3A%2F%2Flocalhost%3A8081"
```

⚠️ AVD 스냅샷이 깨져 있으면 에뮬레이터가 `offline` 에서 안 넘어온다. `-no-snapshot-load` 로 콜드 부팅한다.
