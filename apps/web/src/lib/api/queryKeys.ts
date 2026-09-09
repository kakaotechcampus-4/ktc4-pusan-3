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
  calendar: (childId: string, month: string) => [...qk.child(childId), "calendar", month] as const,
  corrections: (childId: string, ref?: { kind: string; id: string }) =>
    [...qk.child(childId), "corrections", ref ?? null] as const,
} as const;
