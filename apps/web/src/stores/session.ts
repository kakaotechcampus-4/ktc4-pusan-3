import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import { refreshSession, setAuthToken, setSessionRefreshedHandler } from "@/lib/api/client";
import { useConversationStore } from "@/stores/conversation";
import { useDraftStore } from "@/stores/draft";
import { useMedicationDraftStore } from "@/stores/medication-draft";
import { usePhotoDraftStore } from "@/stores/photo-draft";
import { useSafetyScanDraftStore } from "@/stores/safety-scan-draft";
import { useEventDraftStore } from "./event-draft";

/**
 * 로그인 토큰과 "지금 보고 있는 아이" 만 담는다.
 *
 * 🚨 여기에 아이 이름·생일·알레르기 같은 개인정보를 캐시하지 않는다.
 *    서버 상태는 TanStack Query 가 들고, 이 스토어는 클라이언트 상태만 들고 있는다.
 *    (CLAUDE.md §2 개인정보 — 로그·저장소에 원문 대신 id.)
 *
 * 🚨 **토큰은 메모리에만 둔다.** `sessionStorage` · `localStorage` 어디에도 쓰지 않는다
 *    (docs/web/auth-consent-v1.md §2). 이 서비스의 XSS 경로는 특정된다 — 기관 공지 붙여넣기·OCR
 *    로 들어온 외부 텍스트가 LLM 을 거쳐 화면에 렌더링된다 (F-07 · F-14). 저장소에 둔 토큰은
 *    XSS 한 건에 그대로 꺼내진다. 메모리의 access 토큰은 15분이면 끝나고, 오래 사는 refresh
 *    토큰은 서버가 심은 HttpOnly 쿠키라 JS 가 읽지 못한다.
 *
 * 그래서 앱을 다시 켜면 토큰이 비어 있다. `restore()` 가 refresh 쿠키로 다시 받는다 — 앱을
 * 껐다 켜도 로그인이 유지되는 길은 이것 하나다.
 *
 * `activeChildId` 만 `localStorage` 에 남긴다. 권한 근거가 아니라 "마지막에 본 아이" 라는
 * 화면 선택값이고(정본은 URL), 앱을 다시 켰을 때 그 아이의 홈으로 열려고 둔다.
 */
interface SessionState {
  token: string | null;
  /** epoch ms. 서버가 준 `expires_in` 으로 만든다 — 401 을 맞고 나서야 아는 것보다 낫다. */
  expiresAt: number | null;
  activeChildId: string | null;
  /**
   * 앱을 켠 뒤 세션 확인(`restore`)이 끝났는지. false 동안 인증이 필요한 화면을 그리면
   * 로그인한 사용자에게도 로그인 화면이 한 번 깜빡인다.
   * ⚠️ 이름은 persist 복구 시절의 것이다. 지금은 **refresh 확인이 끝났는가** 이고, 확인 실패
   *    화면에서 다시 시도하면 다시 `false` 로 돌아간다.
   */
  hydrated: boolean;
  /**
   * 세션을 확인하지 못했다 (서버에 닿지 못함). 🚨 **로그아웃이 아니다** — refresh 쿠키는
   * 그대로라 연결이 돌아오면 `restore()` 로 다시 받는다. 로그인 화면으로 보내지 않는다.
   */
  restoreFailed: boolean;

  signIn: (token: string, expiresIn: number) => void;
  signOut: () => void;
  setActiveChild: (childId: string | null) => void;
  /** refresh 쿠키로 세션을 되살린다. 앱을 켤 때 한 번, 확인 실패 화면의 다시 시도에서 부른다. */
  restore: () => Promise<void>;
}

/**
 * 지난 판(#465 전)이 `sessionStorage` 에 토큰을 두던 자리. 열려 있던 탭에 남은 사본을 지운다 —
 * 토큰이 저장소에 남지 않는다는 규칙이 업데이트 직후의 탭에서도 지켜지게.
 */
