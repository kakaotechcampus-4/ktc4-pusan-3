# apps/mobile — React Native 웹뷰 셸

Owner: 고태영 (프론트 리드)

> [최상위 CLAUDE.md](../../CLAUDE.md) §7 의 "모바일: React Native 웹뷰" 가 이 폴더다.
> 화면·상태·API 호출은 **전부 [`apps/web`](../web) 에 있다.** 여기는 네이티브만 할 수 있는 것을 맡는다.

---

## 1. 이 앱이 하는 일 / 안 하는 일

**한다** — 웹뷰로 `apps/web` 을 띄운다 · Android 물리 뒤로가기 → 웹뷰 히스토리 · 외부 링크를 in-app 브라우저로 앱 위에 · **로그인 시작 URL 을 인앱 인증 세션으로 열고 복귀를 웹에 싣는다** · 연결 실패 화면과 재시도 · safe area **값을 재서 웹에 넘긴다**.

**안 한다** — 화면을 그리지 않는다. API 를 부르지 않는다. 토큰을 들고 있지 않다. 상태를 관리하지 않는다.

🚨 **여기에 화면을 만들면 화면이 두 벌이 된다.** 웹에서 되는 것은 웹에서 한다.
네이티브 기능(푸시·카메라 권한 등)이 필요해지면 **웹뷰 브릿지로 값만 넘기고 UI 는 웹에 둔다.**

---

## 2. 스택 · 버전

| | 버전 | 비고 |
| --- | --- | --- |
| Expo SDK | **57** (`expo@57.0.28`) | |
| React Native | 0.86.3 | |
| React | 19.2.3 | |
| react-native-webview | 13.16.1 | |
| react-native-safe-area-context | 5.7.0 | |
| expo-dev-client | 57.0.20 | 개발 빌드 런처 — Expo Go 로는 로그인 복귀(`icatch://`)를 못 받는다 (아래) |
| expo-web-browser | 57.0.3 | 카카오 로그인 인증 세션 (Android = Custom Tabs, iOS = `ASWebAuthenticationSession`) · 외부 링크 in-app 브라우저 |
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
App.tsx                      웹뷰 셸 (로딩 · 실패 · 뒤로가기 · 외부 링크 · 주입 배선)
index.ts                     registerRootComponent
src/config.ts                WEB_URL · 허용 출처 판정
src/native/safe-area.ts      상태바·제스처바 크기를 CSS 변수로 웹에 넘긴다
src/native/auth-session.ts   로그인 시작 URL 판정 · 복귀 URL → /auth/callback 변환
src/native/in-app-browser.ts 외부 링크를 in-app 브라우저로 열지 OS 에 맡길지 판정
src/native/internal-url.ts   웹뷰 안에 둘 주소인지 판정 — 출처 + API 경로(`/api/…`) 제외
app.json                     Expo 설정 (name · scheme · bundle id · 막는 권한)
eas.json                     EAS 빌드 프로필 (배포 웹 주소 · versionCode 관리)
```

### 🚨 사진 권한을 받지 않는다 (#408)

Android 앱이 요청하는 권한은 `INTERNET` 하나다 (릴리스 매니페스트 기준). 사진을 고르는 길은 웹의
`<input type="file">` 이 여는 시스템 선택기와 카메라 인텐트라 Android 에서는 권한이 필요 없다.

⚠️ **iOS 는 확인 전이다.** WKWebView 의 촬영과 앨범은 Info.plist 의 `NSCameraUsageDescription` ·
`NSPhotoLibraryUsageDescription` 이 있어야 하는데 지금 둘 다 없다 (앨범 키는 이번에 플러그인과 함께 빠졌다).
iOS 첫 빌드 때 08 시트의 촬영과 앨범을 확인하고, 필요하면 `ios.infoPlist` 에 설명 문구를 넣는다.

예전에는 08 사진 시트의 "최근 사진" 줄을 채우려고 `expo-media-library` 로 갤러리 목록을 읽어
`window.icatch.recentPhotos` 로 웹에 꽂았다. 첫 스토어 배포 전에 뺐다 — Google Play 는 사진 권한
(`READ_MEDIA_IMAGES`)을 쓰는 앱에 별도 선언과 심사를 요구하고, 가끔 사진 한 장을 고르는 용도는 시스템
사진 선택기를 쓰라고 한다. 이 줄은 그 기준을 넘기 어렵다.

- 웹의 계약(`apps/web/src/lib/native/recent-photos.ts`)은 남아 있다. 셸이 창구를 꽂지 않으면 웹은 줄을
  그리지 않으므로 화면 동작은 그대로다.
- 🚨 **다시 넣으려면 권한 선언부터 정한다.** 구현과 그때 지킨 규칙(큰 값은 조각으로, 원본을 줄이지 않기,
  보낸 쪽 주소 확인, `getAssetInfoAsync` 금지)은 #144 와 #408 이전 커밋에 있다.
- `app.json` 의 `android.blockedPermissions` 는 Expo 템플릿이 기본으로 넣는 저장소 · 오버레이 · 진동
  권한을 막는다. 셸이 쓰는 곳이 없다. 권한을 더하는 패키지를 넣으면 릴리스 매니페스트를 다시 확인한다
  (`./gradlew :app:processReleaseMainManifest` 뒤 `app/build/intermediates/merged_manifest/release/`).

### 🚨 Expo Go 로는 로그인을 끝낼 수 없다 — 개발 빌드가 필요하다

카카오 로그인은 서버가 `icatch://auth` 로 돌려보내야 끝나는데(§4), Expo Go 는 이 앱의 스킴을 받지 않는다.
그래서 **로그인 뒤 화면을 보려면 개발 빌드**를 쓴다.

