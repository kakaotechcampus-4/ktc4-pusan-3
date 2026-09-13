import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import { setAuthToken } from "@/lib/api/client";

/**
 * 로그인 토큰과 "지금 보고 있는 아이" 만 담는다.
 *
 * 🚨 여기에 아이 이름·생일·알레르기 같은 개인정보를 캐시하지 않는다.
 *    서버 상태는 TanStack Query 가 들고, 이 스토어는 클라이언트 상태만 들고 있는다.
 *    (CLAUDE.md §2 개인정보 — 로그·저장소에 원문 대신 id.)
 */
interface SessionState {
  token: string | null;
  activeChildId: string | null;
  /** persist 복구가 끝났는지. false 동안 인증이 필요한 화면을 그리면 깜빡인다. */
  hydrated: boolean;

  signIn: (token: string) => void;
  signOut: () => void;
  setActiveChild: (childId: string | null) => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      token: null,
      activeChildId: null,
      hydrated: false,

      signIn: (token) => {
        setAuthToken(token);
        set({ token });
      },
      signOut: () => {
        setAuthToken(null);
        set({ token: null, activeChildId: null });
      },
      setActiveChild: (activeChildId) => set({ activeChildId }),
    }),
    {
      name: "yukameo.session",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ token: s.token, activeChildId: s.activeChildId }),
      // SSR 과 첫 렌더 HTML 을 맞추기 위해 수동 복구한다 (Providers 에서 rehydrate 호출).
      skipHydration: true,
      onRehydrateStorage: () => (state) => {
        setAuthToken(state?.token ?? null);
        useSessionStore.setState({ hydrated: true });
      },
    },
  ),
);
