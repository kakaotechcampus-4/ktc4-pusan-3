import { requireOptionalNativeModule } from "expo";
import Constants from "expo-constants";
import type { ExpoSpeechRecognitionModule } from "expo-speech-recognition";
import { StatusBar } from "expo-status-bar";
import * as WebBrowser from "expo-web-browser";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  BackHandler,
  Keyboard,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  ToastAndroid,
  View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { WebView, type WebViewMessageEvent, type WebViewNavigation } from "react-native-webview";

import { CookieFlush } from "./modules/cookie-flush";
import { WEB_URL, isInternalUrl, isRootUrl } from "./src/config";
import {
  AUTH_RETURN_URL,
  callbackUrlFromReturn,
  isAuthStartUrl,
  navigateScript,
} from "./src/native/auth-session";
import { EXIT_HINT, decideBack } from "./src/native/back-press";
import { flushOnBackground } from "./src/native/cookie-flush";
import { isWebUrl } from "./src/native/in-app-browser";
import { SAFE_AREA_SCRIPT, safeAreaScript, shellInsets } from "./src/native/safe-area";
import {
  SPEECH_LANG,
  type SpeechEvent,
  canListenOnDevice,
  parseWebRequest,
  permissionReason,
  speechBridgeScript,
  speechErrorReason,
  speechEventScript,
  volumeLevel,
} from "./src/native/speech";

/**
 * 🚨 **디자인 값의 원본은 `apps/web/src/app/globals.css` 다** (최상위 CLAUDE.md §6).
 *    여기 있는 것은 페이지가 뜨기 전·연결이 실패했을 때만 보이는 **사본**이다 —
 *    두 값이 어긋나면 로딩 순간에 색이 한 번 튄다. 바뀌면 같은 PR 에서 같이 고친다.
 */
const CANVAS = "#fbf9f5"; // --color-canvas
const BRAND = "#2f6f4e"; // --color-brand
const INK = "#1a1a17"; // --color-ink
const INK_MUTED = "#6b6b63"; // --color-ink-muted

/** 웹 쪽에서 네이티브임을 알아볼 수 있게 붙인다 (예: 웹뷰에서만 다른 UI). */
const USER_AGENT_SUFFIX = `IcatchApp/${Constants.expoConfig?.version ?? "0.0.0"}`;

/**
 * 기기 안 음성인식 (`expo-speech-recognition` · #595). Expo Go 에는 없어서 `null` 이다.
 * 🚨 패키지 본체를 import 하지 않는다 — 본체는 `requireNativeModule` 이라 모듈이 없으면 셸이 뜨지 않는다.
 */
const SpeechRecognizer = requireOptionalNativeModule<typeof ExpoSpeechRecognitionModule>(
  "ExpoSpeechRecognition",
);

/** 이 휴대폰에서 말로 적기를 켤 수 있는가 — 기기 안 인식이 보장될 때만 (`src/native/speech.ts` 머리말). */
const SPEECH_AVAILABLE = (() => {
  if (!SpeechRecognizer) return false;
  try {
    return canListenOnDevice({
      os: Platform.OS,
      apiLevel: typeof Platform.Version === "number" ? Platform.Version : 0,
      onDevice: SpeechRecognizer.supportsOnDeviceRecognition(),
    });
  } catch {
    return false;
  }
})();

