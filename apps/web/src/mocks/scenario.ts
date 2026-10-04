/**
 * 목 시나리오 스위치.
 *
 * 실서버로는 만들기 어려운 상태들을 손으로 켜고 끈다. 기억이 쌓인 뒤에야 나오거나
 * (scarcity · stale), 타이밍에 걸려야 나오거나 (partial), 모델이 죽어야 나오는 것들이라
 * (failed) 화면을 확인할 방법이 이것뿐이다.
 *
 * 쓰는 법 — 주소창에 `?scenario=partial` 을 붙이면 localStorage 에 저장되고 그다음부터 유지된다.
 * 되돌리려면 `?scenario=default`.
 */

export const SCENARIOS = {
  default: "기본 — 기억이 쌓인 상태 · 개인화 추천",
  empty: "기록 0건 — 03 홈 빈 상태 (highlight: null)",
  scarcity: "근거 부족 — 개인화 대신 일반 추천 + 되묻는 질문 1개",
  partial: "Agent 2개 중 1개 실패 — 성공·실패를 한 화면에 (NF-06)",
  failed: "입력 처리 실패 — raw_text 를 입력창에 되돌림",
  disconnected: "종료 이벤트 없이 스트림이 끊김 — 저장 여부를 모르는 상태",
  consent: "필수 동의 미완료 — 신규 가입 대기 · 403 consent_required 로 저장 차단",
  auth_unready: "로그인 미연결 — GET /auth/kakao/status 가 ready: false",
  // 🚨 **받아 간 뒤에 바뀐 것**이라 정적으로 만들 수 없다 (`handlers/policies.ts`). 첫 제출이
  //    버전을 올려 400 을 만들고, 화면은 다시 받아 **바뀐 항목만** 체크를 풀어야 한다.
  policy_bumped: "가입 동의 — 제출 직전에 약관이 새 판으로 (400 policy_version_invalid)",
  stale: "6개월 지난 근거 — is_stale 인 기억만 남은 상태 (NF-08)",
  photo_unreadable: "08 사진 — 읽어낼 게 없는 사진 (failed · 저장된 것 없음)",
  // 🚨 **화면에는 아무 차이도 안 난다.** 08 은 시트에서 고른 lane 을 그대로 쓰고 서버 추측을
  //    보여주지 않는다 — 이 시나리오는 "추측이 선언을 덮지 않는다" 를 거는 **계약 테스트용**이다.
  photo_lane_mismatch: "08 사진 — 서버 추측이 고른 종류와 어긋남 (계약 테스트용 · 화면 변화 없음)",
  photo_meal_plan: "08 사진 — 한 달치 식단표 (항목 21건 · 잘 읽은 것이 접혀 있어야 하는 이유)",
  // 🚨 아래 넷은 Supervisor 가 붙어야 나오는 상태다 (#141). 안전 사전검사에 걸리거나, 아직 없는
  //    Agent 로 가거나, Memory 가 되묻거나, 하루 한도에 걸려야 나온다 — 실서버로는 만들기 어렵다.
  guidance: "04 안내 — 알레르기를 대신 저장하지 않는다 (저장 0건 · 직접 입력 안내)",
  // 🚨 **한 줄에 두 얘기가 섞인 경우다** ("계란 잘 먹었어. 그리고 땅콩 알레르기 있어").
  //    서버는 앞은 저장하고 뒤는 안내로 돌린다 — 화면이 안내만 세우고 저장을 감추면
  //    보호자는 **아무것도 저장 안 된 줄 안다.** unavailable 과 같은 종류의 회귀다.
  guidance_mixed: "04 안내 + 저장 — 섞인 한 줄 (안내와 저장된 기록이 한 화면에)",
  // 🚨 **저장과 같이 온다는 것이 요점이다.** 이 시나리오가 잡는 회귀는 "준비 중 안내가 떴다고
  //    저장된 기록이 화면에서 사라지는 것" 이다 (NF-06 과 같은 규칙).
  unavailable: "04 준비 중 — 없는 Agent 로 간 요청 + 저장은 그대로 (성공·실패 한 화면)",
  note_question: "04 되묻기 — Memory 질문 하나만 (저장 0건 · 이어서 적기 동선)",
  // 🚨 **#158 리뷰가 짚은 빠진 경우다.** Memory 는 한 후보를 저장한 뒤 다른 후보의 정보가
  //    모자라면 글로 되묻는다 — `Saved` 와 `MemoryNote` 가 한 run 에 같이 나간다
  //    (`apps/api/app/agents/pipeline.py`). 이 대본이 없어서, 답할 때 원문을 되돌리면
  //    **이미 저장된 조각이 또 저장된다**는 것을 못 봤다.
  note_mixed: "04 일부 저장 + 되묻기 — 한 run 에 저장과 질문이 같이 (원문 재전송 금지 근거)",
  // 🚨 **이어받기 run 이 한 번 실패한다** (#175). 서버는 그때 맥락을 되돌려 두므로, 다시 시도가
  //    같은 `reply_to` 를 실으면 저장되고 빠뜨리면 맥락 없는 새 입력이라 **질문이 또 뜬다.**
  reply_failed: "04 되묻기 답 — 이어받기 run 이 한 번 실패 (다시 시도가 reply_to 를 유지해야 한다)",
  // 🚨 **서버가 앞 이야기를 놓친 경우다** (#175 — 15분 만료 · 서버 재시작). 방금 물었어도 답은 400 이다.
  reply_unavailable:
    "03 되묻기 답 — 400 reply_context_unavailable (다시 시도 없이 질문을 놓아야 한다)",
  daily_limit: "03 한 줄 보내기 — 하루 한도 초과 429 (다시 시도 버튼이 없어야 한다)",
} as const;

export type Scenario = keyof typeof SCENARIOS;

export const DEFAULT_SCENARIO: Scenario = "default";

const STORAGE_KEY = "icatch.mock.scenario";

function isScenario(value: string | null): value is Scenario {
  return value !== null && value in SCENARIOS;
}

/** 핸들러는 요청마다 이걸 부른다 — 새로고침 없이 바꿔도 다음 요청부터 반영된다. */
export function currentScenario(): Scenario {
  if (typeof localStorage === "undefined") return DEFAULT_SCENARIO;
  const stored = localStorage.getItem(STORAGE_KEY);
  return isScenario(stored) ? stored : DEFAULT_SCENARIO;
}

export function setScenario(scenario: Scenario): void {
  localStorage.setItem(STORAGE_KEY, scenario);
}

/** `?scenario=` 를 읽어 저장한다. 목을 켜기 전에 한 번 부른다. */
export function syncScenarioFromUrl(): void {
  if (typeof window === "undefined") return;
  const requested = new URL(window.location.href).searchParams.get("scenario");
  if (isScenario(requested)) setScenario(requested);
}
