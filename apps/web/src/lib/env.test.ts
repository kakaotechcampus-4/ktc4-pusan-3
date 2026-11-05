import { describe, expect, it } from "vitest";

import { firebaseWebConfigFrom } from "@/lib/env";

/**
 * 🚨 **웹 푸시는 다섯 값을 다 넣었을 때만 켜진다** (#609). 비어 있는 것은 정상이고(값이 없는 로컬 · CI ·
 *    동의 전의 운영), 일부만 넣은 것은 실수라 부팅을 막는다.
 */
describe("웹 푸시 Firebase 값", () => {
  const full = {
    apiKey: "key",
    projectId: "project",
    messagingSenderId: "123",
    appId: "1:123:web:abc",
    vapidKey: "vapid",
  };

  it("하나도 없으면 꺼진다 — 부팅을 막지 않는다", () => {
    expect(
      firebaseWebConfigFrom({
        apiKey: undefined,
        projectId: undefined,
        messagingSenderId: undefined,
        appId: undefined,
        vapidKey: undefined,
      }),
    ).toBeNull();
  });

  it("빈 문자열과 공백은 안 넣은 것이다 — .env.example 을 그대로 복사한 경우", () => {
    expect(
      firebaseWebConfigFrom({
        apiKey: "",
        projectId: " ",
        messagingSenderId: "",
        appId: "",
        vapidKey: "",
      }),
    ).toBeNull();
  });

  it("다 넣으면 그 값이다", () => {
    expect(firebaseWebConfigFrom(full)).toEqual(full);
  });

  it("🚨 일부만 넣으면 빠진 이름을 대며 멈춘다", () => {
    expect(() => firebaseWebConfigFrom({ ...full, vapidKey: "", appId: undefined })).toThrow(
      /NEXT_PUBLIC_FIREBASE_APP_ID, NEXT_PUBLIC_FIREBASE_VAPID_KEY/,
    );
  });
});