function Shell() {
  const webViewRef = useRef<WebView>(null);
  const canGoBackRef = useRef(false);
  const urlRef = useRef(WEB_URL);
  /** 직전에 종료 안내를 띄운 시각 — 그 뒤 `EXIT_WINDOW_MS` 안에 또 누르면 닫는다. */
  const exitHintAtRef = useRef<number | null>(null);

  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  /**
   * 🚨 **위아래 여백을 셸이 먹지 않는다.** 그 자리는 웹뷰가 그대로 덮고, 얼마나 비워야 하는지만
   *    페이지에 넘긴다 (`src/native/safe-area.ts`). 셸이 칠하면 페이지 색과 어긋나 흰 띠가 남는다.
   */
  const insets = useSafeAreaInsets();

  /**
   * 키보드가 열려 있는지 — 열려 있으면 아래 여백을 0 으로 넘긴다 (`shellInsets` · #462).
   * 🚨 Android 만 켠다. iOS 는 기기에서 확인하기 전이다.
   */
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const webInsets = useMemo(() => shellInsets(insets, keyboardOpen), [insets, keyboardOpen]);

  /**
   * Android 물리 뒤로가기 (`src/native/back-press.ts` · #462).
   *
   * 하단 네비 네 칸에서는 히스토리가 있어도 뒤로 가지 않고 닫는 쪽으로 간다. 닫힐 차례의 첫 누름은
   * 안내 토스트만 띄우고, 바로 한 번 더 누르면 닫는다.
   *
   * 닫을 때는 `false` 를 돌려 OS 기본 동작(`MainActivity.invokeDefaultOnBackPressed`)에 넘긴다 —
   * 고치기 전에 히스토리가 다 떨어졌을 때 타던 길과 같다.
   *
   * ⚠️ **앱을 뒤로 보내는 게 아니라 액티비티를 끝낸다** (Android 17 실기기 `finishActivity` 확인).
   *    프로세스는 남아도 웹뷰가 새로 만들어진다. 로그인은 웹이 refresh 쿠키로 되살리지만(#465) 보던 화면이
   *    아니라 홈에서 다시 연다. 뒤로 보내기만 하려면 `moveTaskToBack` 을 부르는 네이티브 코드가 필요하다.
   */
  const handleHardwareBack = useCallback(() => {
    const now = Date.now();
    const action = decideBack({
      atRoot: isRootUrl(urlRef.current),
      canGoBack: canGoBackRef.current,
      now,
      lastPressAt: exitHintAtRef.current,
    });
    if (action === "go-back") {
      webViewRef.current?.goBack();
      return true;
    }
    if (action === "confirm-exit") {
      exitHintAtRef.current = now;
      ToastAndroid.show(EXIT_HINT, ToastAndroid.SHORT);
      return true;
    }
    exitHintAtRef.current = null;
    return false;
  }, []);

  // ⚠️ 웹의 클라이언트 이동(`history.pushState`)도 Android 는 여기로 온다 —
  //    react-native-webview 가 `doUpdateVisitedHistory` 를 로딩 이벤트로 넘긴다.
  const onNavigationStateChange = useCallback((state: WebViewNavigation) => {
    canGoBackRef.current = state.canGoBack;
    urlRef.current = state.url;
  }, []);

  /**
   * 로그인 시작 URL 을 인앱 인증 세션으로 열고, 돌아오면 웹의 복귀 화면으로 옮긴다
   * (`src/native/auth-session.ts` 머리말).
   *
   * `success` 일 때만 옮긴다. X 로 닫은 것(`cancel` · `dismiss`)은 셸이 할 일이 없다 —
   * 00 화면이 `visibilitychange` 로 "로그인하는 중…" 을 스스로 푼다.
   *
   * ⚠️ Android 의 결과는 `Linking` 으로 들어온 복귀 URL(→ `success`)과 앱이 다시 앞으로 온 것
   *    (→ `dismiss`) 중 **먼저 온 쪽**이다. 복귀 때는 `onNewIntent` 가 `onResume` 보다 먼저라
   *    `success` 가 이긴다. 이 순서가 뒤집히면 코드가 조용히 사라지니, 셸을 고치면 기기에서 다시 본다.
   */
  const openAuthSession = useCallback(async (url: string) => {
    try {
      const result = await WebBrowser.openAuthSessionAsync(url, AUTH_RETURN_URL);
      if (result.type !== "success") return;
      const callbackUrl = callbackUrlFromReturn(result.url, WEB_URL);
      if (callbackUrl) webViewRef.current?.injectJavaScript(navigateScript(callbackUrl));
    } catch {
      // 세션이 이미 열려 있다(연타) — 먼저 연 세션이 복귀를 받는다.
    }
  }, []);

  /**
   * 앱 밖 링크를 연다 — 웹 주소는 앱 위에 얹는 in-app 브라우저로, 그 밖은 OS 에 (`src/native/in-app-browser.ts`).
   *
   * 🚨 in-app 브라우저를 못 열면 `Linking` 으로 넘긴다 — Custom Tabs 를 지원하는 브라우저가 없는 기기에서
   *    링크가 아무 일도 안 하게 두지 않는다. 지금까지의 동작이 곧 이 대체 경로다.
   */
  const openExternal = useCallback(async (url: string) => {
    if (isWebUrl(url)) {
      try {
        // 제목을 띄운다 — 약관 주소(`…/policies/service_terms/draft-1`)보다 문서 제목이 무엇을 여는지 말해 준다.
        await WebBrowser.openBrowserAsync(url, { showTitle: true });
        return;
      } catch {
        // 아래로 — 시스템 브라우저
      }
    }
    void Linking.openURL(url);
  }, []);

  const reload = useCallback(() => {
    setFailed(false);
    setLoading(true);
    webViewRef.current?.reload();
  }, []);

  useEffect(() => {
    if (Platform.OS !== "android") return;
    const subscription = BackHandler.addEventListener("hardwareBackPress", handleHardwareBack);
    return () => subscription.remove();
  }, [handleHardwareBack]);

  useEffect(() => {
    if (Platform.OS !== "android") return;
    const shown = Keyboard.addListener("keyboardDidShow", () => setKeyboardOpen(true));
    const hidden = Keyboard.addListener("keyboardDidHide", () => setKeyboardOpen(false));
    return () => {
      shown.remove();
      hidden.remove();
    };
  }, []);

  /**
   * 백그라운드로 갈 때 웹뷰 쿠키를 디스크에 쓴다 (`src/native/cookie-flush.ts` · #469). 안 그러면 앱을
   * 켜고 바로 끌 때 회전된 refresh 쿠키가 사라져 다음 실행에서 서버가 세션을 지운다.
   * 모듈이 없는 iOS · Expo Go 에서는 아무것도 하지 않는다.
   */
  useEffect(() => {
    const native = CookieFlush;
    const flush = native ? () => native.flush() : undefined;
    const subscription = AppState.addEventListener("change", (state) =>
      flushOnBackground(state, flush),
    );
    return () => subscription.remove();
  }, []);

  /**
   * 인식기가 지금 듣고 있는가 — `start()` 를 부른 뒤 `end` 가 올 때까지.
   * 🚨 이게 참일 때만 `abort()` 한다. 라이브러리의 `abort()` 는 상태를 보지 않고 늘 웹에 `end` 를 보내서, 첫 탭의
   *    권한 창(Android 는 이것도 `background` 다)에서 부르면 웹은 꺼진 줄 아는데 허용 뒤 셸은 듣기 시작한다.
   */
  const listeningRef = useRef(false);
  /**
   * 권한을 확인하는 동안 웹이 "그만" 을 보냈는가. 그때 인식기는 아직 없어서 `stop()` 이 아무 일도 안 하고
   * `end` 도 오지 않는다 — 확인이 끝난 뒤 시작하면 웹은 꺼진 줄 아는데 셸은 듣는다.
   */
  const stopRequestedRef = useRef(false);

  /**
   * 말로 적기의 진행을 웹에 알린다 (`src/native/speech.ts`).
   * 🚨 지금 떠 있는 문서가 우리 주소일 때만 보낸다 — 들은 글자를 남의 문서에 넣지 않는다.
   */
  const sendSpeech = useCallback((event: SpeechEvent) => {
    if (!isInternalUrl(urlRef.current)) return;
    webViewRef.current?.injectJavaScript(speechEventScript(event));
  }, []);

  /**
   * 듣기를 끊는다 — 문서가 새로 열리거나, 연결 실패 화면으로 가거나, 웹뷰가 죽을 때.
   * 🚨 웹의 정리(언마운트)는 React 가 컴포넌트를 내릴 때만 돈다. 새로고침 · 하드 이동처럼 문서가 통째로 바뀌면
   *    웹은 `stop` 을 못 보낼 수 있어서 셸이 끊는다. 권한을 확인하는 중이면 확인 뒤에 시작하지 않게 한다.
   */
  const abortListening = useCallback(() => {
    stopRequestedRef.current = true;
    if (listeningRef.current) SpeechRecognizer?.abort();
  }, []);

  /**
   * 듣기 시작 — 권한은 **처음 누를 때** 묻는다. 앱을 켤 때 묻지 않는다: 왜 필요한지 모르는 채로 뜬 창은
   * 거절당하고, Android 는 두 번 거절하면 다시 묻지 못한다.
   *
   * 🚨 `requiresOnDeviceRecognition` 을 빼지 않는다. 빼면 음성이 인식 서버로 간다 (#595).
   */
  const startListening = useCallback(async () => {
    const recognizer = SpeechRecognizer;
    if (!recognizer || !SPEECH_AVAILABLE) {
      sendSpeech({ type: "error", reason: "unsupported" });
      return;
    }
    stopRequestedRef.current = false;
    try {
      // 시스템 창이 뜰 때만 웹이 이유를 먼저 말한다 — Android 권한 창에는 우리 문장이 나오지 않는다.
      const current = await recognizer.getPermissionsAsync();
      if (!current.granted && current.canAskAgain) sendSpeech({ type: "asking" });
      const permission = await recognizer.requestPermissionsAsync();
      const refused = permissionReason(permission);
      if (refused) {
        sendSpeech({ type: "error", reason: refused });
        return;
      }
      if (stopRequestedRef.current) {
        sendSpeech({ type: "end" });
        return;
      }
      listeningRef.current = true;
      recognizer.start({
        lang: SPEECH_LANG,
        interimResults: true,
        continuous: true,
        requiresOnDeviceRecognition: true,
        // 웹의 음량 막대. 막대 다섯 개에 0.1초보다 촘촘할 이유가 없다.
        volumeChangeEventOptions: { enabled: true, intervalMillis: 100 },
      });
    } catch {
      listeningRef.current = false;
      sendSpeech({ type: "error", reason: "failed" });
    }
  }, [sendSpeech]);

  const onMessage = useCallback(
    (event: WebViewMessageEvent) => {
      const request = parseWebRequest(event.nativeEvent.data, event.nativeEvent.url, isInternalUrl);
      if (!request) return;
      if (request.action === "start") {
        void startListening();
        return;
      }
      stopRequestedRef.current = true;
      SpeechRecognizer?.stop();
    },
    [startListening],
  );

  /**
   * 인식기 이벤트 → 웹. 결과는 첫 후보의 글자만 넘긴다 — 웹이 신뢰도나 다른 후보를 쓸 데가 없다.
   *
   * 한국어 모델이 없으면(`language-not-supported`) 받기를 걸고 "준비하는 중" 이라고 알린다. 받는 것은
   * 인식 모델이고 음성이 아니다. 받기를 못 걸면 이 휴대폰에서는 못 쓰는 것이다.
   */
  useEffect(() => {
    const recognizer = SpeechRecognizer;
    if (!recognizer || !SPEECH_AVAILABLE) return;
    const subscriptions = [
      recognizer.addListener("start", () => sendSpeech({ type: "start" })),
      recognizer.addListener("volumechange", (event) =>
        sendSpeech({ type: "volume", level: volumeLevel(event.value) }),
      ),
      recognizer.addListener("result", (event) => {
        const text = event.results[0]?.transcript ?? "";
        sendSpeech({ type: "result", text, final: event.isFinal });
      }),
      recognizer.addListener("error", (event) => {
        if (event.error === "language-not-supported") {
          recognizer
            .androidTriggerOfflineModelDownload({ locale: SPEECH_LANG })
            .then(() => sendSpeech({ type: "error", reason: "preparing" }))
            .catch(() => sendSpeech({ type: "error", reason: "unsupported" }));
          return;
        }
        const reason = speechErrorReason(event.error);
        if (reason) sendSpeech({ type: "error", reason });
      }),
      recognizer.addListener("end", () => {
        listeningRef.current = false;
        sendSpeech({ type: "end" });
      }),
    ];
    return () => subscriptions.forEach((subscription) => subscription.remove());
  }, [sendSpeech]);

  // 🚨 화면을 떠나면 마이크를 끈다. 보호자가 안 보는 동안 듣고 있으면 안 된다.
  useEffect(() => {
    const recognizer = SpeechRecognizer;
    if (!recognizer || !SPEECH_AVAILABLE) return;
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "background" && listeningRef.current) recognizer.abort();
    });
    return () => subscription.remove();
  }, []);

  // 회전·시스템 바·키보드 변화로 값이 바뀌면 다시 넘긴다. 첫 값은 주입 스크립트가 이미 들고 간다.
  useEffect(() => {
    webViewRef.current?.injectJavaScript(safeAreaScript(webInsets));
  }, [webInsets]);

  return (
    <View style={styles.root}>
      <StatusBar style="dark" />

      {failed ? (
        // 🚨 실패 화면은 **셸이 그리는 유일한 화면**이라 여기서는 safe area 를 직접 먹는다.
        <SafeAreaView style={styles.center} edges={["top", "bottom", "left", "right"]}>
          <Text style={styles.errorTitle}>연결하지 못했어요</Text>
          <Text style={styles.errorBody}>
            네트워크를 확인하고 다시 시도해 주세요.{"\n"}
            {WEB_URL}
          </Text>
          <Pressable style={styles.retry} onPress={reload} accessibilityRole="button">
            <Text style={styles.retryLabel}>다시 시도</Text>
          </Pressable>
        </SafeAreaView>
      ) : (
        // 🚨 위아래는 비우지 않는다 — 페이지가 화면 끝까지 칠한다. 좌우(가로 노치)만 셸이 먹는다.
        <SafeAreaView style={styles.fill} edges={["left", "right"]}>
          <WebView
            ref={webViewRef}
            source={{ uri: WEB_URL }}
            applicationNameForUserAgent={USER_AGENT_SUFFIX}
            onNavigationStateChange={onNavigationStateChange}
            // 문서가 새로 열리면 듣기를 끊는다 (`abortListening`). 웹의 주소 이동(`pushState`)은 여기로 오지 않는다.
            onLoadStart={abortListening}
            onLoadEnd={() => setLoading(false)}
            onError={() => {
              abortListening();
              setLoading(false);
              setFailed(true);
            }}
            onHttpError={({ nativeEvent }) => {
              if (nativeEvent.statusCode < 500) return;
              abortListening();
              setFailed(true);
            }}
            // 🚨 웹뷰 렌더 프로세스가 죽으면 마이크를 끊고 연결 실패 화면으로 간다. 이 처리가 없으면 앱이 같이 죽고,
            //    죽은 웹뷰는 다시 쓸 수 없어서 "다시 시도" 가 새 웹뷰를 띄운다 (`failed` 가 웹뷰를 내린다).
            onRenderProcessGone={() => {
              abortListening();
              setLoading(false);
              setFailed(true);
            }}
            // 🚨 `target="_blank"` 를 **이 웹뷰의 이동으로 만든다.** 안드로이드 기본값(true)이면
            //    새 창을 띄울 곳이 없어 링크가 **아무 일도 하지 않는다** — 동의 화면의 "전문 보기"
            //    가 그렇게 죽는다. false 로 두면 아래 `onShouldStartLoadWithRequest` 를 거쳐
            //    앱 밖 주소로 판정되고 in-app 브라우저로 넘어간다.
            setSupportMultipleWindows={false}
            // 앱 밖 링크(약관 등)는 웹뷰에 가두지 않고 in-app 브라우저로 앱 위에 얹는다 (#212).
            // 🚨 로그인 시작은 따로 — 복귀(`icatch://auth`)를 받아야 해서 인증 세션으로 연다 (#210).
            // 🚨 출처가 같아도 `/api/…` 는 웹뷰 밖이다 (#273) — 안 그러면 nginx 로 API 를 웹 주소 아래에
            //    둔 날 로그인 시작이 아래 두 번째 분기까지 오지 못한다 (`src/native/internal-url.ts`).
            onShouldStartLoadWithRequest={(request) => {
              if (isInternalUrl(request.url)) return true;
              if (isAuthStartUrl(request.url)) {
                void openAuthSession(request.url);
                return false;
              }
              void openExternal(request.url);
              return false;
            }}
            // 페이지가 뜨기 전에 상태바·제스처바가 먹는 자리와 말로 적기 창구를 꽂는다
            // (`src/native/safe-area.ts` · `src/native/speech.ts`).
            injectedJavaScriptBeforeContentLoaded={
              SAFE_AREA_SCRIPT + safeAreaScript(webInsets) + speechBridgeScript(SPEECH_AVAILABLE)
            }
            // 🚨 웹의 부탁은 말로 적기 시작 · 멈춤뿐이다. 보낸 쪽 주소와 모양을 검사한 것만 받는다
            //    (`parseWebRequest` · CLAUDE.md §4).
            onMessage={onMessage}
            // 08 사진 분석 — <input type="file"> 이 동작하려면 필요하다.
            allowFileAccess
            allowsInlineMediaPlayback
            mediaPlaybackRequiresUserAction={false}
            // SSE(/runs/{rid}/events) 가 버퍼링 없이 흐르도록.
            cacheEnabled={false}
            // 웹이 이미 자체 스크롤·바운스를 제어한다.
            bounces={false}
            overScrollMode="never"
            style={styles.webview}
          />
          {loading ? (
            <View style={styles.loadingOverlay} pointerEvents="none">
              <ActivityIndicator size="large" color={BRAND} />
            </View>
          ) : null}
        </SafeAreaView>
      )}
    </View>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <Shell />
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: CANVAS },
  fill: { flex: 1 },
  webview: { flex: 1, backgroundColor: CANVAS },
  loadingOverlay: {
    ...StyleSheet.absoluteFill,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: CANVAS,
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 12 },
  errorTitle: { fontSize: 18, fontWeight: "700", color: INK },
  errorBody: { fontSize: 14, lineHeight: 21, color: INK_MUTED, textAlign: "center" },
  retry: {
    marginTop: 8,
    paddingHorizontal: 20,
    paddingVertical: 11,
    borderRadius: 10,
    backgroundColor: BRAND,
  },
  retryLabel: { color: "#ffffff", fontSize: 15, fontWeight: "600" },
});
