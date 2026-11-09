/**
 * 되돌릴 수 없는 엔드포인트 — **키가 필수 인자인 함수**로만 부른다.
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
import { NetworkError } from "./errors";
import { idempotentPath, type IdempotencyKey } from "./idempotency";
import type {
  ApproveSuggestionsRequest,
  ApproveSuggestionsResponse,
  CreateEventDraftsRequest,
  CreateEventDraftsResponse,
  ConfirmDeleteProposalRequest,
  CreateInputResponse,
  DeleteProposalResponse,
  HealthSafety,
  ReviseMedicationBody,
  SubmitMedicationBody,
  SubmitMedicationResponse,
  OnboardingRequest,
  OnboardingResponse,
  PhotoCommitRequest,
  PhotoCommitResponse,
  PhotoLane,
  PhotoUploadTicket,
  SubmitEventBody,
  InputLocation,
  ObservationKind,
  SubmitEventResponse,
  SubmitRepeatEventBody,
  UpdateSeriesBody,
  UpdateSeriesResponse,
  UpdateHealthSafetyRequest,
  UpdateHealthSafetyResponse,
} from "./types";

/* ── 04 한 줄 입력 ────────────────────────────────────────────────────── */

export interface InputRequest {
  text: string;
  source: "home_input" | "chat" | "photo";
  /**
   * 🔶 **되묻기에 대한 답일 때만** 싣는다 — 그 질문이 나온 `run_id` 다 (#158 리뷰 · 서버 #175 머지 전).
   *
   * 🚨 **원문을 다시 보내지 않기 위한 필드다.** 답만 보내면 서버가 맥락을 모르는데, 그렇다고
   *    화면이 원문을 붙여 보내면 **이미 저장된 조각까지 다시 저장된다** — "계란 잘 먹었어.
   *    요즘 기침해" 처럼 일부는 저장되고 질문이 같이 오는 run 이 있기 때문이다
   *    (`apps/api/app/agents/pipeline.py` — `Saved` 와 `MemoryNote` 가 한 run 에 같이 나간다).
   *    맥락은 서버가 이 `run_id` 로 찾는다.
   *
   * 🚨 **서버가 그 질문을 못 찾으면 `400 reply_context_unavailable` 이다** (#175) — 만료(15분) ·
   *    재시작 · 이미 답한 질문. 같은 `reply_to` 는 계속 400 이라 화면은 질문을 놓는다 (03 홈 `onError`).
   * ⚠️ #175 머지 전 서버는 **모르는 필드라 무시한다** — 먼저 보내도 깨지지 않는다.
   */
  reply_to?: string;
  /**
   * 흐린 위치 (#593 · `CreateInputRequest.location`). 🚨 **보내기 직전에 붙인다** — 말풍선의 본문(`ChatTurn.body`)
   * 에는 없다. 다시 시도가 같은 좌표로 나가도록 키마다 붙든다 (`stores/conversation.ts` 의 `sentLocations`).
   */
  location?: InputLocation;
}

/**
 * 202 로 run_id 가 온다. 결과는 SSE 로 흐른다 (streamRunEvents).
 * 🚨 `finished` 가 참이면 SSE 를 열지 않는다 — 채널이 이미 지워진 끝난 run 이다 (`CreateInputResponse`).
 */
export function submitInput(
  childId: string,
  body: InputRequest,
  idempotencyKey: IdempotencyKey,
): Promise<CreateInputResponse> {
  return api.post(idempotentPath.input(childId), body, { idempotencyKey });
}

/* ── 02 온보딩 ────────────────────────────────────────────────────────── */

/**
 * 🚨 **요청·응답 타입은 `types.ts` 가 정본이다. 여기서 다시 정의하지 않는다.**
 *    한동안 두 벌이었고(`OnboardingRequest` · `OnboardingResponse`), 배럴이 `export *` 를
 *    두 번 해서 이름이 겹쳤다 (TS2308). 그때 **나중에 온 이 파일 쪽이 조용히 이겼고**,
 *    화면은 계약서를 적어 둔 정의가 아니라 이쪽 정의로 타입이 잡혀 있었다.
 *    이 파일이 하는 일은 "키를 필수 인자로 받는 호출 지점" 하나다 — 타입의 집이 아니다.
 */