```bash
pnpm android          # = expo run:android — prebuild + gradle + 설치까지 한 번에
```

- `android/` · `ios/` 는 **생성물이고 커밋하지 않는다** (`.gitignore`). `app.json` 을 고쳤으면 다시 prebuild 한다.
- 에뮬레이터 저장 공간이 빠듯하면 `-PreactNativeArchitectures=x86_64` 로 APK 를 한 ABI 로 줄인다
  (4 ABI 175MB → x86_64 32MB).

---

## 4. 규칙

- **허용 출처 밖은 웹뷰에 가두지 않는다.** `onShouldStartLoadWithRequest` 에서 `isInternalUrl()` 로 거르고, 아니면 앱 밖으로 넘긴다. 소셜 로그인·약관 페이지가 웹뷰 안에서 열리면 사용자가 주소창을 못 봐서 피싱과 구분할 수 없다.
  🚨 **출처가 같아도 `/api/…` 는 밖이다** (#273). nginx 로 API 를 웹과 같은 주소 아래에 두면 약관 "전문 보기" 와 로그인 시작이 출처 검사를 통과해 버린다 — 약관은 가입 화면을 덮고(iOS 는 돌아올 길이 없다), 로그인은 아래 인증 세션 분기까지 오지 못한다. 셸은 API 주소를 모르므로 **경로만** 본다. nginx 경로가 `/api` 가 아니게 정해지면 `src/native/internal-url.ts` 를 같이 고친다.
- **앱 밖 웹 주소는 in-app 브라우저로 앱 위에 얹는다** (`openBrowserAsync` · #212). 시스템 브라우저로 넘기면 앱을 떠나서, 약관을 다 읽은 사람이 동의 화면을 다시 찾아야 한다 — 필수 동의 직전의 링크라 그 수고가 곧 "안 읽고 체크" 다. in-app 브라우저는 진짜 브라우저라 도메인 · 자물쇠가 보이므로 위 규칙과 맞는다. `http(s)` 가 아닌 스킴(`tel:` · `mailto:` · `intent:`)과 in-app 브라우저를 못 여는 경우는 지금처럼 `Linking.openURL` 이다.
- 🚨 **로그인 시작 URL 은 따로 연다** (`isAuthStartUrl()` · #210). 위의 in-app 브라우저(`openBrowserAsync`)나 시스템 브라우저로 열면 서버가 302 하는 `icatch://auth` 복귀를 받을 곳이 없다. `openAuthSessionAsync` 로 열고, `success` 일 때만 복귀 쿼리를 `/auth/callback` 에 붙여 **같은 웹뷰를 `location.replace` 로 옮긴다** — 웹뷰를 다시 만들면 `sessionStorage` 의 bind 가 사라진다. 셸은 URL 을 열고 돌려받을 뿐 코드 교환 · 토큰은 웹이 한다 (§1). 흐름 정본은 [`docs/web/kakao-login-v1.md`](../../docs/web/kakao-login-v1.md) §5.
- **`cacheEnabled={false}`** 는 SSE(`/runs/{rid}/events`) 때문이다. 켜면 진행 이벤트가 버퍼링돼서 04 오버레이가 멈춘 것처럼 보인다.
- 🚨 **웹에서 오는 메시지(`onMessage`)를 받지 않는다.** 지금 웹이 셸에 부탁할 것이 없다. 창구가 다시 생기면
  보낸 쪽 주소(`event.nativeEvent.url`)를 먼저 보고, 모양을 검사한 값으로만 네이티브를 부른다 —
  모양이 어긋난 메시지 하나로 셸이 죽으면 안 된다.
- 🚨 **권한을 늘리지 않는다** (§3 "사진 권한을 받지 않는다"). 네이티브 기능이 필요해지면 그 권한이 스토어 심사에
  무엇을 요구하는지부터 본다.
- 🚨 **safe area 는 셸이 재고, 칠하는 것은 페이지다.** 웹뷰는 화면 끝까지 덮고(`edges={["left","right"]}`),
  상태바·제스처바 크기는 `--shell-inset-top` / `--shell-inset-bottom` 으로 넘긴다
  (`src/native/safe-area.ts` → 웹의 `pt-safe-*`/`pb-safe-*`).
  **셸이 그 자리를 자기 배경색으로 칠하면 안 된다** — 이어져야 할 색이 화면마다 다르다
  (그냥 화면 `canvas` · 하단 네비 `surface-muted` · 시트가 열리면 딤). 셸은 그중 하나밖에 모르니
  위아래에 흰 띠가 남았다 (#133). 화면별로 색을 바꿔 칠하는 것은 §1 을 깨는 길이다 — 자리만 비워 준다.
  ⚠️ 셸이 그리는 **연결 실패 화면**은 예외다. 그건 셸의 화면이라 거기서는 `SafeAreaView` 가 직접 먹는다.
- ⚠️ **Android 웹뷰의 `env(safe-area-inset-*)` 는 상태바·제스처바를 세지 않는다** (디스플레이 컷아웃만 본다).
  그래서 웹이 스스로 알 수 없고, 셸이 넘겨야 한다. 예전에 셸 패딩과 `env()` 가 **둘 다 걸려 위쪽이 두 번 밀린**
  적이 있는데(#133), 지금은 웹이 셸 값을 먼저 보므로 한 번만 센다.
- 🚨 **`EXPO_PUBLIC_*` 는 앱 번들에 그대로 들어간다.** 디컴파일하면 보인다. 이 셸이 아는 비밀은 **없어야 정상**이다 (§9 · NF-09).

---

## 5. 명령어

```bash
pnpm start           # Expo 개발 서버 (Expo Go / 개발 빌드)
pnpm android         # Android 로 열기
pnpm ios             # iOS 로 열기 (macOS 필요)
pnpm typecheck       # tsc --noEmit (앱 + 테스트 두 벌)
pnpm test            # Node 내장 러너 — 로그인 · 외부 링크 · 웹뷰 안 주소 판정 (src/native/*.test.ts)
pnpm deps:check      # expo install --check — SDK 와 어긋난 패키지 버전 확인
pnpm doctor          # expo-doctor 전체 점검
```

PR 에서는 [`.github/workflows/ci-mobile.yml`](../../.github/workflows/ci-mobile.yml) 이 `typecheck` · `test` 를 다시 돈다 (`apps/mobile/**` 를 바꾼 PR 만).
네이티브 빌드는 CI 가 하지 않는다 — 개발 빌드는 기기에서 확인한다.

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
adb.exe reverse tcp:8000 tcp:8000   # apps/api — 실서버 로그인을 볼 때. 카카오 콜백이 localhost:8000 이라 인증 세션도 여기로 온다
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

### 배포 빌드 (EAS · #410)

스토어에 올리는 것은 `eas.json` 의 `production` 프로필이 만드는 `.aab` 다. 프로필은 셋이다.

| 프로필 | 산출물 | 띄우는 웹 | 쓰는 곳 |
| --- | --- | --- | --- |
| `development` | 개발 빌드 apk | Metro 가 읽는 `.env.local` | 로컬 개발 (`pnpm android` 와 같은 앱) |
| `preview` | apk | `https://icatch.ai.kr` | 팀원 실기기에 바로 설치해 보는 것 |
| `production` | aab | `https://icatch.ai.kr` | Play 스토어 업로드 |

```bash
npx eas-cli@latest login                                    # 최초 1회 — 팀 Expo 계정
npx eas-cli@latest build -p android --profile production    # aab (EAS 서버에서 빌드)
npx eas-cli@latest build -p android --profile preview       # apk — 설치 링크가 나온다
```

- 🚨 **배포 빌드의 웹 주소는 `eas.json` 의 `env` 가 정한다.** `.env.local` 은 `.gitignore` 라 EAS 서버로
  올라가지 않는다. 주소를 바꾸면 `eas.json` 을 고친다 — `EXPO_PUBLIC_*` 는 번들에 박히므로 다시 빌드해야 한다.
- 🚨 **`versionCode` 는 EAS 가 올린다** (`appVersionSource: remote` + `autoIncrement`). `app.json` 에 손으로 적지 않는다.
  사용자에게 보이는 `version`(지금 `0.1.0`)만 `app.json` 에서 올린다.
- ⚠️ **업로드 키를 누가 만들고 어디에 보관할지는 아직 정하지 않았다** (#410 범위 밖). 첫 `production` 빌드 때 EAS 가
  "키를 만들어 보관할까요" 를 묻는데, 정하기 전에 "예" 를 누르지 않는다. 한 번 올린 업로드 키는 Play Console 에
  재설정을 요청해야 바뀐다.
- EAS 없이 aab 가 만들어지는지만 볼 때는 로컬 gradle 로 굽는다. 서명은 디버그 키이고 `versionCode` 는 늘 1 이라
  스토어에는 못 올린다. 🚨 **두 줄을 같이 돌린다.** 셸에서 준 주소가 `.env.local` 보다 앞서는데, `prebuild --clean`
  없이 둘째 줄만 다른 주소로 다시 돌리면 gradle 이 번들 작업을 건너뛰어 옛 주소가 남을 수 있다.

```bash
CI=1 pnpm exec expo prebuild --clean --platform android --no-install
cd android && EXPO_PUBLIC_WEB_URL=https://icatch.ai.kr ./gradlew bundleRelease
# → android/app/build/outputs/bundle/release/app-release.aab
# 박힌 주소 확인 (Hermes 문자열이라 앞 글자가 붙어 보일 수 있다)
unzip -p app/build/outputs/bundle/release/app-release.aab base/assets/index.android.bundle | strings | grep -o 'icatch\.ai\.kr\|localhost:[0-9]*'
```
