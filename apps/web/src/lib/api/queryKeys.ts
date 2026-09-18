/**
 * TanStack Query 키 팩토리. 문자열을 손으로 쓰지 않는다 — 무효화 범위가 어긋나면 화면이 낡는다.
 *
 * 규칙: 아이 스코프 데이터는 전부 qk.child(cid) 아래에 둔다.
 * 그래야 아이를 바꿀 때 invalidateQueries({ queryKey: qk.child(cid) }) 한 번으로 끝난다.
 */
export const qk = {
  /** GET /auth/{provider}/status — 로그인 전에도 부르는 유일한 쿼리다 (00 화면 prefetch). */
  authStatus: (provider: string) => ["auth-status", provider] as const,

  me: () => ["me"] as const,
  consents: (childId?: string) => ["consents", childId ?? null] as const,

  child: (childId: string) => ["child", childId] as const,

  home: (childId: string) => [...qk.child(childId), "home"] as const,
  /** 02 온보딩의 발달 선별 문항. 나이대에 따라 달라져서 아이 스코프 아래에 둔다. */
  devScreening: (childId: string) => [...qk.child(childId), "dev-screening"] as const,
  observations: (childId: string, filters?: Record<string, unknown>) =>
    [...qk.child(childId), "observations", filters ?? null] as const,
  observation: (childId: string, kind: string, id: string) =>
    [...qk.child(childId), "observation", kind, id] as const,
  affinities: (childId: string, filters?: Record<string, unknown>) =>
    [...qk.child(childId), "affinities", filters ?? null] as const,
  healthSafety: (childId: string) => [...qk.child(childId), "health-safety"] as const,
  /** 10 설정의 "함께 보는 보호자". 아이 스코프라 `qk.child` 아래에 둔다. */
  parents: (childId: string) => [...qk.child(childId), "parents"] as const,
  /**
   * 05 제안 후보. 같은 run·같은 Agent 조합이면 같은 화면이라 키에 둘 다 담는다 —
   * 뒤로 갔다 오면 Agent 를 다시 돌리지 않는다 (NF-01 은 model call 을 센다).
   */
  suggestions: (childId: string, runId: string | null, agents: readonly string[]) =>
    [...qk.child(childId), "suggestions", runId, [...agents].join(",")] as const,
  /** 07 피드백 탭이 평가할 제안 목록. ⚠️ 엔드포인트가 계약서 협의 대상이다 (types.ts). */
  suggestionList: (childId: string) => [...qk.child(childId), "suggestion-list"] as const,
  /** 09 월 조회. `month` 는 `YYYY-MM` — 달을 넘기면 다른 키라 이전 달이 캐시에 남는다. */
  calendar: (childId: string, month: string) => [...qk.child(childId), "calendar", month] as const,
  /**
   * 09 일 조회. 🚨 월 키 **아래에 두지 않는다** — 일기를 쓰면 그날만 무효화하고 싶은데,
   * 월 키 아래면 `calendar` 를 통째로 지우게 되고 달 전체가 다시 뜬다.
   */
  calendarDay: (childId: string, date: string) =>
    [...qk.child(childId), "calendar-day", date] as const,
  corrections: (childId: string, ref?: { kind: string; id: string }) =>
    [...qk.child(childId), "corrections", ref ?? null] as const,
} as const;
