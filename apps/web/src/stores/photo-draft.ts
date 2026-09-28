import { create } from "zustand";

import type { PhotoLane } from "@/lib/api/types";

/**
 * 03 홈 · 09 캘린더에서 **막 고른 사진 한 장**을 08 화면까지 들고 가는 자리.
 *
 * 🚨 **왜 스토어인가 — 파일은 URL 로 못 넘긴다.** 사진을 고르는 시트는 홈에 서고(부모가
 *    카메라 버튼을 누른 그 자리다) 읽어낸 것을 확인하는 화면은 `/child/{cid}/photos` 다.
 *    `File` 은 쿼리 파라미터에 실을 수 없고, "08 에 도착한 다음 파일 입력을 대신 눌러 준다"
 *    는 방법도 안 된다 — 라우트 이동이 사용자 제스처를 소비해서 브라우저가
 *    `input.click()` 을 막는다. 그래서 고른 파일을 화면 밖에 한 칸 두고 넘긴다.
 *
 * 🚨 **저장소에 쓰지 않는다 — `persist` 를 붙이지 말 것.** 아이 사진 원본이라 디스크에
 *    남으면 안 된다 (최상위 CLAUDE.md §2 개인정보 · `stores/draft.ts` 와 같은 규칙).
 *    애초에 `File` 은 직렬화도 안 되지만, 규칙이 먼저다.
 *
 * 🚨 **미리보기 URL 을 만들고 해제하는 자리가 여기 하나다.** 둘 다 부수 효과라 렌더 중에
 *    부르면 안 되고, **effect 의 cleanup 에 걸어도 안 된다** — StrictMode 가 mount 직후
 *    cleanup 을 한 번 돌리기 때문에 방금 넘겨받은 사진의 URL 이 그 자리에서 해제되고
 *    미리보기가 깨진다 (실제로 `ERR_FILE_NOT_FOUND` 로 났다). 해제는 **다음 사진을 고르는
 *    순간**과 **로그아웃**, 두 이벤트에만 건다 — 그래서 살아 있는 URL 은 언제나 최대 한 개다.
 *
 * 🚨 **한 번 쓰면 뗀다** (`release`). 남겨 두면 08 을 다시 열 때 **지난번 사진이 저절로
 *    올라간다** — 부모가 고른 적 없는 사진이 분석으로 들어가는 경로다.
 *    🚨 `release` 는 URL 을 **해제하지 않는다.** 그 시점부터 사진을 그리는 것은 화면이다.
 *
 * 🚨 **childId 를 함께 들고 확인한다.** 아이를 바꾼 뒤 08 에 들어오면 다른 아이의 사진이
 *    그 아이 기록으로 올라갈 수 있다. 아이 스코프의 정본은 URL 이고(apps/web/CLAUDE.md §3),
 *    이 값은 그 URL 과 맞을 때만 쓴다.
 */
export interface PendingPhoto {
  file: File;
  /**
   * 🚨 **부모가 시트에서 고른 값**이다 (서버 추측이 아니다). 사진과 함께 다녀야 하는 이유는,
   * 08 이 업로드에 이 값을 싣고 확인 화면의 정본으로도 쓰기 때문이다 — 파일만 넘기면
   * 부모가 고른 것을 08 이 다시 묻게 된다.
   */
  lane: PhotoLane;
  /** `URL.createObjectURL` 결과. 🚨 해제는 이 스토어만 한다 (다음 사진 · 로그아웃). */
  url: string;
}

interface PhotoDraftState {
  /** 넘겨줄 사진. 받는 화면이 `release` 하면 비고, **URL 은 그때 살아 있다.** */
  pending: (PendingPhoto & { childId: string }) | null;
  /**
   * 지금 살아 있는 objectURL 하나. `pending` 과 따로 두는 이유는 화면이 `release` 한 뒤에도
   * 그 URL 로 사진을 그리고 있어서다 — 해제 책임만 여기 남는다.
   */
  live: string | null;

  /**
   * 🚨 **이벤트 핸들러에서만 부른다.** 08 로 넘기지 않고 **그 자리에서 쓸** 사진을 만든다
   * (08 화면 자신의 시트가 쓴다). 앞의 URL 은 여기서 해제한다.
   */
  adoptPhoto: (file: File, lane: PhotoLane) => PendingPhoto;
  /** 🚨 **이벤트 핸들러에서만 부른다.** `adoptPhoto` + 08 로 넘길 자리에 놓기. */
  putPhoto: (childId: string, file: File, lane: PhotoLane) => void;
  /** 순수한 읽기. 렌더 중에 불러도 안전하다 — 아무것도 바꾸지 않는다. */
  peek: (childId: string) => PendingPhoto | null;
  /** 화면이 가져갔다. 참조만 놓고 **URL 은 해제하지 않는다.** */
  release: () => void;
  /** 로그아웃에서 부른다 — 다음 사람에게 남의 아이 사진을 넘기지 않는다. 여기서는 해제한다. */
  clearAll: () => void;
}

export const usePhotoDraftStore = create<PhotoDraftState>()((set, get) => ({
  pending: null,
  live: null,

  adoptPhoto: (file, lane) => {
    // 앞 사진은 이 순간 화면에서 내려간다. 살아 있는 URL 은 언제나 한 개다.
    const previous = get().live;
    if (previous) URL.revokeObjectURL(previous);

    const url = URL.createObjectURL(file);
    set({ live: url });
    return { file, lane, url };
  },

  putPhoto: (childId, file, lane) => {
    const adopted = get().adoptPhoto(file, lane);
    set({ pending: { childId, ...adopted } });
  },

  peek: (childId) => {
    const pending = get().pending;
    if (!pending || pending.childId !== childId) return null;
    return { file: pending.file, lane: pending.lane, url: pending.url };
  },

  release: () => set({ pending: null }),

  clearAll: () => {
    const live = get().live;
    if (live) URL.revokeObjectURL(live);
    set({ pending: null, live: null });
  },
}));