/** 전부 선택이지만 빈 본문은 400 이다 — 아무것도 안 적었으면 부르지 않는다. */
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
  photo: File | { uploadKey: string },
  { lane, date }: { lane: PhotoLane; date?: string },
): FormData {
  const form = new FormData();
  // S3 에 이미 올렸으면 사진 대신 열쇠만 보낸다 (`sendPhoto`). 모양으로 가른다 —
  // `instanceof File` 은 다른 실행 환경(iframe · 웹뷰 다리)에서 만든 File 을 못 알아본다
  if ("uploadKey" in photo) form.append("upload_key", photo.uploadKey);
  else form.append("file", photo);
  form.append("lane", lane);
  // 🚨 날짜를 프론트가 계산하지 않는다. 캘린더에서 고른 날이 있을 때만 그 값을 그대로 싣고,
  //    없으면 아예 보내지 않는다 — "오늘" 로 채우는 것은 서버가 할 일이다 (CLAUDE.md §3).
  if (date) form.append("date", date);
  return form;
}

/** 사진을 어디로 올릴지 받는다. 아무것도 저장하지 않고 하루 한도도 쓰지 않는다. */
export function createPhotoUpload(childId: string): Promise<PhotoUploadTicket> {
  return api.post(`/children/${childId}/photos/uploads`);
}

/**
 * S3 업로드 주소에 사진을 올린다.
 *
 * 🚨 **`api` 클라이언트를 쓰지 않는다.** 우리 서버가 아니라서 인증 헤더 · 쿠키가 S3 로 새면 안 된다.
 *    S3 는 주소에 든 서명으로 확인한다.
 * 🚨 `file` 은 **맨 마지막**에 붙인다. S3 는 `file` 뒤의 폼 값을 무시한다.
 * 🚨 시간 제한을 건다. 20초 안전망은 run 스트림만 보고 있어서, 여기서 멈추면 읽는 중에 갇힌다.
 */
