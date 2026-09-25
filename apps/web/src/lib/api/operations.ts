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
import type {
  HealthSafety,
  OnboardingRequest,
  OnboardingResponse,
  PhotoCommitRequest,
  PhotoCommitResponse,
  PhotoLane,
  SubmitEventBody,
  SubmitEventResponse,
} from "./types";

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

/**
 * 🚨 **요청·응답 타입은 `types.ts` 가 정본이다. 여기서 다시 정의하지 않는다.**
 *    한동안 두 벌이었고(`OnboardingRequest` · `OnboardingResponse`), 배럴이 `export *` 를
 *    두 번 해서 이름이 겹쳤다 (TS2308). 그때 **나중에 온 이 파일 쪽이 조용히 이겼고**,
 *    화면은 계약서를 적어 둔 정의가 아니라 이쪽 정의로 타입이 잡혀 있었다.
 *    이 파일이 하는 일은 "키를 필수 인자로 받는 호출 지점" 하나다 — 타입의 집이 아니다.
 *
 * ⚠️ 두 벌이 완전히 같지는 않았다. 이쪽 `safety` 원소에는 `notes` 가 있고 `severity` 가
 *    `string | null` 이었다. 정본(`OnboardingSafetyInput`)에는 `notes` 가 없다.
 *    **서버가 `safety[].notes` 를 받는지가 확인되면 `types.ts` 를 고친다** — 화면은
 *    보호자가 적은 라벨만 올리고 심각도·반응·메모를 추측하지 않으므로(NF-03) 지금은 정본이 맞다.
 */

/** 전부 선택이다 — 모두 건너뛰어도 200 이다. */
export function submitOnboarding(
  childId: string,
  body: OnboardingRequest,
  idempotencyKey: IdempotencyKey,
): Promise<OnboardingResponse> {
  return api.post(idempotentPath.onboarding(childId), body, { idempotencyKey });
}

/* ── 08 사진으로 적기 ────────────────────────────────────────────────── */

/**
 * multipart/form-data → 202 {run_id}. SSE 채널을 재사용한다.
 *
 * ⚠️ **계약서가 multipart 필드 이름을 정해 두지 않았다.** 화면은 `file` · `lane`(부모가 고른 값) ·
 *    캘린더의 특정 날짜에서 들어온 경우 `date`(YYYY-MM-DD) 를 보낸다 —
 *    `photoFormData()` 가 그 이름들을 한 곳에서 만든다.
 *    👉 `apps/api` Owner 협의 대상 (최상위 CLAUDE.md §8).
 */
export function uploadPhoto(
  childId: string,
  form: FormData,
  idempotencyKey: IdempotencyKey,
): Promise<{ run_id: string }> {
  return api.post(idempotentPath.photo(childId), form, { idempotencyKey });
}

/**
 * 위 ⚠️ 의 필드 이름을 만드는 **유일한 자리**. 화면이 FormData 를 직접 조립하지 않는다.
 *
 * 🚨 `lane` 은 **부모가 시트에서 고른 값**이다. 계약서 §09 는 서버가 추측해 `lane` 이벤트로
 *    내려주게 되어 있는데, 부모가 먼저 선언하면 그 추측은 "다르게 읽혔다" 를 알리는 용도가 된다 —
 *    무엇을 찍었는지는 찍은 사람이 안다. 문서와 활동은 저장 경로가 통째로 다르므로
 *    (`institution_notice` vs 보호자 확인) 이 값을 추측에 맡기지 않는다.
 *    ⚠️ 이 필드도 계약서에 없다. 👉 `apps/api` Owner 협의 대상.
 */
export function photoFormData(
  file: File,
  { lane, date }: { lane: PhotoLane; date?: string },
): FormData {
  const form = new FormData();
  form.append("file", file);
  form.append("lane", lane);
  // 🚨 날짜를 프론트가 계산하지 않는다. 캘린더에서 고른 날이 있을 때만 그 값을 그대로 싣고,
  //    없으면 아예 보내지 않는다 — "오늘" 로 채우는 것은 서버가 할 일이다 (CLAUDE.md §3).
  if (date) form.append("date", date);
  return form;
}

