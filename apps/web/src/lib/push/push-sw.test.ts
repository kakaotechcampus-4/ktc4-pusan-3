import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { describe, expect, it, vi } from "vitest";

import { pushOpenPath } from "@/lib/push/web-push";

/**
 * `public/push-sw.js` 를 가짜 서비스 워커 전역에서 실제로 돌린다 (#609).
 *
 * 🚨 거는 것: 받은 푸시를 띄우고, 누르면 그날 09 를 연다. 그리고 워커가 만든 주소를 페이지
 *    (`pushOpenPath`)가 받아 준다 — 두 파일이 같은 모양을 따로 들고 있어서 어긋나면 누를 때 아무 일도 없다.
 */
const SOURCE = readFileSync(new URL("../../../public/push-sw.js", import.meta.url), "utf8");
const ORIGIN = "https://icatch.ai.kr";
const CHILD = "7c1d0c2e-5b4a-4f1e-9d3a-0a1b2c3d4e5f";

type Listener = (event: unknown) => void;

function loadWorker({ tabs = [] as Array<{ url: string }> } = {}) {
  const listeners = new Map<string, Listener>();
  const showNotification = vi.fn(async () => {});
  const openWindow = vi.fn(async () => null);
  const windows = tabs.map((tab) => ({
    url: tab.url,
    focus: vi.fn(async function (this: unknown) {
      return this;
    }),
    postMessage: vi.fn(),
  }));
  const self = {
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
    skipWaiting: vi.fn(),
    location: { origin: ORIGIN },
    registration: { showNotification },
    clients: { matchAll: vi.fn(async () => windows), openWindow },
  };
  runInNewContext(SOURCE, { self, URL });

  /** 이벤트를 보내고 `waitUntil` 에 넘긴 일이 끝날 때까지 기다린다. */
  async function dispatch(type: string, event: Record<string, unknown>) {
    const pending: Promise<unknown>[] = [];
    listeners.get(type)?.({ ...event, waitUntil: (p: Promise<unknown>) => pending.push(p) });
    await Promise.all(pending);
  }

  return { dispatch, showNotification, openWindow, windows };
}

function pushEvent(payload: unknown) {
  return {
    data: {
      json: () => {
        if (typeof payload === "string") throw new SyntaxError("not json");
        return payload;
      },
    },
  };
}

const REMINDER = {
  notification: { title: "일정 알림", body: "1시간 뒤에 일정이 있어요" },
  data: { type: "schedule_reminder", child_id: CHILD, event_id: "e1", date: "2026-11-02" },
  from: "1234",
  fcmMessageId: "m1",
};

describe("받은 푸시", () => {
  it("서버 문구 그대로 띄우고, 누르면 열 곳을 그날 09 로 단다", async () => {
    const worker = loadWorker();
    await worker.dispatch("push", pushEvent(REMINDER));

    expect(worker.showNotification).toHaveBeenCalledWith("일정 알림", {
      body: "1시간 뒤에 일정이 있어요",
      data: { path: `/child/${CHILD}/calendar?date=2026-11-02` },
    });
  });

  it("🚨 data 의 모양이 다르면 주소에 붙이지 않고 첫 화면을 단다", async () => {
    const worker = loadWorker();
    await worker.dispatch(
      "push",
      pushEvent({ ...REMINDER, data: { ...REMINDER.data, child_id: "../../evil", date: "x" } }),
    );

    expect(worker.showNotification).toHaveBeenCalledWith(
      "일정 알림",
      expect.objectContaining({ data: { path: "/" } }),
    );
  });

  it("JSON 이 아니거나 알림 칸이 없으면 띄우지 않는다", async () => {
    const worker = loadWorker();
    await worker.dispatch("push", pushEvent("plain text"));
    await worker.dispatch("push", pushEvent({ data: REMINDER.data }));

    expect(worker.showNotification).not.toHaveBeenCalled();
  });
});

describe("알림 누르기", () => {
  const path = `/child/${CHILD}/calendar?date=2026-11-02`;
  const click = () => ({ notification: { close: vi.fn(), data: { path } } });

  it("🚨 열린 탭이 있으면 새 탭 대신 그 탭을 앞으로 가져와 갈 곳을 보낸다", async () => {
    const worker = loadWorker({ tabs: [{ url: `${ORIGIN}/child/${CHILD}/home` }] });
    await worker.dispatch("notificationclick", click());

    expect(worker.windows[0].focus).toHaveBeenCalled();
    expect(worker.windows[0].postMessage).toHaveBeenCalledWith({ type: "icatch:push-open", path });
    expect(worker.openWindow).not.toHaveBeenCalled();
  });

  it("열린 탭이 없으면(다른 사이트 탭뿐이어도) 새로 연다", async () => {
    const worker = loadWorker({ tabs: [{ url: "https://example.com/" }] });
    await worker.dispatch("notificationclick", click());

    expect(worker.openWindow).toHaveBeenCalledWith(path);
    expect(worker.windows[0].postMessage).not.toHaveBeenCalled();
  });

  it("🚨 워커가 보낸 주소를 페이지가 받아 준다 — 두 파일의 모양이 같다", async () => {
    const worker = loadWorker({ tabs: [{ url: `${ORIGIN}/` }] });
    await worker.dispatch("push", pushEvent(REMINDER));
    const [, options] = worker.showNotification.mock.calls[0] as unknown as [
      string,
      { data: { path: string } },
    ];
    await worker.dispatch("notificationclick", {
      notification: { close: vi.fn(), data: options.data },
    });
    const [message] = worker.windows[0].postMessage.mock.calls[0];

    expect(pushOpenPath(message)).toBe(path);
  });
});

describe("페이지가 받는 메시지", () => {
  it("우리 모양이 아니면 받지 않는다 — 목 워커도 이 페이지에 메시지를 보낸다", () => {
    expect(pushOpenPath({ type: "MOCK_ACTIVATE" })).toBeNull();
    expect(pushOpenPath(null)).toBeNull();
    expect(pushOpenPath({ type: "icatch:push-open", path: "https://evil.example/" })).toBeNull();
    expect(pushOpenPath({ type: "icatch:push-open", path: "/child/c1/memories" })).toBeNull();
    expect(pushOpenPath({ type: "icatch:push-open", path: "/" })).toBe("/");
  });
});
