import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import { setAuthToken } from "@/lib/api/client";

/**
 * 로그인 토큰과 "지금 보고 있는 아이" 만 담는다.
 *
 * 🚨 여기에 아이 이름·생일·알레르기 같은 개인정보를 캐시하지 않는다.
 *    서버 상태는 TanStack Query 가 들고, 이 스토어는 클라이언트 상태만 들고 있는다.
 *    (CLAUDE.md §2 개인정보 — 로그·저장소에 원문 대신 id.)
 *
 * 🚨 저장소는 `sessionStorage` 다. `localStorage` 금지 —
 *    docs/api/auth-kakao-v1.md §4-3 이 못박은 규칙이고, 이유는 이 서비스의 XSS 경로가
 *    특정된다는 것이다(기관 공지 붙여넣기·OCR 로 들어온 외부 텍스트가 LLM 을 거쳐
 *    화면에 렌더링된다 · F-07 · F-14). 토큰이 JS 에서 접근 가능하므로 XSS 한 건이
 *    세션을 넘긴다. 그래서 apps/web/CLAUDE.md §4 의 "외부 텍스트·LLM 출력을 HTML 로
 *    렌더링하지 않는다" 가 이 저장 규칙의 전제다.
 *
 * ⚠️ 부수 효과 — `activeChildId` 도 세션 스코프가 된다. 탭을 닫으면 "마지막에 본 아이"
 *    복원이 사라진다. 권한 근거가 아니라 화면 선택값일 뿐이고(정본은 URL) 다시 고르면
 *    되는 값이라, 필드 하나 때문에 저장소를 두 벌로 나누지 않았다.
 */
interface SessionState {
  token: string | null;
  /** epoch ms. 서버가 준 `expires_in` 으로 만든다 — 401 을 맞고 나서야 아는 것보다 낫다. */
  expiresAt: number | null;
  activeChildId: string | null;
  /** persist 복구가 끝났는지. false 동안 인증이 필요한 화면을 그리면 깜빡인다. */
  hydrated: boolean;

  signIn: (token: string, expiresIn: number) => void;
  signOut: () => void;
  setActiveChild: (childId: string | null) => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      token: null,
      expiresAt: null,
      activeChildId: null,
      hydrated: false,

      signIn: (token, expiresIn) => {
        setAuthToken(token);
        set({ token, expiresAt: Date.now() + expiresIn * 1000 });
      },
      signOut: () => {
        setAuthToken(null);
        set({ token: null, expiresAt: null, activeChildId: null });
      },
      setActiveChild: (activeChildId) => set({ activeChildId }),
    }),
    {
      name: "yukameo.session",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (s) => ({
        token: s.token,
        expiresAt: s.expiresAt,
        activeChildId: s.activeChildId,
      }),
      // SSR 과 첫 렌더 HTML 을 맞추기 위해 수동 복구한다 (Providers 에서 rehydrate 호출).
      skipHydration: true,
      onRehydrateStorage: () => (state) => {
        // 복구한 토큰이 이미 만료됐으면 들고 있지 않는다 — 첫 요청이 401 이 될 뿐이다.
        const expired = state?.expiresAt != null && state.expiresAt <= Date.now();
        if (expired) {
          setAuthToken(null);
          useSessionStore.setState({ token: null, expiresAt: null, hydrated: true });
          return;
        }
        setAuthToken(state?.token ?? null);
        useSessionStore.setState({ hydrated: true });
      },
    },
  ),
);

/** 토큰이 있고 아직 살아 있는지. 만료 시각을 모르는 옛 세션은 살아 있는 것으로 본다. */
export function hasLiveSession(state: Pick<SessionState, "token" | "expiresAt">): boolean {
  if (!state.token) return false;
  return state.expiresAt == null || state.expiresAt > Date.now();
}
