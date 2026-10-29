# apps/mobile — React Native 웹뷰 셸

Owner: 고태영 (프론트 리드)

> [최상위 CLAUDE.md](../../CLAUDE.md) §7 의 "모바일: React Native 웹뷰" 가 이 폴더다.
> 화면·상태·API 호출은 **전부 [`apps/web`](../web) 에 있다.** 여기는 네이티브만 할 수 있는 것을 맡는다.

---

## 1. 이 앱이 하는 일 / 안 하는 일

**한다** — 웹뷰로 `apps/web` 을 띄운다 · Android 물리 뒤로가기 → 웹뷰 히스토리 (하단 네비 네 칸에서는 두 번 눌러 종료) · 외부 링크를 in-app 브라우저로 앱 위에 · **로그인 시작 URL 을 인앱 인증 세션으로 열고 복귀를 웹에 싣는다** · 연결 실패 화면과 재시도 · safe area **값을 재서 웹에 넘긴다** · 백그라운드로 갈 때 웹뷰 쿠키를 디스크에 쓴다(값은 모른다) · **채팅바의 말로 적기를 기기 안 음성인식으로 돌리고 글자만 웹에 넘긴다**(#595) · **일정 알림의 FCM 토큰을 받아 웹에 넘기고, 알림을 누르면 그 화면 주소를 넘긴다**(#609 · 등록은 웹이 한다).

**안 한다** — 화면을 그리지 않는다. API 를 부르지 않는다. 로그인 토큰을 들고 있지 않다(FCM 토큰은 받아서 넘기기만 한다). 상태를 관리하지 않는다.

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
| expo-notifications | ~57.0.24 | 일정 알림 FCM 토큰 · 알림 채널 · 알림 누르기 (#609). `expo install` 이 골랐다. 함께 `expo-constants` 를 SDK 기대값 57.0.22 로 맞췄다 (두 벌이 되지 않게) |
| expo-speech-recognition | 57.1.1 | 말로 적기의 기기 안 음성인식 (#595). Expo 가 고르는 묶음 밖이라 메이저를 SDK 에 맞춰 고정했다 (57.x = SDK 57) |
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
src/native/safe-area.ts      상태바·제스처바 크기를 CSS 변수로 웹에 넘긴다 (키보드가 열리면 아래는 0)
src/native/back-press.ts     물리 뒤로가기 판정 — 히스토리 / 종료 안내 / 종료. 셸이 아는 웹 경로(하단 네비 네 칸)
src/native/auth-session.ts   로그인 시작 URL 판정 · 복귀 URL → /auth/callback 변환
src/native/in-app-browser.ts 외부 링크를 in-app 브라우저로 열지 OS 에 맡길지 판정
src/native/internal-url.ts   웹뷰 안에 둘 주소인지 판정 — 출처 + API 경로(`/api/…`) 제외
src/native/cookie-flush.ts   백그라운드로 갈 때 쿠키 flush 판단 (#469)
src/native/speech.ts         말로 적기 — 웹 부탁 검사(`parseWebRequest` · 알림 부탁도 여기서) · 기기 안 인식 조건 · 오류 → 이유 · 웹에 넣는 창구와 이벤트 (#595)
src/native/push.ts           일정 알림 — 알림 data → 화면 주소 · 권한 창을 띄울지 · 웹에 넣는 창구와 이벤트 (#609)
app.config.ts                app.json 위에 빌드 때만 정해지는 것 — `google-services.json` 경로 (#609). Node 에서 돌아서 tsconfig.test.json 이 본다
modules/cookie-flush/        로컬 Expo 모듈 (Android 만) — `CookieManager.flush()` 하나. 🚨 `android/` 가 생성물이 아니라 소스다
app.json                     Expo 설정 (name · scheme · bundle id · 막는 권한)
eas.json                     EAS 빌드 프로필 (배포 웹 주소 · versionCode 관리)
```

### 🚨 마이크 권한은 말로 적기 하나에만 쓴다 (#595)

마이크(`RECORD_AUDIO` · `expo-speech-recognition` 플러그인이 더한다)는 채팅바의 말로 적기에만 쓰고, 권한은 **처음 누를 때** 묻는다.
앱 권한 전체(릴리스 매니페스트 기준)는 아래 "알림 권한" 절의 표다.

- 🚨 **음성은 휴대폰 밖으로 나가지 않는다.** 음성을 서비스 밖으로 보내는 것은 정한 적이 없다 (개인정보 처리방침의
  국외 이전 표에 음성인식 업체가 없다 · 외부 전달 범위 자체가 최상위 CLAUDE.md §10 의 미정이다).
  `requiresOnDeviceRecognition: true` 로만 돌리고, 셸은 **글자만** 웹에 넘긴다. 이 옵션이 기기 안을
  **보장하는 곳은 Android 13+ 뿐**이라 거기서만 켠다 (`canListenOnDevice` · 근거는 `src/native/speech.ts` 머리말).
  Android 12 이하는 오프라인 힌트뿐이고, iOS 는 기기 안이 안 되면 그 옵션을 조용히 빼고 서버로 돈다.
  🚨 **기기 안이 안 되면 서버 인식으로 넘어가지 않는다.** 웹이 버튼을 끄고 이유를 말한다.
- 🚨 **문서가 바뀌거나 웹뷰가 죽으면 듣기를 끊는다** (`abortListening` · #602 리뷰). 새 문서 로딩(`onLoadStart`),
  연결 실패 화면(`onError` · 5xx), 렌더 프로세스 종료(`onRenderProcessGone`)다. 웹의 정리는 React 가 컴포넌트를 내릴 때만
  돌아서 새로고침 · 하드 이동에서는 `stop` 이 안 올 수 있다(웹은 `pagehide` 에서도 보낸다). 셸 → 웹 이벤트는 지금 떠 있는
  문서가 우리 주소일 때만 보낸다.
  ⚠️ `onRenderProcessGone` 이 생기면서 렌더 프로세스가 죽어도 앱이 같이 죽지 않고 연결 실패 화면으로 간다. 죽은 웹뷰는
  다시 쓸 수 없어서 "다시 시도" 가 새 웹뷰를 띄운다.
- 🚨 **Android 권한 창에는 `app.json` 의 문구가 나오지 않는다** (플러그인이 iOS Info.plist 에만 넣는다). 그래서 창을
  띄우기 직전(`getPermissionsAsync` 가 아직 아니고 다시 물을 수 있을 때)에 웹에 `asking` 을 보내고, 웹이 채팅바 위에
  이유를 한 줄로 말한다 (정보통신망법 제22조의2 선택 접근권한 고지). 거절은 다시 물을 수 있으면 `declined`, 아니면
  `denied` 로 나눠 보낸다 — 다시 누르면 될 보호자를 설정 화면으로 보내지 않는다.
- 🚨 **`abort()` 는 듣고 있을 때만 부른다** (`listeningRef`). 라이브러리의 `abort()` 는 늘 웹에 `end` 를 보내는데, 첫 탭의
  권한 창도 Android 에서는 `background` 라서 거기서 부르면 웹은 꺼진 줄 알고 셸은 허용 뒤 듣기 시작한다.
- 한국어 모델이 없으면 `androidTriggerOfflineModelDownload` 로 받기를 걸고 "내려받고 있어요" 라고 알린다. 받는 것은 인식 모델이다.
- 🚨 **라이브러리가 인식 결과를 logcat(`Log.d`)에 찍는 세 줄을 패치로 뺐다** (`patches/expo-speech-recognition@57.1.1.patch`
  · `pnpm-workspace.yaml` 의 `patchedDependencies`). 릴리스 빌드에서도 남아서 아이 얘기 원문이 USB 디버깅 · 버그 리포트에
  들어갔다. 지금은 글자 대신 건수만 찍는다 (최상위 CLAUDE.md §2 개인정보).
  🚨 **이 패키지를 올리면 패치를 다시 만든다** — 패치가 버전에 묶여 있어서, 버전을 바꾸면 pnpm 이
  `ERR_PNPM_UNUSED_PATCH` 로 멈춘다. 🚨 그걸 풀려고 `patchedDependencies` 줄만 지우지 않는다 — Mobile CI 의
  "Speech log patch" 단계가 설치된 `ExpoSpeechService.kt` 에서 `$resultsList` · `$nonEmptyStrings` 를 `.size` 없이
  찍는 줄을 찾으면 실패한다. 올린 버전에서 `results` · `transcriptions` 를 찍는 `log(...)` 를 다시 찾아 `pnpm patch`
  로 새로 만든다. ⚠️ Mobile CI 는 Kotlin 을 컴파일하지 않으니 `./gradlew :expo-speech-recognition:compileReleaseKotlin`
  으로 직접 확인하고, 릴리스 빌드에서 한 번 말해 본 뒤 `adb logcat -s ExpoSpeechService` 에 `results: 1` 처럼 건수만
  찍히는지 본다.
- ⚠️ **iOS 는 꺼져 있다.** 켜려면 기기에서 한국어 기기 안 인식이 서버로 새지 않는지부터 확인한다. 문구(`app.json` 플러그인 옵션)는 넣어 두었다.
- 매니페스트의 `<queries>` 에 `com.google.android.googlequicksearchbox` 가 붙는다 (플러그인 기본값). 권한이 아니라 패키지 조회 허용이다.

### 🚨 알림 권한은 일정 알림 하나에만 쓴다 (#609)

`expo-notifications` 를 넣으면 릴리스 매니페스트 권한이 2개에서 29개가 된다 (런처 배지 20여 개 · 부팅 수신 · 설치 경로).
쓰는 것만 남기고 `app.json` 의 `android.blockedPermissions` 로 막았다. 남는 것은 이렇다 (릴리스 병합 매니페스트로 확인).

| 권한 | 왜 |
| --- | --- |
| `POST_NOTIFICATIONS` | 알림을 띄운다 (Android 13+ 실행 중 권한). **처음 알림을 고를 때** 묻는다 |
| `com.google.android.c2dm.permission.RECEIVE` · `WAKE_LOCK` · `ACCESS_NETWORK_STATE` | FCM 수신 (firebase-messaging 이 더한다 · 일반 권한) |
| `kr.ai.icatch.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION` | androidx 가 앱 안 브로드캐스트를 막으려고 스스로 만드는 서명 권한. 사용자에게 묻지 않는다 |

- 🚨 **막은 것**: 런처 배지(ShortcutBadger · 배지를 안 쓴다 — `shouldSetBadge: false`), `RECEIVE_BOOT_COMPLETED`(기기 안에서 예약하는 알림이 없다 — 알림은 서버가 FCM 으로 보낸다), `BIND_GET_INSTALL_REFERRER_SERVICE`(`expo-application` 의 설치 경로 · 부르지 않는다).
  `expo-notifications` 를 올리면 매니페스트를 다시 보고 새로 붙은 것을 막는다
- 🚨 **토큰은 셸이 받고 등록은 웹이 한다** (§1). 셸은 토큰을 `icatch:push` 로 넘기기만 하고 로그에 남기지 않는다.
  **웹의 Firebase 값이 비어 있으면 웹이 묻지도 넘기지도 않는다** — 앱의 "묻기" 스위치도 웹 배포 한 곳이다.
  토큰 **저장**은 서버 `PUSH_DEVICE_ACCEPT` 가 막는다 (#610). 켜는 순서는
  [`docs/web/web-push-v1.md`](../../docs/web/web-push-v1.md) "켜는 순서"
- 🚨 **알림을 누른 화면 주소는 웹이 `ready` 를 보낸 뒤에 넘긴다.** 앱이 알림으로 켜지면 웹이 아직 없어서 들고 있는다.
  🚨 `ready` 를 `onLoadStart` 에서 내리지 않는다 — Android 는 웹 안의 화면 이동에도 그걸 불러서, 탭을 하나 옮긴 뒤로
  알림을 눌러도 화면이 안 옮겨졌다(#609 리뷰). 연결 실패 · 웹뷰 종료 때만 내린다 (`onDocumentGone`).
  처리한 누름은 `clearLastNotificationResponse` 로 지운다 — 뒤로가기 종료 뒤 다시 켜면 지난 알림의 날짜로 열렸다
- 🚨 **`google-services.json` 이 없는 빌드는 창구를 꺼서 낸다** (`PUSH_AVAILABLE`). 켜 두면 웹이 권한 창을 띄우고, 허용한 뒤에야 토큰 받기가 실패한다
- 🚨 **채널 id `schedule` 은 세 곳이 같아야 한다** — `src/native/push.ts` 의 `PUSH_CHANNEL_ID`, `app.json` 플러그인 `defaultChannel`
  (FCM 이 앱 밖에서 띄우는 알림), 서버 `reminder_dispatch.py` 의 `ANDROID_CHANNEL`(앱을 보고 있을 때 온 알림 · #610).
  앞의 둘은 `push.test.ts` 가 묶는다. 서버 값이 어긋나면 앱을 보는 중 받은 알림이 영문 기본 채널("Miscellaneous")로 간다 (실기기 확인)
- 🚨 **권한 창은 허용 전이고 OS 가 다시 물을 수 있을 때 띄운다** (`shouldAskPermission` · `canAskAgain`). 알림을 고를 때 많아야 두 번이다 —
  두 번 거절하면 Android 가 더 묻지 못하게 막는다. 🚨 **`status === "undetermined"` 로 판정하지 않는다.** Android 13+ 의
  `expo-notifications` 는 한 번도 묻지 않았어도 `"denied"` 를 돌려줘서, 그 판정으로는 권한 창이 영영 뜨지 않았다 (#609 실기기)
- ⚠️ **실기기로 확인할 때 걸리는 것** (#609 · SM-S942N Android 17)
  - **Metro 를 `CI=1` 로 띄우면 파일을 고쳐도 다시 읽지 않는다** ("Metro is running in CI mode, reloads are disabled"). 고친 셸 코드를 볼 때는 빼고 띄운다
  - **`am force-stop` 한 앱은 FCM 을 받지 못한다** (Android 의 중지 상태 · 사용자가 다시 열 때까지). 보호자가 최근 앱에서 쓸어 닫는 것과 다르다.
    "꺼진 앱" 은 앱을 뒤로 보낸 뒤 `am kill kr.ai.icatch` 로 만든다
  - **개발 빌드는 꺼진 앱을 알림으로 켜면 누른 알림을 놓친다** — 개발 런처가 앱을 다시 띄우면서 인텐트가 바뀐다. 이 경우는 릴리스 빌드로 본다
    (`./gradlew assembleRelease` · `http://localhost` 를 띄우려면 생성된 매니페스트에만 잠깐 `usesCleartextTraffic` 을 넣는다)
  - 앱을 보고 있을 때 온 알림의 채널은 `adb shell dumpsys notification --noredact` 의 `channel=` 으로 본다
- 🚨 **`google-services.json` 은 저장소에 두지 않는다** (`.gitignore`). EAS 는 파일 환경변수 `GOOGLE_SERVICES_JSON`
  (`eas env:create --name GOOGLE_SERVICES_JSON --type file --visibility secret`), 로컬은 이 폴더의 파일. 없으면 알림만 꺼진 빌드다 (`app.config.ts`)
- 🚨 **패키지를 import 하지 않고 모듈이 있을 때만 불러온다** (`App.tsx` 의 `Notifications`). 하위 모듈이 전부 `requireNativeModule` 이라,
  이 패키지를 넣기 전에 만든 개발 빌드에서 import 하면 셸이 뜨지 않는다
- ⚠️ **알림 아이콘이 없다.** `assets/android-icon-monochrome.png` 는 Expo 템플릿의 기본 그림이라 쓰지 않았다(런처의 테마 아이콘에도 이 그림이 쓰이고 있다). 아이콘을 정하면 플러그인 `icon` 에 넣는다
- ⚠️ **스토어에 올리기 전에** 개인정보 처리방침 제10장 ③("알림 권한을 요청하지 않습니다")을 고친 새 판이 먼저 나가야 하고, Play Console 데이터 보안에 기기 ID(FCM 토큰) 수집을 적는다 (#609 · 말로 적기의 마이크 문구와 한 판으로)
- ⚠️ **iOS 는 하지 않는다** (#594). `Notifications` 가 Android 에서만 불린다

### 🚨 사진 권한을 받지 않는다 (#408)

사진 권한은 받지 않는다. 사진을 고르는 길은 웹의
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
  권한과, `expo-notifications` 가 붙이는 런처 배지 · 부팅 수신 · 설치 경로 권한을 막는다 (위 "알림 권한" 절). 셸이 쓰는 곳이 없다. 권한을 더하는 패키지를 넣으면 릴리스 매니페스트를 다시 확인한다
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
- 🚨 **백그라운드로 갈 때 웹뷰 쿠키를 flush 한다** (#469 · `src/native/cookie-flush.ts`). 웹은 앱을 켤 때 refresh 쿠키로 로그인을 되살리고(#465) 서버는 그때마다 쿠키를 회전시킨다(#468). 웹뷰는 쿠키를 주기적으로만 디스크에 쓰고(Chromium 기준 약 30초) react-native-webview 는 페이지 로드가 끝날 때만 flush 하는데, 회전은 그 뒤다. flush 없이 바로 앱을 끄면 디스크에 직전 refresh 가 남고, 다음 실행에서 서버가 유예 30초 밖 재사용으로 보고 **세션을 지운다.** Android 는 최근 앱 화면으로 갈 때도 `background` 라 스와이프 종료 전에 지나간다.
  셸은 쿠키 값을 읽거나 쓰지 않는다 — 기록 시점만 앞당긴다 (§1). 네이티브 쪽은 로컬 모듈 `modules/cookie-flush` 이고 패키지를 늘리지 않았다. iOS 는 WKWebView 가 스스로 저장해 모듈이 없다 (기기 확인 전).
  ⚠️ **좁은 틈이 하나 남는다.** refresh 응답이 백그라운드 전환보다 늦게 오면(켜자마자 최근 앱 화면으로 간 경우, 왕복 수백 ms) flush 는 옛 쿠키를 쓰고 새 쿠키는 메모리에만 남는다. 그 상태로 스와이프 종료하고 30초 뒤 다시 켜면 서버가 세션을 지운다. 같은 증상을 보면 여기부터 의심한다.
  ⚠️ Mobile CI 는 Kotlin 을 컴파일하지 않는다. `modules/` 를 고치면 `./gradlew assembleDebug` 로 직접 빌드해 본다.
- **`cacheEnabled={false}`** 는 SSE(`/runs/{rid}/events`) 때문이다. 켜면 진행 이벤트가 버퍼링돼서 04 오버레이가 멈춘 것처럼 보인다.
- 🚨 **웹에서 오는 메시지(`onMessage`)는 `parseWebRequest` 를 통과한 것만 받는다** (#595). 지금 웹의 부탁은
  말로 적기 시작 · 멈춤과 일정 알림 준비 · 토큰 · 권한(`kind: "push"` · #609)이다. 🚨 **종류(`kind`)부터 가른다** —
  "말로 적기 시작이 아니면 멈춤" 으로 두면 알림 부탁이 마이크를 끈다. 보낸 쪽 주소(`event.nativeEvent.url`)를 먼저 보고, 모양을 검사한 값으로만
  네이티브를 부른다 — 모양이 어긋난 메시지 하나로 셸이 죽으면 안 된다. 부탁을 더하면(#593 위치 등) 같은 함수에
  `kind` 를 더하고 테스트를 단다.
  셸 → 웹은 `injectJavaScript` 로 `icatch:speech` · `icatch:push` 이벤트를 쏜다. 글자와 토큰은 `JSON.stringify` 로만 싣는다.
- 🚨 **권한을 늘리지 않는다** (§3 "사진 권한을 받지 않는다"). 네이티브 기능이 필요해지면 그 권한이 스토어 심사에
  무엇을 요구하는지부터 본다. ⚠️ 마이크(`RECORD_AUDIO`)를 더한 첫 배포 전에 Play Console 의 권한 선언과
  데이터 보안 항목에 걸리는지 확인한다 (§3 마이크 · #595).
- 🚨 **safe area 는 셸이 재고, 칠하는 것은 페이지다.** 웹뷰는 화면 끝까지 덮고(`edges={["left","right"]}`),
  상태바·제스처바 크기는 `--shell-inset-top` / `--shell-inset-bottom` 으로 넘긴다
  (`src/native/safe-area.ts` → 웹의 `pt-safe-*`/`pb-safe-*`).
  **셸이 그 자리를 자기 배경색으로 칠하면 안 된다** — 이어져야 할 색이 화면마다 다르다
  (그냥 화면 `canvas` · 하단 네비 `surface-muted` · 시트가 열리면 딤). 셸은 그중 하나밖에 모르니
  위아래에 흰 띠가 남았다 (#133). 화면별로 색을 바꿔 칠하는 것은 §1 을 깨는 길이다 — 자리만 비워 준다.
  ⚠️ 셸이 그리는 **연결 실패 화면**은 예외다. 그건 셸의 화면이라 거기서는 `SafeAreaView` 가 직접 먹는다.
- 🚨 **키보드가 열려 있으면 아래 여백을 0 으로 넘긴다** (#462). 웹뷰 바닥이 키보드 윗변으로 올라오고 시스템 바는
  키보드 아래에 가려지는데, `useSafeAreaInsets()` 의 아래 값은 키보드와 무관하게 그대로라 하단 네비가 키보드 위에
  빈 띠를 남겼다. `Keyboard` 이벤트로 `shellInsets()` 를 바꾼다. ⚠️ Android 에서만 켠다 — iOS 는 기기 확인 전이다.
- 🚨 **하단 네비 네 칸(홈 · 캘린더 · 기록/기억 · 아이)에서 뒤로가기는 히스토리로 가지 않는다** (#462). 탭 이동이
  push 로 쌓이고 로그인 화면도 히스토리에 남아 있어서 앱이 닫힐 차례가 오지 않았다. 닫힐 차례의 첫 누름은
  `ToastAndroid` 로 "한 번 더 누르면 종료" 를 띄우고, 2초 안에 또 누르면 `false` 를 돌려 OS 기본 동작에 넘긴다.
  ⚠️ 기본 동작은 액티비티를 **끝낸다** (Android 17 실기기 확인) — 웹뷰가 새로 만들어진다. 로그인은 웹이 refresh 쿠키로 되살리고(#465) 홈에서 다시 연다.
  뒤로 보내기만 하려면 `moveTaskToBack` 을 부르는 네이티브 코드가 필요하다.
  셸이 웹의 **화면** 경로를 아는 곳이다 (`/auth/callback` · `/api` 는 흐름의 입구라 성격이 다르다). 웹이 알려 주려면
  `onMessage` 를 열어야 해서 경로를 본다. `apps/web/src/components/child-nav.tsx` 의 `TABS` 가 바뀌면 `back-press.ts` 를 같이 고친다.
  토스트는 OS 가 그리는 것이라 "셸은 화면을 그리지 않는다"(§1)에 걸리지 않는다.
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
pnpm test            # Node 내장 러너 — 로그인 · 외부 링크 · 웹뷰 안 주소 · 뒤로가기 · safe area · 쿠키 flush · 말로 적기 · 일정 알림 판정 (src/native/*.test.ts)
pnpm deps:check      # expo install --check — SDK 와 어긋난 패키지 버전 확인
pnpm doctor          # expo-doctor 전체 점검
```

PR 에서는 [`.github/workflows/ci-mobile.yml`](../../.github/workflows/ci-mobile.yml) 이 `typecheck` · `test` 와 말로 적기 로그 패치 검사를 다시 돈다 (`apps/mobile/**` 를 바꾼 PR 만).
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

EAS 프로젝트는 **`truedoc` 조직의 `@truedoc/icatch`** 다 (`app.json` 의 `owner` · `extra.eas.projectId`).
개인 계정이 아니라 조직에 둔 이유는 스토어 운영자(약관의 운영자)와 같은 곳에 빌드 기록과 키를 두기 위해서다.
빌드하려면 그 조직의 멤버로 로그인한다.

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
- 🚨 **업로드 키는 EAS 가 만들어 `truedoc` 조직 자격 증명에 보관한다** (2026-10-14 결정). 키 파일을 팀원 PC 에
  두지 않는다 — 누가 빌드하든 같은 키로 서명된다. 한 번 올린 업로드 키는 Play Console 에 재설정을 요청해야 바뀌므로,
  `eas credentials` 에서 키를 지우거나 새로 만들지 않는다. 백업이 필요하면 조직 Owner 가 `eas credentials` 로 내려받는다.
- 🚨 **앱 식별자는 `kr.ai.icatch` 다** (Android `package` · iOS `bundleIdentifier` · #478). 소유 도메인 `icatch.ai.kr` 의
  역순이다. Play Console 에 한 번 올리면 영구히 고정되고 앱을 지워도 그 이름은 다시 못 쓴다 — 바꿀 수 있는 건 첫 업로드 전뿐이다.
  EAS 자격 증명은 applicationId 별로 저장돼서, 10-14 에 만든 `kr.ac.pusan.ktc4.icatch` 용 키는 쓰이지 않고
  `kr.ai.icatch` 용 키가 첫 `production` 빌드 때 한 번 새로 만들어진다. 그 뒤로는 위 규칙대로 건드리지 않는다.
  옛 식별자로 깔아 둔 앱은 덮어써지지 않고 따로 남아 둘 다 `icatch://` 를 받는다 — 로그인 복귀 때 앱 선택 창이 뜨니 지운다.
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
