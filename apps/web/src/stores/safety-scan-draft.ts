import { create } from "zustand";

/**
 * 11 프로필에서 **막 고른 알레르기 검사지 사진 한 장**을 11-2 확인 화면까지 들고 가는 자리.
 *
 * 🚨 **왜 스토어인가 — 파일은 URL 로 못 넘긴다.** 사진을 고르는 곳은 프로필의 시트고(부모가
 *    "추가" 를 누른 그 자리다) 옮겨 적은 것을 확인하는 화면은
 *    `/child/{cid}/profile/safety-scan` 이다. `File` 은 쿼리 파라미터에 실을 수 없고,
 *    "확인 화면에 도착한 다음 파일 입력을 대신 눌러 준다" 도 안 된다 — 라우트 이동이 사용자
 *    제스처를 소비해서 브라우저가 `input.click()` 을 막는다.
 *
 * 🚨 **`persist` 를 붙이지 말 것.** 아이 **의료 기록** 사진이라 디스크에 남으면 안 된다
 *    (최상위 CLAUDE.md §2 개인정보 · `stores/draft.ts` 와 같은 규칙). 알레르기 검사지는
 *    이 저장소가 다루는 것 중 제일 민감한 축이다 — `File` 이 직렬화가 안 된다는 것은
 *    이유가 아니라 우연이다.
 *
 * 🚨 **미리보기 URL 을 만들고 해제하는 자리가 여기 하나다.** 둘 다 부수 효과라 렌더 중에
 *    부르면 안 되고, **effect 의 cleanup 에 걸어도 안 된다** — StrictMode 가 mount 직후
 *    cleanup 을 한 번 돌려서 방금 넘겨받은 사진의 URL 이 그 자리에서 해제되고 미리보기가
 *    깨진다 (`ERR_FILE_NOT_FOUND` 로 실제로 났다). 해제는 **다음 사진을 고르는 순간**과
 *    **로그아웃** 두 이벤트에서만 — 그래서 살아 있는 URL 은 언제나 최대 한 개다.
 *
 * 🚨 **한 번 쓰면 뗀다** (`release`). 남겨 두면 확인 화면을 다시 열 때 **지난번 검사지가
 *    저절로 올라간다** — 보호자가 고른 적 없는 사진이 분석으로 들어가는 경로다.
 *    🚨 `release` 는 URL 을 **해제하지 않는다.** 그때부터 사진을 그리는 것은 화면이다.
 *
 * 🚨 **childId 를 함께 들고 확인한다.** 아이를 바꾼 뒤 들어오면 다른 아이의 검사지가 이 아이
 *    알레르기로 등록될 수 있다. 아이 스코프의 정본은 URL 이고(apps/web/CLAUDE.md §3),
 *    이 값은 그 URL 과 맞을 때만 쓴다.
 */
export interface PendingScanPhoto {
  file: File;
  /** `URL.createObjectURL` 결과. 🚨 해제는 이 스토어만 한다 (다음 사진 · 로그아웃). */
  url: string;
}

interface SafetyScanDraftState {
  /** 넘겨줄 사진. 받는 화면이 `release` 하면 비고, **URL 은 그때 살아 있다.** */
  pending: (PendingScanPhoto & { childId: string }) | null;
  /**
   * 지금 살아 있는 objectURL 하나. `pending` 과 따로 두는 이유는 화면이 `release` 한 뒤에도
   * 그 URL 로 사진을 그리고 있어서다 — 해제 책임만 여기 남는다.
   */
  live: string | null;

  /** 🚨 **이벤트 핸들러에서만 부른다.** 앞 사진의 URL 은 여기서 해제한다. */
  putPhoto: (childId: string, file: File) => void;
  /** 순수한 읽기. 렌더 중에 불러도 안전하다 — 아무것도 바꾸지 않는다. */
  peek: (childId: string) => PendingScanPhoto | null;
  /** 화면이 가져갔다. 참조만 놓고 **URL 은 해제하지 않는다.** */
  release: () => void;
  /** 로그아웃에서 부른다 — 다음 사람에게 남의 아이 검사지를 넘기지 않는다. 여기서는 해제한다. */
  clearAll: () => void;
}

export const useSafetyScanDraftStore = create<SafetyScanDraftState>()((set, get) => ({
  pending: null,
  live: null,

  putPhoto: (childId, file) => {
    // 앞 사진은 이 순간 화면에서 내려간다. 살아 있는 URL 은 언제나 한 개다.
    const previous = get().live;
    if (previous) URL.revokeObjectURL(previous);

    const url = URL.createObjectURL(file);
    set({ live: url, pending: { childId, file, url } });
  },

  peek: (childId) => {
    const pending = get().pending;
    if (!pending || pending.childId !== childId) return null;
    return { file: pending.file, url: pending.url };
  },

  release: () => set({ pending: null }),

  clearAll: () => {
    const live = get().live;
    if (live) URL.revokeObjectURL(live);
    set({ pending: null, live: null });
  },
}));
