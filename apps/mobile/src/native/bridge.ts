/**
 * 웹뷰 브릿지 — **셸과 웹이 값을 주고받는 유일한 통로.**
 *
 * 웹은 `window.icatch.recentPhotos` 라는 창구만 알고, 그 창구가 무엇으로 만들어졌는지는 모른다
 * (`apps/web/src/lib/native/recent-photos.ts`). 이 파일이 그 창구를 웹뷰 안에 꽂고,
 * 창구에 들어온 요청을 네이티브가 알아들을 수 있는 모양으로 바꾼다.
 *
 * 🚨 **여기에 기능을 넣지 않는다.** 사진을 실제로 읽는 것은 `recent-photos.ts` 다.
 *    이 파일은 배달만 한다 — 창구가 늘어나도 배달 방식은 하나로 둔다.
 *
 * ## 왜 이렇게 생겼나
 *
 * - **웹 → 네이티브** 는 `postMessage` 한 줄(JSON)이다. 요청은 작아서 나눌 필요가 없다.
 * - **네이티브 → 웹** 은 `injectJavaScript` 로 스크립트를 밀어 넣는 것뿐이라 돌려줄 값을
 *   **스크립트 안에 적어야** 한다. 🚨 그래서 큰 값은 **나눠 보낸다** — 사진 원본 하나가
 *   base64 로 몇 MB 가 되는데, 한 번에 밀어 넣으면 Android WebView 가 조용히 잘라 먹는다.
 *   `__icatchChunk` 로 조각을 쌓고 `__icatchSettle` 로 끝을 알린다.
 * - 값은 전부 `JSON.stringify` 로 **JS 문자열 리터럴**을 만들어 끼운다. 따옴표 · 역슬래시를
 *   손으로 이스케이프하지 않는다 — 파일 이름에 따옴표가 하나 들어간 날 스크립트가 깨진다.
 */

/**
 * 한 번에 밀어 넣는 조각의 크기(문자 수).
 * 🚨 키우면 왕복이 줄지만 Android `evaluateJavascript` 가 큰 스크립트에서 불안정해진다.
 */
const CHUNK_SIZE = 96 * 1024;

/** 셸이 답하지 않을 때 웹이 영원히 기다리지 않도록 하는 한도. */
const TIMEOUT_MS = 60_000;

/** 웹이 보내는 요청. 이 모양이 아니면 무시한다. */
export type BridgeRequest =
  | { id: string; method: "recentPhotos.list"; limit: number }
  | { id: string; method: "recentPhotos.read"; photoId: string };

/** 셸이 돌려주는 끝맺음. 조각은 이미 다 갔고, 이건 "무엇이었는지" 만 말한다. */
export type BridgeSettlement =
  /** 값이 없다 — 권한 거부 · 읽기 실패. 웹은 `[]` 또는 `null` 로 받는다. */
  | { id: string; kind: "empty" }
  /** 조각을 이어 붙이면 JSON 이다. */
  | { id: string; kind: "json" }
  /** 조각을 이어 붙이면 base64 원본이다. 웹이 `File` 로 만든다. */
  | { id: string; kind: "file"; mimeType: string; filename: string };

/**
 * 들어온 메시지를 요청으로 읽는다. 우리 것이 아니면 `null`.
 *
 * 🚨 **웹이 보낸 값은 믿지 않는다.** 웹뷰는 우리 페이지만 띄우지만(`isInternalUrl`),
 *    모양이 어긋난 메시지 하나로 셸이 죽으면 안 된다.
 */
export function parseBridgeRequest(raw: string): BridgeRequest | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }

  if (typeof parsed !== "object" || parsed === null) return null;
  const message = parsed as Record<string, unknown>;
  if (message.icatch !== 1 || typeof message.id !== "string") return null;

  if (message.method === "recentPhotos.list") {
    const limit = typeof message.limit === "number" ? message.limit : 0;
    // 🚨 웹이 부른 숫자를 그대로 쓰지 않는다. 한 장씩 썸네일을 만들기 때문에 큰 값이 그대로
    //    시트가 열리는 시간이 된다.
    return { id: message.id, method: "recentPhotos.list", limit: Math.min(Math.max(limit, 0), 30) };
  }

  if (message.method === "recentPhotos.read" && typeof message.photoId === "string") {
    return { id: message.id, method: "recentPhotos.read", photoId: message.photoId };
  }

  return null;
}

