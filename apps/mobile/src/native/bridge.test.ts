/**
 * 브릿지 왕복 점검 — **에뮬레이터 없이** 주입 스크립트와 셸의 응답이 맞물리는지 본다.
 *
 * 🚨 여기서 보는 것은 **배달**뿐이다. 사진을 실제로 읽는 것(`recent-photos.ts`)은 기기 없이
 *    확인할 수 없고, 그건 에뮬레이터 · 실기기의 몫이다. 대신 **기기에서 티가 잘 안 나는**
 *    것들을 여기서 막는다 — 조각이 어긋나 바이트가 한 개 틀어지거나, 파일 이름의 따옴표
 *    하나로 주입 스크립트가 깨지는 종류다.
 *
 * 실행: `pnpm test` (Node 내장 테스트 러너 — 별도 의존성 없음)
 */

import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";

import {
  BRIDGE_SCRIPT,
  chunkScript,
  parseBridgeRequest,
  settleScript,
  splitIntoChunks,
  type BridgeRequest,
  type BridgeSettlement,
} from "./bridge.ts";

/** 웹뷰 흉내 — 주입 스크립트를 돌리고, 웹이 보낸 메시지를 모은다. */
function mountWebView() {
  const posted: string[] = [];
  const sandbox: Record<string, unknown> = {
    setTimeout,
    clearTimeout,
    Promise,
    JSON,
    Uint8Array,
    File,
    Blob,
    atob,
    ReactNativeWebView: { postMessage: (message: string) => posted.push(message) },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(BRIDGE_SCRIPT, sandbox);

  return {
    /** 웹이 부르는 창구. 셸이 꽂아 준 그것이다. */
    bridge: (sandbox.window as { icatch: { recentPhotos: RecentPhotosBridge } }).icatch
      .recentPhotos,
    /** 방금 웹이 보낸 요청. 셸의 `onMessage` 가 받는 것과 같은 문자열을 읽는다. */
    lastRequest(): BridgeRequest {
      const request = parseBridgeRequest(posted[posted.length - 1]);
      assert.ok(request, "요청이 파싱되지 않았다");
      return request;
    },
    /** 셸이 답하는 것 — 조각을 먼저 밀어 넣고 끝을 알린다 (App.tsx 의 `respond` 와 같은 순서). */
    respond(settlement: BridgeSettlement, body?: string) {
      if (body) {
        for (const chunk of splitIntoChunks(body)) {
          vm.runInContext(chunkScript(settlement.id, chunk), sandbox);
        }
      }
      vm.runInContext(settleScript(settlement), sandbox);
    },
  };
}

interface RecentPhotosBridge {
  list(limit: number): Promise<{ id: string; thumbnailUrl: string }[]>;
  read(photoId: string): Promise<File | null>;
}

test("list — 셸이 준 목록이 그대로 웹의 배열이 된다", async () => {
  const webView = mountWebView();
  const listed = webView.bridge.list(12);

  const request = webView.lastRequest();
  assert.equal(request.method, "recentPhotos.list");

  const photos = [
    {
      id: "content://media/external/images/media/1",
      thumbnailUrl: "data:image/jpeg;base64,/9j/4AAQ",
    },
  ];
  webView.respond({ id: request.id, kind: "json" }, JSON.stringify(photos));

  assert.deepEqual(await listed, photos);
});

test("list — 웹이 부른 limit 을 그대로 믿지 않는다", async () => {
  const webView = mountWebView();
  const listed = webView.bridge.list(9999);

  const request = webView.lastRequest();
  assert.equal(request.method === "recentPhotos.list" && request.limit, 30);

  // 답을 안 주면 웹 쪽 타이머가 살아남는다 — 셸은 어떤 경우에도 답한다 (App.tsx).
  webView.respond({ id: request.id, kind: "empty" });
  await listed;
});

test("read — 조각으로 나눠 보낸 원본이 바이트 하나 틀리지 않고 File 이 된다", async () => {
  const webView = mountWebView();

  // 조각이 여러 개로 갈리는 크기여야 의미가 있다.
  const original = Buffer.alloc(700 * 1024);
  for (let i = 0; i < original.length; i++) original[i] = (i * 31) % 256;
  const base64 = original.toString("base64");
  assert.ok(splitIntoChunks(base64).length > 1, "조각이 하나면 나눠 보내는 길을 안 지난다");

  const read = webView.bridge.read("content://media/external/images/media/7");
  const request = webView.lastRequest();
  assert.equal(request.method, "recentPhotos.read");

  // 🚨 따옴표·역슬래시가 든 파일 이름으로 주입 스크립트가 깨지지 않는지 같이 본다.
  const filename = '아이 "알림장"\\ 2026-09-22.jpg';
  webView.respond({ id: request.id, kind: "file", mimeType: "image/jpeg", filename }, base64);

  const file = await read;
  assert.ok(file, "File 이 만들어지지 않았다");
  assert.equal(file.name, filename);
  assert.equal(file.type, "image/jpeg");
  assert.ok(Buffer.from(await file.arrayBuffer()).equals(original));
});

test("빈 답 — 권한 거부·읽기 실패는 예외가 아니라 빈 값이다", async () => {
  const webView = mountWebView();

  const listed = webView.bridge.list(12);
  webView.respond({ id: webView.lastRequest().id, kind: "empty" });
  // ⚠️ 주입 스크립트가 만든 배열은 다른 realm 이라 `deepEqual` 이 참조까지 본다. 길이로 본다.
  assert.equal((await listed).length, 0);

  const read = webView.bridge.read("content://media/external/images/media/7");
  webView.respond({ id: webView.lastRequest().id, kind: "empty" });
  assert.equal(await read, null);
});

test("웹이 보낸 값을 믿지 않는다 — 모양이 어긋난 메시지는 전부 무시", () => {
  assert.equal(parseBridgeRequest("not json"), null);
  assert.equal(parseBridgeRequest('{"hello":1}'), null);
  assert.equal(parseBridgeRequest('{"icatch":1,"id":"q1","method":"nope"}'), null);
  // photoId 가 문자열이 아니면 요청이 아니다 — 그대로 네이티브로 넘어가면 안 된다.
  const badPhotoId = '{"icatch":1,"id":"q1","method":"recentPhotos.read","photoId":7}';
  assert.equal(parseBridgeRequest(badPhotoId), null);
});
