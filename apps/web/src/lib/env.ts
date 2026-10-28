import { z } from "zod";

/**
 * 브라우저 번들에 실려도 되는 값만 여기 둔다.
 * NEXT_PUBLIC_* 는 빌드 시점에 문자열로 인라인되므로, 반드시 아래처럼 통째로 참조해야 한다.
 * (process.env[key] 같은 동적 접근은 치환되지 않는다.)
 */
const publicEnvSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z.url({ error: "NEXT_PUBLIC_API_BASE_URL 이 URL 형식이 아니다" }),
  /** "enabled" 면 MSW 목 서버가 뜬다. 개발 환경에서만 동작한다 (src/mocks/start.ts). */
  NEXT_PUBLIC_API_MOCKING: z.enum(["enabled", "disabled"]).default("disabled"),
});

const parsed = publicEnvSchema.safeParse({
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_API_MOCKING: process.env.NEXT_PUBLIC_API_MOCKING,
});

if (!parsed.success) {
  throw new Error(
    `환경변수가 올바르지 않다. .env.example 을 .env.local 로 복사했는지 확인할 것.\n${z.prettifyError(parsed.error)}`,
  );
}

export const env = parsed.data;

/** 모든 엔드포인트는 /api/v1 하위다 (docs/api/api-interface-v1.html §01). */
export const API_BASE_URL = `${env.NEXT_PUBLIC_API_BASE_URL.replace(/\/$/, "")}/api/v1`;

/* ── 웹 푸시 (#609) ──────────────────────────────────────────────────── */

/** 웹 푸시에 쓰는 Firebase 값. 공개 식별자라 번들에 들어가도 된다 (`.env.example`). */
export interface FirebaseWebConfig {
  apiKey: string;
  projectId: string;
  messagingSenderId: string;
  appId: string;
  /** 웹 푸시 인증서의 공개 키. 🚨 바꾸면 그 전에 받은 웹 토큰이 맞지 않는다. */
  vapidKey: string;
}

const FIREBASE_KEYS = {
  apiKey: "NEXT_PUBLIC_FIREBASE_API_KEY",
  projectId: "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
  messagingSenderId: "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID",
  appId: "NEXT_PUBLIC_FIREBASE_APP_ID",
  vapidKey: "NEXT_PUBLIC_FIREBASE_VAPID_KEY",
} as const satisfies Record<keyof FirebaseWebConfig, string>;

/**
 * 다섯 값을 **전부 넣었을 때만** 웹 푸시를 켠다. 하나도 없으면 `null` — 꺼진 것이다.
 *
 * 🚨 **비어 있는 것이 정상인 환경이 있다.** 값이 없는 팀원의 로컬 · CI · 그리고 운영도 그렇다 —
 *    기기 토큰 수집 동의와 처리방침이 나가기 전에는 운영에서 기기 등록을 켜지 않는다(#605).
 *    `NEXT_PUBLIC_API_BASE_URL` 처럼 없으면 부팅을 막지 않는 이유다.
 * 🚨 **일부만 넣었으면 부팅을 막는다.** 넣으려다 하나를 빠뜨린 것이라, 조용히 꺼 두면 "알림이 안 와요"
 *    를 한참 찾게 된다. 빈 문자열은 안 넣은 것으로 본다(`.env.example` 을 그대로 복사한 경우).
 */
export function firebaseWebConfigFrom(
  raw: Record<keyof FirebaseWebConfig, string | undefined>,
): FirebaseWebConfig | null {
  const entries = Object.entries(raw).map(([key, value]) => [key, value?.trim() ?? ""] as const);
  const missing = entries.filter(([, value]) => value === "").map(([key]) => key);
  if (missing.length === entries.length) return null;
  if (missing.length > 0) {
    const names = missing.map((key) => FIREBASE_KEYS[key as keyof FirebaseWebConfig]);
    throw new Error(
      `웹 푸시 환경변수가 일부만 들어 있다. 다섯 개를 다 넣거나 다 비울 것: ${names.join(", ")}`,
    );
  }
  return Object.fromEntries(entries) as unknown as FirebaseWebConfig;
}

export const FIREBASE_WEB = firebaseWebConfigFrom({
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
  vapidKey: process.env.NEXT_PUBLIC_FIREBASE_VAPID_KEY,
});
