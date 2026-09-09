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
  consent: "필수 동의 미완료 — 403 consent_required 로 저장 차단",
  stale: "6개월 지난 근거 — is_stale 인 기억만 남은 상태 (NF-08)",
} as const;

export type Scenario = keyof typeof SCENARIOS;

export const DEFAULT_SCENARIO: Scenario = "default";

const STORAGE_KEY = "yukameo.mock.scenario";

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
