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
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { WebView, type WebViewNavigation } from "react-native-webview";

import { WEB_URL, isInternalUrl } from "./src/config";

/** 웹 쪽에서 네이티브임을 알아볼 수 있게 붙인다 (예: 웹뷰에서만 다른 UI). */
const USER_AGENT_SUFFIX = `YukameoApp/${Constants.expoConfig?.version ?? "0.0.0"}`;

function Shell() {
  const webViewRef = useRef<WebView>(null);
  const canGoBackRef = useRef(false);

  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

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

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom", "left", "right"]}>
      <StatusBar style="dark" />

      {failed ? (
        <View style={styles.center}>
          <Text style={styles.errorTitle}>연결하지 못했어요</Text>
          <Text style={styles.errorBody}>
            네트워크를 확인하고 다시 시도해 주세요.{"\n"}
            {WEB_URL}
          </Text>
          <Pressable style={styles.retry} onPress={reload} accessibilityRole="button">
            <Text style={styles.retryLabel}>다시 시도</Text>
          </Pressable>
        </View>
      ) : (
        <>
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
              <ActivityIndicator size="large" color="#2f6f4e" />
            </View>
          ) : null}
        </>
      )}
    </SafeAreaView>
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
  safe: { flex: 1, backgroundColor: "#ffffff" },
  webview: { flex: 1, backgroundColor: "#ffffff" },
  loadingOverlay: {
    ...StyleSheet.absoluteFill,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#ffffff",
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 12 },
  errorTitle: { fontSize: 18, fontWeight: "700", color: "#1a1a17" },
  errorBody: { fontSize: 14, lineHeight: 21, color: "#6b6b63", textAlign: "center" },
  retry: {
    marginTop: 8,
    paddingHorizontal: 20,
    paddingVertical: 11,
    borderRadius: 10,
    backgroundColor: "#2f6f4e",
  },
  retryLabel: { color: "#ffffff", fontSize: 15, fontWeight: "600" },
});
