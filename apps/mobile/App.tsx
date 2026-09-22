import Constants from "expo-constants";
import { StatusBar } from "expo-status-bar";
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
import { WebView, type WebViewNavigation } from "react-native-webview";

import { WEB_URL, isInternalUrl } from "./src/config";
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
            // 앱 밖 링크(약관·소셜 로그인 등)는 웹뷰에 가두지 않고 시스템 브라우저로 넘긴다.
            onShouldStartLoadWithRequest={(request) => {
              if (isInternalUrl(request.url)) return true;
              void Linking.openURL(request.url);
              return false;
            }}
            // 상태바·제스처바가 먹는 자리를 페이지에 알려 준다 (`src/native/safe-area.ts`).
            injectedJavaScriptBeforeContentLoaded={SAFE_AREA_SCRIPT + safeAreaScript(insets)}
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
