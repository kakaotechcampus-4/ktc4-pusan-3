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

/* ── 키 수명 ──────────────────────────────────────────────────────────── */

export interface IdempotencyKeyHolder {
  /**
   * 지금 동작의 키. 같은 인자로 여러 번 불러도 같은 값이다 — 그게 재시도다.
   *
   * @param payload 이 키로 보낼 본문. **본문이 달라지면 새 키**를 돌려준다.
   *   서버는 같은 키에 다른 본문이 오면 `422 idempotency_key_reuse` 로 거절하는데
   *   (idempotency-v1 §0 서버 보장 ③), 거기 걸리는 경로가 실제로 있다 — 서버는 처리했는데
   *   **응답만 유실되면** 화면은 실패로 보이고, 보호자가 한 줄을 고쳐서 다시 보낸다.
   *   그때 본문만 바뀌고 키는 그대로라 계속 422 다 (PR #71 리뷰).
   *   본문이 하나뿐인 동작(확정 · 승인)은 넘기지 않는다.
   */
  current: (payload?: string) => IdempotencyKey;
  /** 다음 동작으로 넘어간다. 🚨 성공 응답을 받은 뒤에만 부른다. */
  rotate: () => void;
}

/**
 * 키 수명의 순수 구현. React 없이 검증할 수 있게 훅 밖에 둔다.
 * 화면에서는 `useIdempotencyKey()` 로 쓴다 — 렌더 사이에 이 객체를 유지하는 것이 훅의 일이다.
 */
export function createIdempotencyKeyHolder(): IdempotencyKeyHolder {
  let key: IdempotencyKey | null = null;
  let issuedFor: string | undefined;

  return {
    current(payload) {
      if (key === null || payload !== issuedFor) {
        key = newIdempotencyKey();
        issuedFor = payload;
      }
      return key;
    },
    rotate() {
      key = null;
      issuedFor = undefined;
    },
  };
}

/* ── 되돌릴 수 없는 엔드포인트 ────────────────────────────────────────── */

/**
 * 계약서 §01 의 5곳. 승인 게이트 2곳(㉠ 초안 제출 · ㉡ healthSafety)이 여기 포함된다.
 * 🚨 초안 제출은 create(`submitEvent`)와 update(`updateEvent`) **두 줄**이다 — 한 곳의 두 갈래라
 *    게이트가 늘어난 것이 아니다 (9/21 에 op 별로 엔드포인트를 갈랐다).
 * 여기에 줄을 더하면 아래 정규식과 operations.ts 가 자동으로 따라온다.
 * 🚨 줄을 더하면 `idempotentMethod` 에도 더한다 — 타입이 빠진 줄을 잡는다.
 */
export const idempotentPath = {
  /** 한 줄 입력 — 같은 한 줄이 관찰 N건씩 두 번 저장되는 것을 막는다. */
  input: (childId: string) => `/children/${childId}/inputs`,
  onboarding: (childId: string) => `/children/${childId}/onboarding`,
  photo: (childId: string) => `/children/${childId}/photos`,
  /** 🚨 승인 게이트 ㉡ — 알레르기·건강 기록 확정. */
  healthSafety: (childId: string) => `/children/${childId}/health-safety`,
  /**
   * 🚨 승인 게이트 ㉠ — 캘린더 쓰기. 초안 **제출**이 이 자리다 (#121).
   *
   * 9/21 에 op 별로 엔드포인트를 갈랐다 — create 는 여기(`POST`), update 는 아래 `updateEvent`(`PATCH`).
   * 경로는 서버 구현(#236 · #284)이 정본이다 (Swagger).
   */
  submitEvent: (childId: string) => `/children/${childId}/events`,
  /**
   * 🚨 승인 게이트 ㉠ — 수정 초안 반영. **이것도 캘린더 쓰기라 키가 필요하다** (#316).
   *
   * 🚨 한동안 "`items` 가 최종 목록이라 같은 본문을 두 번 보내도 결과가 같다(멱등)" 고 보고 표에서
   *    뺐었다. **`item_id: null` 인 새 준비물에는 그 말이 성립하지 않는다** — 재시도가 같은 null 행을
   *    다시 보내면 서버는 이미 들어간 것인지 알 수 없어서 준비물이 두 줄 들어간다.
   */
  updateEvent: (childId: string, eventId: string) => `/children/${childId}/events/${eventId}`,
} as const satisfies Record<string, (...ids: string[]) => string>;

export type IdempotentOperation = keyof typeof idempotentPath;

/**
 * 각 경로를 **어느 메서드로** 부를 때 키가 필요한가.
 *
 * 🚨 경로만으로 판정하지 않는다. `/children/{cid}/events/{eid}` 는 수정(`PATCH`)만 되돌릴 수
 *    없고 같은 경로의 읽기는 아무것도 바꾸지 않는다 — 경로로만 걸면 읽기까지 막는다.
 */
export const idempotentMethod = {
  input: "POST",
  onboarding: "POST",
  photo: "POST",
  healthSafety: "POST",
  submitEvent: "POST",
  updateEvent: "PATCH",
} as const satisfies Record<IdempotentOperation, "POST" | "PATCH">;

const ID_SLOT = "__ID__";

/**
 * 경로 빌더에서 정규식을 만든다 — 목록을 손으로 두 번 쓰지 않기 위해서다.
 * id 칸이 여럿인 빌더(`updateEvent`)는 칸마다 같은 자리표를 넣는다 (`build.length`).
 */
function pathPattern(build: (...ids: string[]) => string): RegExp {
  const slots = Array.from({ length: build.length }, () => ID_SLOT);
  const segments = build(...slots)
    .split(ID_SLOT)
    .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^${segments.join("[^/]+")}$`);
}

const REQUIRED: Array<{ method: string; pattern: RegExp }> = (
  Object.keys(idempotentPath) as IdempotentOperation[]
).map((operation) => ({
  method: idempotentMethod[operation],
  pattern: pathPattern(idempotentPath[operation]),
}));

/** `POST /children/c1/inputs` 처럼 이미 만들어진 요청이 키를 요구하는지. */
export function requiresIdempotencyKey(method: string, path: string): boolean {
  const upper = method.toUpperCase();
  return REQUIRED.some((entry) => entry.method === upper && entry.pattern.test(path));
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
