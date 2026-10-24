/**
 * TanStack Query 키 팩토리. 문자열을 손으로 쓰지 않는다 — 무효화 범위가 어긋나면 화면이 낡는다.
 *
 * 규칙: 아이 스코프 데이터는 전부 qk.child(cid) 아래에 둔다.
 * 그래야 아이를 바꿀 때 invalidateQueries({ queryKey: qk.child(cid) }) 한 번으로 끝난다.
 */
export const qk = {
  /** GET /auth/{provider}/status — 00 화면 prefetch. */
  authStatus: (provider: string) => ["auth-status", provider] as const,
  /**
   * GET /policies — 가입 동의 화면이 로그인 **전에** 부른다 (#91).
   * 아이 스코프까지 한 응답에 오므로 아이별로 키를 나누지 않는다.
   */
  policies: () => ["policies"] as const,

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
   * 11 아이 프로필 (`GET /children/{cid}` · PR #334). 아이 스코프 아래라 아이를 바꾸면
   * 한 번에 무효화된다.
   */
  childProfile: (childId: string) => [...qk.child(childId), "profile"] as const,
  /**
   * 복약 화면의 진행 중 코스 · 회차 (`GET /children/{cid}/medications` · #537 · 잠정). 아이 스코프 아래라
   * 복약을 제출하면 `qk.child` 무효화에 같이 걸린다.
   */
  medications: (childId: string) => [...qk.child(childId), "medications"] as const,
  /** 11 키·몸무게 측정 로그 (`GET /children/{cid}/growth` · PR #353). */
  growth: (childId: string) => [...qk.child(childId), "growth"] as const,
  /**
   * 05 제안 후보. 같은 run·같은 Agent 조합이면 같은 화면이라 키에 둘 다 담는다 —
   * 뒤로 갔다 오면 Agent 를 다시 돌리지 않는다 (NF-01 은 model call 을 센다).
   * 🚨 홈 버튼의 `promptKey` 도 담는다 — 빼면 "저녁" 으로 받은 제안이 "내일 아침" 버튼에 그대로 뜬다.
   */
  suggestions: (
    childId: string,
    runId: string | null,
    agents: readonly string[],
    promptKey: string | null = null,
  ) => [...qk.child(childId), "suggestions", runId, [...agents].join(","), promptKey] as const,
  /** 07 피드백 탭이 평가할 제안 목록. ⚠️ 엔드포인트가 계약서 협의 대상이다 (types.ts). */
  suggestionList: (childId: string) => [...qk.child(childId), "suggestion-list"] as const,
  /** 09 월 조회. `month` 는 `YYYY-MM` — 달을 넘기면 다른 키라 이전 달이 캐시에 남는다. */
  calendar: (childId: string, month: string) => [...qk.calendarMonths(childId), month] as const,
  /**
   * 09 월 조회 **전부** (달마다 키가 따로라 무효화는 이 접두어로 한다). 요소 단위로 비교하므로
   * `"calendar"` 는 `"calendar-day"` 와 겹치지 않는다 — 하루 보기는 이 접두어에 걸리지 않는다.
   */
  calendarMonths: (childId: string) => [...qk.child(childId), "calendar"] as const,
  /**
   * 09 일 조회. 🚨 월 키 **아래에 두지 않는다** — 일기를 쓰면 그날만 무효화하고 싶은데,
   * 월 키 아래면 `calendar` 를 통째로 지우게 되고 달 전체가 다시 뜬다.
   */
  calendarDay: (childId: string, date: string) =>
    [...qk.child(childId), "calendar-day", date] as const,
  /**
   * 04 지난 대화 (`GET /children/{cid}/chat` · #228) 전부. 무효화는 이 접두어로 한다.
   */
  chat: (childId: string) => [...qk.child(childId), "chat"] as const,
  /**
   * 대화 목록 — 하루 한 장씩 이어 받는다. `from` 은 처음 연 날이다 (`null` 이면 오늘 · 09 입구면 그날).
   * 🚨 **시작한 날마다 키가 다르다** — 같은 키면 오늘부터 받은 목록과 그날부터 받은 목록의 장이 섞인다.
   * 🚨 run 이 끝나면 통째로 무효화하지 않고 오늘 장만 갈아 끼운다 (`lib/chat-history.ts` 의 `refreshAfterRun`).
   */
  chatList: (childId: string, from: string | null) => [...qk.chat(childId), "list", from] as const,
  /**
   * 03 홈의 "오늘 대화 이어보기" 가 보는 오늘 한 장. 🚨 **`chat` 과 따로 둔다** — 같은 키면 홈에 올 때마다
   * 대화 화면에서 받아 둔 지난 날을 전부 다시 받는다. `chat` 아래라 대화 무효화에 같이 걸린다.
   */
  chatToday: (childId: string) => [...qk.chat(childId), "today"] as const,
  /**
   * 그날 한 장 — 결과를 못 받은 줄을 다시 보냈더니 서버가 이미 끝난 run 이라고 한 경우, 그 줄이 저장된 결과를
   * 찾아 그린다 (#538 · `ChatThread` 의 `RecordedReply`). 🚨 대화 목록(`chatList`)에 기대지 않는다 — 되묻기 답이면
   * 그 턴이 목록에 아직 받지 않은 지난 날에 붙어 있을 수 있다.
   */
  chatDay: (childId: string, date: string) => [...qk.chat(childId), "day", date] as const,
  corrections: (childId: string, ref?: { kind: string; id: string }) =>
    [...qk.child(childId), "corrections", ref ?? null] as const,
} as const;
