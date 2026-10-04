import Constants from "expo-constants";
import { StatusBar } from "expo-status-bar";
import * as WebBrowser from "expo-web-browser";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  BackHandler,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { WebView, type WebViewMessageEvent, type WebViewNavigation } from "react-native-webview";

import { WEB_URL, isInternalUrl } from "./src/config";
import {
  AUTH_RETURN_URL,
  callbackUrlFromReturn,
  isAuthStartUrl,
  navigateScript,
} from "./src/native/auth-session";
import {
  BRIDGE_SCRIPT,
  chunkScript,
  parseBridgeRequest,
  settleScript,
  splitIntoChunks,
  type BridgeSettlement,
} from "./src/native/bridge";
import { listRecentPhotos, readRecentPhoto } from "./src/native/recent-photos";
import { SAFE_AREA_SCRIPT, safeAreaScript } from "./src/native/safe-area";

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

function Shell() {
  const webViewRef = useRef<WebView>(null);
  const canGoBackRef = useRef(false);

  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  /**
   * 🚨 **위아래 여백을 셸이 먹지 않는다.** 그 자리는 웹뷰가 그대로 덮고, 얼마나 비워야 하는지만
   *    페이지에 넘긴다 (`src/native/safe-area.ts`). 셸이 칠하면 페이지 색과 어긋나 흰 띠가 남는다.
   */
  const insets = useSafeAreaInsets();

  // Android 물리 뒤로가기 → 웹뷰 히스토리 뒤로. 더 갈 곳이 없으면 앱을 닫는다.
  const handleHardwareBack = useCallback(() => {
    if (canGoBackRef.current) {
      webViewRef.current?.goBack();
      return true;
    }
    return false;
  }, []);

  const onNavigationStateChange = useCallback((state: WebViewNavigation) => {
    canGoBackRef.current = state.canGoBack;
  }, []);

  /**
   * 브릿지의 답을 웹에 돌려준다. 큰 값은 조각으로 먼저 보내고 마지막에 끝을 알린다
   * (`src/native/bridge.ts` 머리말).
   */
  const respond = useCallback((settlement: BridgeSettlement, body?: string) => {
    const webView = webViewRef.current;
    if (!webView) return;
    if (body) {
      for (const chunk of splitIntoChunks(body)) {
        webView.injectJavaScript(chunkScript(settlement.id, chunk));
      }
    }
    webView.injectJavaScript(settleScript(settlement));
  }, []);

  /**
   * 웹이 `window.icatch.recentPhotos` 로 부른 것을 받는다.
   *
   * 🚨 **어떤 경우에도 답은 돌려준다.** 답이 없으면 08 시트의 썸네일이 누른 채로 멈춘다 —
   *    실패는 `kind: "empty"` 로 알리고, 웹은 그걸 "줄을 안 그린다 / 그 사진을 못 읽었어요" 로 받는다.
   */
  const handleMessage = useCallback(
    async (event: WebViewMessageEvent) => {
      /*
       * 🚨 **보낸 쪽이 우리 페이지인지 먼저 본다** (#144 리뷰).
       *
       * 여기는 아이 사진 **원본이 기기 밖으로 나가는 유일한 문**이다. 탐색 제한
       * (`onShouldStartLoadWithRequest`) 한 겹에만 기대면, 리다이렉트 같은 예외 경로로 그것이
       * 한 번이라도 뚫렸을 때 외부 페이지가 사진을 받아 간다.
       *
       * 이 `url` 은 **보낸 프레임** 기준이다 — iOS 는 `frameInfo.request.URL`, Android 는
       * `WebMessageListener` 의 `sourceOrigin`. 그래서 크로스 오리진 iframe 이 부르는 것까지 막힌다.
       *
       * ⚠️ WebViewClient 가 붙기 전·떨어진 뒤의 찰나에는 이벤트에 `url` 이 안 실린다. 그때는
       *    여기서 걸러져 **요청이 조용히 버려진다** — 창구가 아직 살아 있지 않다는 뜻이라 맞는 동작이고,
       *    웹은 응답이 없으면 타임아웃으로 빈 값을 받는다 (`src/native/bridge.ts`).
       */
      if (!isInternalUrl(event.nativeEvent.url)) return;

      const request = parseBridgeRequest(event.nativeEvent.data);
      if (!request) return;

      try {
        if (request.method === "recentPhotos.list") {
          const photos = await listRecentPhotos(request.limit);
          if (photos.length === 0) {
            respond({ id: request.id, kind: "empty" });
            return;
          }
          respond({ id: request.id, kind: "json" }, JSON.stringify(photos));
          return;
        }

        const original = await readRecentPhoto(request.photoId);
        if (!original) {
          respond({ id: request.id, kind: "empty" });
          return;
        }
        respond(
          {
            id: request.id,
            kind: "file",
            mimeType: original.mimeType,
            filename: original.filename,
          },
          original.base64,
        );
      } catch {
        // 🚨 무엇이 터졌는지 찍지 않는다 — 예외 메시지에 사진 경로가 섞여 들어온다 (CLAUDE.md §2).
        respond({ id: request.id, kind: "empty" });
      }
    },
    [respond],
  );

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

  // 회전·시스템 바 변화로 값이 바뀌면 다시 넘긴다. 첫 값은 주입 스크립트가 이미 들고 간다.
  useEffect(() => {
    webViewRef.current?.injectJavaScript(safeAreaScript(insets));
  }, [insets]);

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
            onLoadEnd={() => setLoading(false)}
            onError={() => {
              setLoading(false);
              setFailed(true);
            }}
            onHttpError={({ nativeEvent }) => {
              if (nativeEvent.statusCode >= 500) setFailed(true);
            }}
            // 🚨 `target="_blank"` 를 **이 웹뷰의 이동으로 만든다.** 안드로이드 기본값(true)이면
            //    새 창을 띄울 곳이 없어 링크가 **아무 일도 하지 않는다** — 동의 화면의 "전문 보기"
            //    가 그렇게 죽는다. false 로 두면 아래 `onShouldStartLoadWithRequest` 를 거쳐
            //    앱 밖 주소로 판정되고 시스템 브라우저로 넘어간다.
            setSupportMultipleWindows={false}
            // 앱 밖 링크(약관 등)는 웹뷰에 가두지 않고 시스템 브라우저로 넘긴다.
            // 🚨 로그인 시작만 예외다 — 시스템 브라우저로 나가면 복귀를 받을 곳이 없다 (#210).
            onShouldStartLoadWithRequest={(request) => {
              if (isInternalUrl(request.url)) return true;
              if (isAuthStartUrl(request.url)) {
                void openAuthSession(request.url);
                return false;
              }
              void Linking.openURL(request.url);
              return false;
            }}
            // 페이지가 뜨기 전에 꽂는 것 둘 — 최근 사진 창구(`src/native/bridge.ts`)와
            // 상태바·제스처바가 먹는 자리(`src/native/safe-area.ts`). 각 스크립트가 자기 끝을
            // `true;` 로 닫으므로 이어 붙여도 서로를 건드리지 않는다.
            // 🚨 `...BeforeContentLoaded` 여야 웹의 개발용 가짜 브릿지보다 먼저 자리를 잡는다.
            injectedJavaScriptBeforeContentLoaded={
              BRIDGE_SCRIPT + SAFE_AREA_SCRIPT + safeAreaScript(insets)
            }
            onMessage={handleMessage}
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
