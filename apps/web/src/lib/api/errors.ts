/**
 * 서버 에러 봉투. docs/api/api-interface-v1.html §01 공통 규약.
 *
 * { "error": { "code": "...", "message": "...", "detail": { ... } } }
 */

/** 계약서에 명시된 코드. 그 외 코드가 와도 죽지 않게 string 을 함께 허용한다. */
export const API_ERROR_CODES = [
  // ── Idempotency-Key (계약서 §01 은 "헤더가 없으면 400" 까지만 정한다).
  //    아래 3개는 docs/api/idempotency-v1.md 가 제안하는 신규 코드다 — 계약서 v1.1 대상.
  "idempotency_key_required", // 400 되돌릴 수 없는 엔드포인트인데 헤더가 없다
  "idempotency_key_reuse", // 422 같은 키인데 요청 내용이 다르다 — 재사용 거부
  "idempotency_in_progress", // 409 같은 키가 아직 처리 중이다 — 중복 실행 차단
  "unauthenticated", // 401 토큰 없음·만료
  "child_access_denied", // 403 parent_child 연결 없음
  "consent_required", // 403 필수 동의 미동의·철회 — 저장 시도 전에 막힌다
  "not_found", // 404
  "owner_required", // 409
  "already_sent", // 409 reminder 발송 완료
  "already_confirmed", // 409 승인 게이트 중복
  "already_exists", // 409 health_safety UNIQUE(child_id, type, label) 재등록 — 계약서에 코드가 없어 제안
  "invite_used", // 409
  "validation_failed", // 422
  "llm_unavailable", // 503 — 🚨 기본값으로 대체하지 않는다
] as const;

export type ApiErrorCode = (typeof API_ERROR_CODES)[number] | (string & {});

export interface ApiErrorBody {
  error: {
    code: ApiErrorCode;
    message: string;
    detail?: Record<string, unknown>;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly detail?: Record<string, unknown>;

  constructor(
    status: number,
    code: ApiErrorCode,
    message: string,
    detail?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** 동의 화면으로 보낼 딥링크. consent_required 일 때만 채워진다. */
  get consentDeeplink(): string | null {
    const link = this.detail?.deeplink;
    return typeof link === "string" ? link : null;
  }
}

/** 네트워크 자체가 실패했을 때 (서버 봉투가 없음). */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("서버에 연결하지 못했어요");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

export function isApiError(e: unknown, code?: ApiErrorCode): e is ApiError {
  return e instanceof ApiError && (code === undefined || e.code === code);
}