/*
 * ⚠️ `POST /photo-runs/{rid}/reanalyze`(힌트 한 줄로 다시 읽기) 를 부르는 함수는 **없다.**
 *    화면에서 뺐기 때문이다 — 읽어낸 항목을 부모가 **그 자리에서 고칠 수 있게** 되면서
 *    "빠진 것을 알려주고 모델에게 다시 맡기기" 가 더 먼 길이 됐다 (고치면 바로 끝난다).
 *    엔드포인트는 계약서에 남아 있고, 필요해지면 여기 다시 만든다.
 */

/**
 * 🚨 **사진 흐름에서 저장이 일어나는 유일한 지점.** 여기 오기 전까지는 아무것도 저장되지 않는다.
 *
 * 🚨 승인 게이트가 아니다 — 문서 lane 이 만드는 `event` 는 `draft` 다 (`types.ts` 의 주석).
 * ⚠️ 계약서 §01 의 Idempotency 목록 밖이라 키를 받지 않는다. 두 번 보내면 두 번 쌓일 수 있고,
 *    화면이 보내는 동안 버튼을 잠그는 것으로만 막고 있다. 👉 협의 대상.
 */
export function commitPhotoRun(
  runId: string,
  body: PhotoCommitRequest,
): Promise<PhotoCommitResponse> {
  return api.post(`/photo-runs/${runId}/commit`, body);
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
 * 초안을 캘린더에 **넣는다.** 되돌릴 수 없는 지점이다 —
 * 여기 오기 전까지 초안은 SSE 와 응답에만 있고 DB 에 행이 없다 (#118 · #121).
 *
 * 🚨 본문은 **보호자가 확인한 최종 상태 전체**다 (#122). 부분 갱신이 아니라서
 *    `items` 에서 빠진 `item_id` 가 삭제로 처리된다.
 * 🚨 낙관적 업데이트 금지 (apps/web/CLAUDE.md §3). 응답을 받은 뒤에 캐시를 갱신한다.
 * 🚨 재시도는 **같은 키**로 간다. 새 키를 만들면 일정이 두 건 생긴다.
 */
export function submitEventDraft(
  childId: string,
  body: SubmitEventBody,
  idempotencyKey: IdempotencyKey,
): Promise<SubmitEventResponse> {
  return api.post(idempotentPath.submitEvent(childId), body, { idempotencyKey });
}

/**
 * 수정 초안을 반영한다. 🚨 **이것도 캘린더 쓰기라 게이트 ㉠ 이고, 키를 받는다.**
 *
 * 🚨 한동안 키 없이 뒀다 — `items` 가 최종 목록이라 같은 본문을 두 번 보내도 결과가 같다고 봤다.
 *    **`item_id: null` 인 새 준비물에는 그 말이 성립하지 않는다:** 재시도가 같은 null 행을 다시
 *    보내는데 서버는 그게 이미 들어간 것인지 알 방법이 없다. 서버가 저장하고 응답만 유실되면
 *    카드가 실패로 보이고, 보호자가 한 번 더 누르면 **"모자" 가 두 줄** 들어간다.
 *
 * ⚠️ 경로가 미정이다 (`docs/event/event-draft-flow-v1.md` §6 — op 별로 가른다는 것까지만 정했다).
 *    🚨 그래서 `idempotentPath` 표에는 **아직 넣지 않았다** — 그 표는 POST 차단과 계약 테스트
 *    ①(키 없으면 400)을 함께 돌리는데, `POST /events/{eid}` 는 존재하지 않는 경로다.
 *    경로가 확정되면 표로 옮긴다.
 */
export function updateEventDraft(
  eventId: string,
  body: SubmitEventBody,
  idempotencyKey: IdempotencyKey,
): Promise<SubmitEventResponse> {
  return api.patch(`/events/${eventId}`, body, { idempotencyKey });
}