/** 큰 값을 조각으로 나눈다. 빈 문자열이면 보낼 조각이 없다. */
export function splitIntoChunks(body: string): string[] {
  const chunks: string[] = [];
  for (let offset = 0; offset < body.length; offset += CHUNK_SIZE) {
    chunks.push(body.slice(offset, offset + CHUNK_SIZE));
  }
  return chunks;
}

/** 조각 하나를 웹에 쌓는 스크립트. */
export function chunkScript(id: string, chunk: string): string {
  return `window.__icatchChunk(${JSON.stringify(id)},${JSON.stringify(chunk)});true;`;
}

/** 끝을 알리는 스크립트. 이걸 받아야 웹의 Promise 가 풀린다. */
export function settleScript(settlement: BridgeSettlement): string {
  return `window.__icatchSettle(JSON.parse(${JSON.stringify(JSON.stringify(settlement))}));true;`;
}

/**
 * 페이지가 그려지기 **전에** 웹뷰에 꽂는 스크립트.
 *
 * 🚨 **`injectedJavaScriptBeforeContentLoaded` 여야 한다.** 웹의 개발용 가짜 브릿지
 *    (`apps/web/src/mocks/native-bridge.ts`)는 "이미 꽂혀 있으면 건드리지 않는다" 로 되어 있어서,
 *    진짜가 먼저 자리를 잡아야 셸 안에서 가짜 썸네일이 뜨는 일이 없다.
 *
 * 🚨 **ES5 로 쓴다.** 이 코드는 번들러를 거치지 않고 기기의 WebView 엔진이 그대로 읽는다.
 */
export const BRIDGE_SCRIPT = `(function () {
  if (window.__icatchBridge) return;

  var pending = {};
  var counter = 0;

  function call(method, params) {
    return new Promise(function (resolve) {
      var id = 'q' + ++counter;
      var entry = { resolve: resolve, chunks: [] };
      entry.timer = setTimeout(function () {
        if (pending[id]) { delete pending[id]; resolve(null); }
      }, ${TIMEOUT_MS});
      pending[id] = entry;

      var message = { icatch: 1, id: id, method: method };
      for (var key in params) message[key] = params[key];

      // 셸 밖(브라우저)에서는 이 창구 자체가 안 꽂히므로 여기 오지 않는다.
      window.ReactNativeWebView.postMessage(JSON.stringify(message));
    });
  }

  window.__icatchChunk = function (id, chunk) {
    var entry = pending[id];
    if (entry) entry.chunks.push(chunk);
  };

  window.__icatchSettle = function (settlement) {
    var entry = pending[settlement.id];
    if (!entry) return;
    delete pending[settlement.id];
    clearTimeout(entry.timer);
    if (settlement.kind === 'empty') { entry.resolve(null); return; }
    settlement.body = entry.chunks.join('');
    entry.resolve(settlement);
  };

  function base64ToBytes(base64) {
    var binary = atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  window.__icatchBridge = true;
  window.icatch = window.icatch || {};
  window.icatch.recentPhotos = {
    list: function (limit) {
      return call('recentPhotos.list', { limit: limit }).then(function (result) {
        if (!result || result.kind !== 'json') return [];
        try { return JSON.parse(result.body); } catch (error) { return []; }
      });
    },
    read: function (photoId) {
      return call('recentPhotos.read', { photoId: photoId }).then(function (result) {
        if (!result || result.kind !== 'file') return null;
        return new File([base64ToBytes(result.body)], result.filename, { type: result.mimeType });
      });
    }
  };
})();
true;`;
