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