export async function putPhotoToS3(
  ticket: Pick<PhotoUploadTicket, "url" | "fields">,
  file: File,
): Promise<void> {
  if (!ticket.url) throw new Error("사진을 올릴 곳을 받지 못했어요.");
  const form = new FormData();
  for (const [name, value] of Object.entries(ticket.fields ?? {})) form.append(name, value);
  form.append("file", file);
  const response = await fetch(ticket.url, {
    method: "POST",
    body: form,
    credentials: "omit",
    signal: AbortSignal.timeout(S3_UPLOAD_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error("사진을 보내지 못했어요.");
}

/** 15MB 사진을 느린 휴대폰 망으로 올려도 넉넉한 시간. */
const S3_UPLOAD_TIMEOUT_MS = 60_000;

/**
 * 같은 사진의 재시도가 처음 올린 열쇠를 다시 쓰게 기억한다 (키 → S3 열쇠).
 *
 * 재시도마다 새로 올리면 ① 앞 요청이 서버에서 이미 성공했을 때 새 사본이 버킷에 남고
 * ② 같은 Idempotency-Key 에 본문(upload_key)이 달라진다. 서버는 이미 지운 열쇠가 다시 와도
 * 키 재생으로 앞 결과를 돌려준다.
 * 🚨 기억하는 것은 **응답을 못 받은 실패**(`NetworkError`)까지다. 응답이 왔으면 오류여도 버린다 —
 *    그 열쇠는 서버가 지웠을 수 있고(아래 `sendPhoto`), 그 뒤 재시도는 새로 올린 다른 열쇠를 같은
 *    Idempotency-Key 로 보낸다. 서버와 목은 **성공한 요청만** 기억해서 이것이 422 가 되지 않는다
 *    (`api/idempotency.py` · `mocks/handlers/idempotency.ts`).
 */
const uploadedFor = new Map<IdempotencyKey, string>();

/**
 * 사진을 서버가 읽을 수 있게 보낸다 — 사진 화면이 부르는 **한 자리**.
 *
 * 서버에 S3 버킷이 있으면 사진은 S3 로 가고 서버에는 열쇠만 간다. 서버는 내려받자마자 S3 에서
 * 지운다(원본을 남기지 않는다). 버킷이 없는 로컬 개발이면 지금처럼 사진을 서버에 보낸다.
 * 🚨 재시도는 같은 `idempotencyKey` 로 온다. 이미 S3 에 올렸으면 그 열쇠를 다시 보낸다.
 * 🚨 S3 는 문서 사진만 쓴다. 서버가 아직 받지 않는 활동 사진(아이가 찍힌 사진)을 버킷에 올리지 않는다.
 */
export async function sendPhoto(
  childId: string,
  file: File,
  options: { lane: PhotoLane; date?: string },
  idempotencyKey: IdempotencyKey,
): Promise<{ run_id: string }> {
  let uploadKey = uploadedFor.get(idempotencyKey);
  if (!uploadKey) {
    const ticket = options.lane === "document" ? await createPhotoUpload(childId) : null;
    if (!ticket || ticket.mode === "direct") {
      return uploadPhoto(childId, photoFormData(file, options), idempotencyKey);
    }
    if (!ticket.key) throw new Error("사진을 올릴 곳을 받지 못했어요.");
    await putPhotoToS3(ticket, file);
    uploadKey = ticket.key;
    uploadedFor.set(idempotencyKey, uploadKey);
  }
  try {
    const res = await uploadPhoto(childId, photoFormData({ uploadKey }, options), idempotencyKey);
    uploadedFor.delete(idempotencyKey);
    return res;
  } catch (error) {
    // 🚨 응답이 왔으면(오류여도) 열쇠를 버린다. 서버는 핸들러 안에서 사진을 읽지 않고 돌려보낼 때
    //    S3 원본을 지운다(503 스위치 · 429 한도 · 400 …). 그 열쇠로 다시 보내면 매번 "사진을 다시
    //    골라 주세요" 다 (#727 리뷰). 핸들러 앞에서 끝난 오류(401 · 403 남의 아이 · 프록시 502)는 원본을
    //    못 지우지만, 새로 올리면 되고 남은 원본은 버킷 수명 규칙이 지운다.
    //    응답을 못 받았을 때(`NetworkError`)만 남긴다 — 서버가 받았는지 모른다.
    if (!(error instanceof NetworkError)) uploadedFor.delete(idempotencyKey);
    throw error;
  }
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
  severity?: string | null;
  reactions?: string[];
  notes?: string | null;
  /** 괜찮았던 식품으로 저장한다 (#825 · 뜻은 `types.ts` 의 `CreateHealthSafetyRequest.tolerated`). */
  tolerated?: boolean;
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

/**
 * 🚨 승인 게이트 ㉡ — **고치기.** 등록과 같은 게이트의 다른 메서드다 (일정 제출의 POST · PATCH 와 같다).
 *
 * 🚨 **응답의 id 가 바뀔 수 있다** (서버 #370) — 고치면 지금 기록을 내리고 새 기록을 넣는다. 바뀐 것이
 *    없으면 같은 id 그대로다. 화면은 응답 뒤 목록을 다시 받아 그리고, 들고 있던 id 를 다시 쓰지 않는다.
 * 🚨 `404 not_found` 는 먼저 고쳐졌거나 내려간 기록이다(다른 보호자, 또는 응답을 잃은 뒤 값을 바꿔
 *    다시 보낸 본인) — 같은 요청을 다시 보내도 같다.
 */
export function updateHealthSafety(
  childId: string,
  safetyId: string,
  body: UpdateHealthSafetyRequest,
  idempotencyKey: IdempotencyKey,
): Promise<UpdateHealthSafetyResponse> {
  return api.patch(idempotentPath.updateHealthSafety(childId, safetyId), body, { idempotencyKey });
}

/* ── 🚨 승인 게이트 ㉡ — 복약 초안 제출 (#537) ─────────────────────────── */

/**
 * 복약 초안을 **등록한다.** 코스와 기간만큼의 회차가 생긴다 — 되돌릴 수 없는 지점이다.
 *
 * 🚨 **게이트 ㉡ 안의 메서드다** (건강 기록 확정). 새 게이트가 아니다 (`idempotentPath.submitMedication`).
 * 🚨 본문은 보호자가 카드에서 확인한 최종 상태 전체다. 낙관적 업데이트 금지 · 재시도는 같은 키.
 * 경로 · 본문 · 응답은 서버 계약(`SubmitMedicationRequest` · `SubmitMedicationResponse`)과 같다.
 */
export function submitMedicationDraft(
  childId: string,
  body: SubmitMedicationBody,
  idempotencyKey: IdempotencyKey,
): Promise<SubmitMedicationResponse> {
  return api.post(idempotentPath.submitMedication(childId), body, { idempotencyKey });
}

/**
 * 복약 수정 초안을 반영한다. 🚨 **이것도 게이트 ㉡ 이고 키를 받는다.** 지난 회차와 그 체크는 서버가 그대로 둔다 —
 * 화면은 그 규칙을 흉내 내지 않고 본문만 보낸다. 🚨 `source` 는 수정에서 바뀌지 않아 싣지 않는다.
 */
export function updateMedicationDraft(
  childId: string,
  scheduleId: string,
  body: ReviseMedicationBody,
  idempotencyKey: IdempotencyKey,
): Promise<SubmitMedicationResponse> {
  return api.patch(idempotentPath.updateMedication(childId, scheduleId), body, { idempotencyKey });
}

/* ── 05 제안 → 일정 초안 ──────────────────────────────────────────────── */

/**
 * 고른 제안을 **채택한다** (`status: approved`). 🚨 **일정과 다른 축이다** — 캘린더에는 아무것도
 * 안 들어가고, 일정으로 만들지는 그다음에 따로 묻는다 (`ApproveSuggestionsRequest` 의 ⚠️).
 *
 * 🚨 Idempotency-Key 를 받지 않는다 — 같은 id 를 두 번 보내도 결과가 같은 상태 전환이다.
 */
export function approveSuggestions(
  childId: string,
  body: ApproveSuggestionsRequest,
): Promise<ApproveSuggestionsResponse> {
  return api.post(`/children/${childId}/suggestions/approve`, body);
}

/**
 * 채택한 제안들을 초안으로 바꾼다. 🚨 **승인 게이트가 아니다 — 아무것도 쓰지 않는다** (#121).
 *    그래서 Idempotency-Key 를 받지 않는다. 두 번 불러도 초안이 두 벌 생길 뿐 DB 는 그대로다.
 *
 * 🚨 **고른 개수와 초안 개수가 1:1 이 아니다** — `food` 는 한 끼로 묶인다 (`types.ts` 참고).
 * ⚠️ 경로가 계약서에 없다 (`CreateEventDraftsRequest` 의 ⚠️).
 */
export function createEventDrafts(
  childId: string,
  body: CreateEventDraftsRequest,
): Promise<CreateEventDraftsResponse> {
  return api.post(`/children/${childId}/suggestions/event-drafts`, body);
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
  /** 🚨 반복 일정(#738)도 같은 경로다 — `repeat` 가 실리면 서버가 회차마다 펼친다. */
  body: SubmitEventBody | SubmitRepeatEventBody,
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
 * 🚨 **아이 스코프 아래 경로다** (#284 · #316). 서버가 `AccessibleChild` 로 권한을 본다 —
 *    `event_id` 하나로는 남의 아이 일정인지 가릴 수 없다.
 * 🚨 본문은 `submitEventDraft` 와 같은 **최종 목록**이다 (`items` 에서 빠진 `item_id` 는 삭제).
 *    `suggestion_ids` 는 싣지 않는다 — 제안 ↔ 일정 연결은 create 에서만 생긴다.
 * 🚨 `reminder` 도 싣지 않는다 (#609). 계약은 받지만, 칸이 없어야 서버가 걸린 알림을 그대로 두고
 *    시각 · 종일이 바뀔 때 알아서 옮긴다. 같은 값을 다시 보내면 시작을 옮긴 경우 그 값이 지나
 *    `422 invalid_reminder` 로 수정까지 막힌다. 걸린 알림은 09 하루 패널이 `PUT …/reminder` 로 바꾼다.
 */
export function updateEventDraft(
  childId: string,
  eventId: string,
  body: Omit<SubmitEventBody, "suggestion_ids" | "reminder">,
  idempotencyKey: IdempotencyKey,
): Promise<SubmitEventResponse> {
  return api.patch(idempotentPath.updateEvent(childId, eventId), body, { idempotencyKey });
}

/**
 * 반복 묶음 고치기 초안을 반영한다 (#738). 🚨 **캘린더 쓰기라 게이트 ㉠ 이고, 키를 받는다** — 같은 게이트의 다른 메서드다.
 *
 * 🚨 아직 시작하지 않은 회차에만 적용되고, 서버는 `before` 와 달라진 값만 바꾼다 (`UpdateSeriesBody`).
 * 🚨 `reminder` 를 싣지 않는다 — 수정 초안(`updateEventDraft`)과 같다. 걸린 알림은 회차마다 서버가 옮긴다.
 * 경로와 본문은 서버 `PATCH /children/{cid}/event-series/{sid}`(#761)와 같다 — 짝은 `contract/schema-map.ts`.
 */
export function updateEventSeries(
  childId: string,
  seriesId: string,
  body: UpdateSeriesBody,
  idempotencyKey: IdempotencyKey,
): Promise<UpdateSeriesResponse> {
  return api.patch(idempotentPath.updateEventSeries(childId, seriesId), body, { idempotencyKey });
}

/* ── 04 대화의 삭제 확정 (#535) ───────────────────────────────────────── */

/**
 * 🚨 **체크한 기록 · 일정을 지운다** — 되살릴 수 없다. 대화 카드에서 두 번 누른 뒤에만 부른다.
 *
 * 서버는 **제안 후보 안의 체크한 것만** 한 트랜잭션에서 지운다. 하나라도 후보 밖이면 422 `not_in_proposal`
 * 이고 아무것도 지우지 않는다. 24시간이 지났으면 409 `proposal_expired`, 이미 끝난 제안이면 409 `proposal_closed`.
 * 🚨 키는 **체크한 id 목록에 묶는다** (`current(payload)`) — 실패한 뒤 체크를 바꿔 다시 누르면 다른 본문이다.
 */
export function confirmDeleteProposal(
  childId: string,
  proposalId: string,
  body: ConfirmDeleteProposalRequest,
  idempotencyKey: IdempotencyKey,
) {
  return api.post<DeleteProposalResponse>(
    idempotentPath.confirmDeleteProposal(childId, proposalId),
    body,
    { idempotencyKey },
  );
}

/**
 * 삭제 제안을 아무것도 지우지 않고 닫는다. 키가 없다 — 지우지 않아서 다시 보내도 같은 응답이 온다.
 * 이미 지운 제안이면 409 `proposal_closed`, 24시간이 지났으면 409 `proposal_expired`.
 */
export function cancelDeleteProposal(childId: string, proposalId: string) {
  return api.post<DeleteProposalResponse>(
    `/children/${childId}/delete-proposals/${proposalId}/cancel`,
  );
}

/* ── 07 기록 상세의 기록 지우기 (#736 · #779) ───────────────────────────── */

/**
 * 🚨 **기록 한 건을 지운다** — 되살릴 수 없다. 기록 상세 시트에서 확인 띠를 한 번 더 누른 뒤에만 부른다.
 *    보호자가 직접 누르는 삭제라 승인 게이트가 아니라 확인 창이다 (최상위 §2 — 게이트 ㉢ 은 AI 가 고른 삭제다).
 *
 * 서버는 대화 삭제(#535)와 같은 함수로 지운다 — 상태는 `deleted`, 검색 벡터를 비우고 묶인 기억을 다시 계산한다.
 * `deleted` 가 아닌 기록은 상태와 상관없이 지운다(고친 기록 `stand_alone` · `inactive` 도). 응답은 204(본문 없음).
 * 이미 지웠거나 없는 기록이면 404 `not_found` 다 — 다른 보호자가 먼저 지웠을 수 있어 실패가 아니다.
 * 🚨 키는 **기록에 묶는다** (`current(\`${kind}/${id}\`)`) — 본문이 없어서 다른 기록을 지울 때 새 키가 나가게 한다.
 */
export function deleteObservation(
  childId: string,
  kind: ObservationKind,
  observationId: string,
  idempotencyKey: IdempotencyKey,
) {
  return api.delete<void>(idempotentPath.deleteObservation(childId, kind, observationId), {
    idempotencyKey,
  });
}
