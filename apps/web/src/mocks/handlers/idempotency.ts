import {
  HttpResponse,
  type DefaultBodyType,
  type HttpResponseResolver,
  type PathParams,
} from "msw";

import { apiError } from "./helpers";

/**
 * 목 쪽 Idempotency-Key 구현.
 *
 * 타입 검사는 **응답의 모양**만 본다. 키를 빠뜨렸을 때 · 같은 키로 다시 보냈을 때 ·
 * 같은 키가 동시에 들어왔을 때 서버가 어떻게 행동해야 하는지는 타입이 알려주지 않는다.
 * 그래서 목이 그 행동까지 흉내 낸다 — 화면이 "재시도해도 두 번 실행되지 않는다"를
 * 전제로 짜여도 되는지 여기서 확인된다.
 *
 * 정본은 docs/api/idempotency-v1.md. 규칙 네 줄:
 *
 *   ① 키 없음               → 400 idempotency_key_required (계약서 §01 "헤더가 없으면 400")
 *   ② 같은 키 · 같은 요청   → 처음 응답을 그대로 재생 (핸들러를 다시 실행하지 않는다)
 *   ③ 같은 키 · 다른 요청   → 422 idempotency_key_reuse (성공을 이미 저장한 뒤부터)
 *   ④ 같은 키 · 처리 중     → 409 idempotency_in_progress
 *
 * 🚨 ②·③ 이 없으면 "성공했는데 응답을 못 받아 다시 누른" 경우와
 *    "새 요청으로 이미 끝난 걸 또 하려는" 경우를 화면이 구분할 수 없다.
 */

interface StoredResponse {
  /** method + 경로 + 요청 본문. 같은 키인데 이게 다르면 다른 요청이다. */
  fingerprint: string;
  status: number;
  headers: [string, string][];
  body: string;
}

const completed = new Map<string, StoredResponse>();
const inFlight = new Set<string>();

/** 테스트가 요청 사이에 상태를 비운다. 목 서버는 프로세스 수명만큼 산다. */
export function resetIdempotencyStore(): void {
  completed.clear();
  inFlight.clear();
}

/**
 * 요청 지문. multipart 는 본문을 그대로 쓸 수 없다 —
 * boundary 가 요청마다 새로 생성돼서 같은 파일을 다시 올려도 문자열이 달라진다.
 * 파트 이름·파일명·크기로 지문을 만든다.
 */
async function fingerprintOf(request: Request): Promise<string> {
  const scope = `${request.method} ${new URL(request.url).pathname}`;
  const contentType = request.headers.get("Content-Type") ?? "";

  if (contentType.startsWith("multipart/form-data")) {
    const form = await request.formData();
    const parts = [...form.entries()]
      .map(([name, value]) =>
        typeof value === "string" ? `${name}=${value}` : `${name}=${value.name}:${value.size}`,
      )
      .sort();
    return `${scope}|${parts.join("&")}`;
  }

  return `${scope}|${await request.text()}`;
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
 * 핸들러의 성공 응답 타입으로 좁히면 400·409·422 가 그 타입과 맞지 않는다.
 * 목의 응답 모양은 핸들러 안에서 계약서 타입으로 이미 강제된다 (src/mocks/fixtures.ts).
 */
export function withIdempotency<Params extends PathParams, RequestBody extends DefaultBodyType>(
  resolver: HttpResponseResolver<Params, RequestBody, DefaultBodyType>,
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

    // 본문을 두 번 읽을 수 없다. 지문은 복제본에서 뜨고 원본은 핸들러에 그대로 넘긴다.
    const fingerprint = await fingerprintOf(info.request.clone());

    const stored = completed.get(key);
    if (stored) {
      if (stored.fingerprint !== fingerprint) {
        return apiError(
          422,
          "idempotency_key_reuse",
          "같은 Idempotency-Key 로 다른 요청을 보낼 수 없어요",
        );
      }
      // 처음 응답을 그대로. 핸들러는 실행되지 않는다 — 그게 "중복 실행 방지" 다.
      return new HttpResponse(stored.body, {
        status: stored.status,
        headers: stored.headers,
      });
    }

    if (inFlight.has(key)) {
      return apiError(
        409,
        "idempotency_in_progress",
        "같은 요청이 아직 처리 중이에요. 잠시 뒤 다시 시도해 주세요",
      );
    }

    inFlight.add(key);
    try {
      const result = await resolver(info);
      if (!(result instanceof Response)) return result;

      // 본문은 복제본에서 읽는다. 원본은 읽지 않은 채로 그대로 내보내야 한다.
      const body = await result.clone().text();
      if (isReplayable(result.status)) {
        completed.set(key, {
          fingerprint,
          status: result.status,
          headers: [...result.headers.entries()],
          body,
        });
      }
      return result;
    } finally {
      inFlight.delete(key);
    }
  };
}
