/**
 * Idempotency-Key — 되돌릴 수 없는 엔드포인트의 **단일 출처**.
 *
 * 계약서 v1 §01: "중복 실행이 기억을 두 번 쌓거나 캘린더에 두 번 쓰는 엔드포인트에만
 * 필수다. 헤더가 없으면 400."
 *
 * 서버가 지켜야 하는 동작(재시도 재생 · 키 재사용 거부 · 동시 요청 차단)은
 * docs/api/idempotency-v1.md 에 있다. 이 파일은 그 계약의 **클라이언트 쪽 절반**이다.
 *
 * 🚨 경로를 여기 표에서만 만든다. 문자열로 직접 조립하면 아래 두 가지가 동시에 깨진다 —
 *    ① request() 의 누락 검사가 이 표에서 정규식을 만든다
 *    ② operations.ts 의 "키가 필수 인자인 함수" 도 이 표에서 경로를 받는다
 *    즉 표를 거치지 않고는 되돌릴 수 없는 엔드포인트를 부를 방법이 없다.
 */

/* ── 키 ───────────────────────────────────────────────────────────────── */

declare const IDEMPOTENCY_KEY_BRAND: unique symbol;

/**
 * 아무 문자열이나 키로 넘길 수 없게 브랜드를 붙인다.
 * `newIdempotencyKey()` 를 거치지 않은 값은 타입이 맞지 않는다.
 */
export type IdempotencyKey = string & { readonly [IDEMPOTENCY_KEY_BRAND]: true };

export function newIdempotencyKey(): IdempotencyKey {
  return crypto.randomUUID() as IdempotencyKey;
}

/**
 * 밖에서 들어온 문자열(저장해 둔 키 복원 등)을 키로 되돌린다.
 * 🚨 새 키를 만드는 용도가 아니다 — 재시도에 쓸 키는 `useIdempotencyKey()` 가 들고 있다.
 */
export function restoreIdempotencyKey(value: string): IdempotencyKey {
  return value as IdempotencyKey;
}

/* ── 되돌릴 수 없는 엔드포인트 ────────────────────────────────────────── */

/**
 * 계약서 §01 의 5개. 승인 게이트 2곳(㉠ confirmEvent · ㉡ healthSafety)이 여기 포함된다.
 * 여기에 줄을 더하면 아래 정규식과 operations.ts 가 자동으로 따라온다.
 */
export const idempotentPath = {
  /** 한 줄 입력 — 같은 한 줄이 관찰 N건씩 두 번 저장되는 것을 막는다. */
  input: (childId: string) => `/children/${childId}/inputs`,
  onboarding: (childId: string) => `/children/${childId}/onboarding`,
  photo: (childId: string) => `/children/${childId}/photos`,
  /** 🚨 승인 게이트 ㉡ — 알레르기·건강 기록 확정. */
  healthSafety: (childId: string) => `/children/${childId}/health-safety`,
  /** 🚨 승인 게이트 ㉠ — 캘린더 쓰기. */
  confirmEvent: (eventId: string) => `/events/${eventId}/confirm`,
} as const satisfies Record<string, (id: string) => string>;

export type IdempotentOperation = keyof typeof idempotentPath;

const ID_SLOT = "__ID__";

/** 경로 빌더에서 정규식을 만든다 — 목록을 손으로 두 번 쓰지 않기 위해서다. */
function pathPattern(build: (id: string) => string): RegExp {
  const segments = build(ID_SLOT)
    .split(ID_SLOT)
    .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^${segments.join("[^/]+")}$`);
}

const REQUIRED_PATTERNS: RegExp[] = Object.values(idempotentPath).map(pathPattern);

/** `/children/c1/inputs` 처럼 이미 만들어진 경로가 키를 요구하는지. */
export function requiresIdempotencyKey(path: string): boolean {
  return REQUIRED_PATTERNS.some((pattern) => pattern.test(path));
}

/**
 * 키 없이 되돌릴 수 없는 엔드포인트를 부르려 했을 때. **요청은 나가지 않는다.**
 *
 * dev 콘솔 경고가 아니라 예외인 이유 — 경고는 프로덕션에서 실행되지 않고,
 * 실행되더라도 요청이 그대로 나가 서버의 400 에 의존하게 된다. 중복 실행을 막는 장치가
 * "서버가 거부해 주기를 기대하는 것" 하나뿐이면 장치가 아니다.
 */
export class IdempotencyKeyRequiredError extends Error {
  readonly path: string;

  constructor(path: string) {
    super(
      `${path} 은 되돌릴 수 없는 엔드포인트다 — Idempotency-Key 없이 부를 수 없다. ` +
        `lib/api/operations.ts 의 전용 함수를 쓰고, 키는 useIdempotencyKey() 로 받는다.`,
    );
    this.name = "IdempotencyKeyRequiredError";
    this.path = path;
  }
}
