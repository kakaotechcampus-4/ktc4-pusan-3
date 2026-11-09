import {
  HttpResponse,
  type DefaultBodyType,
  type HttpResponseResolver,
  type PathParams,
} from "msw";

import { apiError } from "./helpers";

/**
 * 목 쪽 Idempotency-Key 구현 — 서버(`apps/api/app/api/idempotency.py`)와 같이 행동한다.
 *
 * 타입 검사는 **응답의 모양**만 본다. 키를 빠뜨렸을 때 · 같은 키로 다시 보냈을 때 ·
 * 같은 키가 동시에 들어왔을 때 서버가 어떻게 행동하는지는 타입이 알려주지 않는다.
 * 그래서 목이 그 행동까지 흉내 낸다 — 화면이 "재시도해도 두 번 실행되지 않는다"를
 * 전제로 짜여도 되는지 여기서 확인된다.
 *
 * 정본은 docs/api/idempotency-v1.md. 규칙 세 줄:
 *
 *   ① 키 없음                      → 400 idempotency_key_required (계약서 §01 "헤더가 없으면 400")
 *   ② 같은 키(같은 방법 · 경로)    → 처음 성공 응답을 그대로 재생 (핸들러를 다시 실행하지 않는다)
 *   ③ 같은 키가 아직 처리 중       → 끝날 때까지 기다렸다가 그 응답을 재생 (핸들러는 한 번만)
 *
 * 🚨 **본문은 비교하지 않는다** — 서버도 하지 않는다. 422 `idempotency_key_reuse` · 409
 *    `idempotency_in_progress` 는 만들지 않기로 했다 (#438 · 11-09 PM). 화면이 본문이 바뀌면 새 키를
 *    쓰므로(`current(text)`) 고친 본문이 옛 응답에 묻히는 일은 화면 쪽에서 막는다. 목이 서버에 없는
 *    거절을 흉내 내면 화면 테스트가 실제로 오지 않는 응답을 기준으로 짜인다.
 */

interface StoredResponse {
  status: number;
  headers: [string, string][];
  body: string;
}

/** 키 범위는 서버와 같이 (방법, 경로, 키) — 보호자는 목에 한 명뿐이다. */
const completed = new Map<string, StoredResponse>();
const inFlight = new Map<string, Promise<void>>();

/** 테스트가 요청 사이에 상태를 비운다. 목 서버는 프로세스 수명만큼 산다. */
export function resetIdempotencyStore(): void {
  completed.clear();
  inFlight.clear();
}

/**
 * `failed` 로 끝난 run 의 키를 놓아준다 — 같은 키로 "다시 시도" 하면 새 run 이 떠야 한다.
 *
 * 🚨 서버가 이렇게 한다 (`apps/api/app/api/runs/runner.py` 의 `forget_run`). 목이 안 놓으면
 *    재시도가 **실패한 run 을 재생**해서 핸들러가 다시 돌지 않고, 재시도 요청에 무엇이 실렸는지
 *    (특히 `reply_to`) 를 목이 볼 수 없다.
 */
export function forgetRun(runId: string): void {
  for (const [key, stored] of completed) {
    if (stored.body.includes(`"run_id":"${runId}"`)) completed.delete(key);
  }
}

function scopeOf(request: Request, key: string): string {
  return `${request.method} ${new URL(request.url).pathname}|${key}`;
}

/**
 * 성공(2xx)만 저장한다.
 *
 * 4xx·5xx 는 **저장이 일어나지 않은 상태** 다. 특히 403 consent_required 를 캐시하면
 * "동의하고 다시 누른" 흐름이 영원히 같은 403 을 받는다 — 실제로 이 앱에 있는 흐름이다.
 * 그래서 실패 응답은 재생하지 않고, 같은 키로 다시 시도할 수 있게 둔다.
 */
function isReplayable(status: number): boolean {
  return status < 300;
}

/**
 * ResponseBody 를 제네릭으로 열지 않은 이유 — 이 래퍼는 에러 봉투(`apiError`)도 내려보낸다.
 * 핸들러의 성공 응답 타입으로 좁히면 400 이 그 타입과 맞지 않는다.
 * 목의 응답 모양은 핸들러 안에서 계약서 타입으로 이미 강제된다 (src/mocks/fixtures.ts).
 */
export function withIdempotency<Params extends PathParams, RequestBody extends DefaultBodyType>(
  resolver: HttpResponseResolver<Params, RequestBody, DefaultBodyType>,
  {
    replay = (body) => body,
  }: {
    /**
     * 재생할 본문을 바꾼다. 🚨 **한 줄 입력만 쓴다** — 서버가 재생할 run 의 채널 상태에 따라 응답을 나눈다
     * (`finished` · #538). 다른 경로는 처음 응답을 그대로 재생한다.
     */
    replay?: (body: string) => string;
  } = {},
): HttpResponseResolver<Params, RequestBody, DefaultBodyType> {
  return async (info) => {
    const key = info.request.headers.get("Idempotency-Key");

    if (!key) {
      return apiError(
        400,
        "idempotency_key_required",
        "Idempotency-Key 헤더가 필요해요 (되돌릴 수 없는 요청)",
      );
    }

    const scope = scopeOf(info.request, key);
    // 🚨 처리 중인 같은 키는 끝날 때까지 기다린다. 여기까지 await 가 없어야 두 요청이 동시에 빈 자리를
    //    보고 둘 다 핸들러를 돌리지 않는다 — 첫 요청이 아래에서 자리를 잡은 뒤에야 두 번째가 돈다.
    const running = inFlight.get(scope);
    if (running) await running;

    const stored = completed.get(scope);
    if (stored) {
      // 처음 응답을 그대로. 핸들러는 실행되지 않는다 — 그게 "중복 실행 방지" 다.
      return new HttpResponse(replay(stored.body), {
        status: stored.status,
        headers: stored.headers,
      });
    }

    let finish!: () => void;
    inFlight.set(scope, new Promise<void>((resolve) => (finish = resolve)));
    try {
      const result = await resolver(info);
      if (!(result instanceof Response)) return result;

      // 본문은 복제본에서 읽는다. 원본은 읽지 않은 채로 그대로 내보내야 한다.
      const body = await result.clone().text();
      if (isReplayable(result.status)) {
        completed.set(scope, {
          status: result.status,
          headers: [...result.headers.entries()],
          body,
        });
      }
      return result;
    } finally {
      inFlight.delete(scope);
      finish();
    }
  };
}
