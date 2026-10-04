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
  // ── 초대. 🔶 이름은 **아직 제안**이다 (`docs/api/invite-v1.md` §5 · §7 열린 결정 03) —
  //    백엔드가 다른 이름으로 정하면 여기와 화면 문구를 그 이름으로 맞춘다.
  "child_already_exists", // 409 이미 아이가 있는 보호자가 수락 — 아이는 보호자당 한 명
  "invite_expired", // 410 코드 기한 만료
  "invite_not_found", // 404 없는 코드
  "too_many_attempts", // 429 🚨 코드 방식의 전제다 — 없으면 8자(40비트)가 뚫린다
  // 🔶 429 `POST /children/{cid}/inputs` 하루 한도 (#147 · 계약서에 아직 없다). 한국 시간 자정
  //    기준이고 횟수는 서버 설정값이라 바뀐다 — **화면이 숫자를 적지 않는다.**
  //    🚨 다시 시도할 수 없는 429 다: 같은 본문은 같은 Idempotency-Key 로 나가서 자정까지 같은
  //       응답이다. `too_many_attempts` 와 달리 기다림이 초 단위가 아니라 **날짜 단위**다.
  "daily_input_limit",
  // 🔶 400 `POST /children/{cid}/inputs` 의 `reply_to` 가 가리키는 질문을 못 찾음 (#175 · 계약서에 없다).
  //    없는 run · 남의 run · 다른 아이 · 이미 답한 질문 · 만료(15분)·서버 재시작을 **한 코드로** 합친다.
  //    🚨 다시 시도할 수 없는 400 이다: 맥락이 서버에서 사라졌으니 같은 `reply_to` 는 계속 400 이다.
  //       화면은 질문을 놓고, 보호자가 무엇에 대한 답인지까지 적어 **새 입력**으로 보내게 한다.
  "reply_context_unavailable",
  // 400 가입 · 아이 등록 · 동의 변경이 **지금 유효하지 않은 약관 버전**을 보냈다 (#91 · #172).
  // 🚨 고장이 아니라 화면이 낡은 것이다 — `GET /policies` 를 다시 받아 **바뀐 항목만** 다시
  //    확인받는다. 대기표(`consent_code`)는 살아 있어서 로그인부터 다시 하지 않는다
  //    (`docs/api/auth-kakao-v1.md` §3-5).
  "policy_version_invalid",
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
