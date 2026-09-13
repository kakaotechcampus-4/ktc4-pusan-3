/**
 * 되돌릴 수 없는 5개 엔드포인트 — **키가 필수 인자인 함수**로만 부른다.
 *
 * 왜 `api.post(...)` 를 그대로 쓰지 않나 —
 * `api.post` 의 `idempotencyKey` 는 선택 인자다. 빠뜨려도 컴파일이 통과하고,
 * 실패는 실행 시점(혹은 서버의 400)에야 드러난다. 아래 함수들은 키를 **세 번째 인자**로
 * 받기 때문에 빠뜨리면 `tsc` 가 먼저 잡는다. 경로도 idempotentPath 표에서만 만든다.
 *
 * 🚨 키는 "한 사용자 동작에 하나" 다. mutationFn 안에서 newIdempotencyKey() 를 부르면
 *    재시도마다 새 키가 나가 중복 방지가 통째로 무의미해진다 — useIdempotencyKey() 를 쓸 것.
 *
 * 응답 재생 · 키 재사용 거부 · 동시 요청 차단은 서버 몫이다 (docs/api/idempotency-v1.md).
 */

import { api } from "./client";
import { idempotentPath, type IdempotencyKey } from "./idempotency";
import type { Affinity, CalendarEvent, HealthSafety, Observation, SuggestionStatus } from "./types";

/* ── 04 한 줄 입력 ────────────────────────────────────────────────────── */

export interface InputRequest {
  text: string;
  source: "home_input" | "chat" | "photo";
}

/** 202 로 run_id 만 온다. 결과는 전부 SSE 로 흐른다 (streamRunEvents). */
export function submitInput(
  childId: string,
  body: InputRequest,
  idempotencyKey: IdempotencyKey,
): Promise<{ run_id: string }> {
  return api.post(idempotentPath.input(childId), body, { idempotencyKey });
}

/* ── 02 온보딩 ────────────────────────────────────────────────────────── */

export interface OnboardingRequest {
  interests?: string[];
  /** 🚨 "잘 모르겠어요"(unknown)는 "없음"(none)이 아니다. 서버가 skipped 로 되돌린다. */
  safety_status?: "none" | "has" | "unknown";
  safety?: Array<Omit<HealthSafetyRequest, "notes"> & { notes?: string | null }>;
  one_line?: string;
  dev_answers?: Array<{ item_id: string; level: number }>;
}

export interface OnboardingResponse {
  observations: Observation[];
  affinities: Affinity[];
  safety: HealthSafety[];
  skipped: string[];
  run_id: string;
}

/** 전부 선택이다 — 모두 건너뛰어도 200 이다. */
export function submitOnboarding(
  childId: string,
  body: OnboardingRequest,
  idempotencyKey: IdempotencyKey,
): Promise<OnboardingResponse> {
  return api.post(idempotentPath.onboarding(childId), body, { idempotencyKey });
}

/* ── 09 사진 ──────────────────────────────────────────────────────────── */

/** multipart/form-data → 202 {run_id}. SSE 채널을 재사용한다. */
export function uploadPhoto(
  childId: string,
  form: FormData,
  idempotencyKey: IdempotencyKey,
): Promise<{ run_id: string }> {
  return api.post(idempotentPath.photo(childId), form, { idempotencyKey });
}

/* ── 🚨 승인 게이트 ㉡ — 알레르기·건강 기록 확정 ──────────────────────── */

export interface HealthSafetyRequest {
  type: string;
  label: string;
  category: string;
  severity?: string | null;
  reactions?: string[];
  notes?: string | null;
}

/**
 * 🚨 알레르기·건강 정보의 **유일한 쓰기 경로**다 (NF-03).
 * 보호자가 직접 확정한 값만 들어온다 — Agent 실행 경로에서 이 함수를 부르지 않는다.
 */
export function addHealthSafety(
  childId: string,
  body: HealthSafetyRequest,
  idempotencyKey: IdempotencyKey,
): Promise<{ safety: HealthSafety }> {
  return api.post(idempotentPath.healthSafety(childId), body, { idempotencyKey });
}

/* ── 🚨 승인 게이트 ㉠ — 캘린더 쓰기 ──────────────────────────────────── */

/**
 * 되돌릴 수 없는 지점. event.status → confirmed, 연결된 suggestion.status → approved.
 *
 * 🚨 낙관적 업데이트 금지 (apps/web/CLAUDE.md §3). 응답을 받은 뒤에 캐시를 갱신한다.
 * 409 already_confirmed 는 "다른 요청이 이미 확정한 일정" 이라는 뜻이다 —
 * 같은 키로 다시 보낸 재시도는 409 가 아니라 처음 응답을 그대로 받는다.
 */
export function confirmEvent(
  eventId: string,
  idempotencyKey: IdempotencyKey,
): Promise<{ event: CalendarEvent; suggestion_status: SuggestionStatus }> {
  return api.post(idempotentPath.confirmEvent(eventId), undefined, { idempotencyKey });
}
