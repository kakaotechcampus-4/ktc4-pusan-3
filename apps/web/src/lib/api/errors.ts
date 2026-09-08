/**
 * 서버 에러 봉투. docs/api/api-interface-v1.html §01 공통 규약.
 *
 * { "error": { "code": "...", "message": "...", "detail": { ... } } }
 */

/** 계약서에 명시된 코드. 그 외 코드가 와도 죽지 않게 string 을 함께 허용한다. */
export const API_ERROR_CODES = [
  "unauthenticated", // 401 토큰 없음·만료
  "child_access_denied", // 403 parent_child 연결 없음
  "consent_required", // 403 필수 동의 미동의·철회 — 저장 시도 전에 막힌다
  "not_found", // 404
  "owner_required", // 409
  "already_sent", // 409 reminder 발송 완료
  "already_confirmed", // 409 승인 게이트 중복
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
