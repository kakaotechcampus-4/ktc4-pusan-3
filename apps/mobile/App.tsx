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
import { WebView, type WebViewMessageEvent, type WebViewNavigation } from "react-native-webview";

import { WEB_URL, isInternalUrl } from "./src/config";
import {
  BRIDGE_SCRIPT,
  chunkScript,
  parseBridgeRequest,
  settleScript,
  splitIntoChunks,
  type BridgeSettlement,
} from "./src/native/bridge";
import { listRecentPhotos, readRecentPhoto } from "./src/native/recent-photos";

/** 웹 쪽에서 네이티브임을 알아볼 수 있게 붙인다 (예: 웹뷰에서만 다른 UI). */
const USER_AGENT_SUFFIX = `IcatchApp/${Constants.expoConfig?.version ?? "0.0.0"}`;

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
            // 08 사진 시트의 "최근 사진" 줄 — 웹은 기기 갤러리 목록을 읽을 수 없어서 셸이 꽂아 준다.
            // 🚨 `...BeforeContentLoaded` 여야 웹의 개발용 가짜 브릿지보다 먼저 자리를 잡는다.
            injectedJavaScriptBeforeContentLoaded={BRIDGE_SCRIPT}
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