const LEGACY_SESSION_KEY = "icatch.session";

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      token: null,
      expiresAt: null,
      activeChildId: null,
      hydrated: false,
      restoreFailed: false,

      signIn: (token, expiresIn) => {
        const expiresAt = Date.now() + expiresIn * 1000;
        setAuthToken(token, expiresAt);
        set({ token, expiresAt, restoreFailed: false });
      },
      signOut: () => {
        setAuthToken(null);
        // 🚨 아직 안 보낸 입력·사진에는 아이 이야기가 그대로 들어 있다. 로그아웃하면 같이 지운다 —
        //    메모리에만 있어도 다음 사람이 같은 탭을 쓰면 남의 아이 이야기가 보인다.
        useDraftStore.getState().clearAll();
        // 🚨 보낸 한 줄 · run 결과 · 되물은 질문에도 아이 이야기가 들어 있다 (`stores/conversation.ts`).
        //    도는 스트림도 여기서 끊는다 — 안 끊으면 로그아웃한 뒤에 결과가 빈 스토어로 흘러든다.
        useConversationStore.getState().clearAll();
        // 🚨 고르고 아직 안 올린 사진도 같이 지운다 (`stores/photo-draft.ts` — 같은 이유다).
        usePhotoDraftStore.getState().clearAll();
        // 🚨 넘기다 만 알레르기 검사지 사진도 같이 놓는다 (의료 기록이다 · 최상위 §2).
        //    여기서는 objectURL 까지 해제한다 — 탭이 사는 동안 메모리에 남지 않게.
        useSafetyScanDraftStore.getState().clearAll();
        // 🚨 아직 제출하지 않은 일정 초안도 지운다 (`stores/event-draft.ts`). 서버에 없는 값이라
        //    여기서 안 비우면 다음 사람이 같은 탭에서 남의 아이 일정을 본다.
        useEventDraftStore.getState().clearAll();
        // 🚨 복약 초안은 약 이름 · 복용 이유가 든 건강 정보다 (`stores/medication-draft.ts` · #537).
        useMedicationDraftStore.getState().clearAll();
        set({ token: null, expiresAt: null, activeChildId: null });
      },
      setActiveChild: (activeChildId) => set({ activeChildId }),

      restore: async () => {
        set({ hydrated: false, restoreFailed: false });
        try {
          sessionStorage.removeItem(LEGACY_SESSION_KEY);
        } catch {
          // 저장소를 막아 둔 브라우저다. 지울 사본도 없다.
        }
        try {
          // 성공하면 아래 핸들러가 signIn 을 부른다. `null` 이면 로그인 전이다 (쿠키 없음 · 만료).
          await refreshSession();
        } catch {
          // 서버에 닿지 못했다. 그 사이 다른 길로 로그인했으면(콜백) 실패로 보지 않는다.
          if (get().token === null) set({ restoreFailed: true });
        }
        set({ hydrated: true });
      },
    }),
    {
      name: "icatch.active-child",
      storage: createJSONStorage(() => localStorage),
      // 🚨 토큰을 여기 넣지 않는다 (위 머리말).
      partialize: (s) => ({ activeChildId: s.activeChildId }),
      // SSR 과 첫 렌더 HTML 을 맞추기 위해 수동 복구한다 (Providers 에서 rehydrate 호출).
      skipHydration: true,
    },
  ),
);

// 갱신(앱을 켤 때 · access 만료 직전 · 401 뒤)으로 받은 토큰을 스토어가 받는다.
// client.ts 는 스토어를 import 하지 못해서(서버 컴포넌트) 이쪽에서 꽂는다.
setSessionRefreshedHandler(({ token, expiresIn }) =>
  useSessionStore.getState().signIn(token, expiresIn),
);

/**
 * 로그인돼 있는지. 🚨 access 의 만료 시각은 보지 않는다 — 만료된 access 는 `lib/api/client.ts` 가
 * refresh 쿠키로 갱신하고 다시 보낸다. 여기서 만료를 로그아웃으로 보면 15분마다 화면이 튕긴다.
 * 세션이 정말 끝났는지는 갱신까지 실패한 401 이 알려준다 (전역 처리가 `signOut` 을 부른다).
 */
export function hasLiveSession(state: Pick<SessionState, "token">): boolean {
  return state.token !== null;
}
