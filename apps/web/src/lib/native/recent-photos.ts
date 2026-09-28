/**
 * 최근 사진 — **네이티브 셸이 꽂아 주는 창구**의 웹 쪽 절반.
 *
 * 🚨 **웹은 기기 갤러리를 읽을 수 없다.** 파일 입력(`<input type="file">`)은 OS 피커를 열 뿐
 *    목록을 먼저 가져오는 API 가 없고, 그건 브라우저의 의도된 제약이다(사진 목록을 읽는 것은
 *    페이지가 마음대로 할 수 있는 일이 아니다). 카카오톡처럼 시트 안에 최근 사진을 늘어놓는
 *    것은 **네이티브만 할 수 있다** — [`apps/mobile`](../../../../mobile) 의 Expo 셸이
 *    `expo-media-library` 로 읽어 WebView 에 넘겨야 한다.
 *
 * 그래서 이 파일은 **약속만 정의한다.** 셸 구현은 별도 브랜치·별도 이슈다.
 *
 * 🚨 **셸이 없으면 줄 자체를 그리지 않는다** (`list()` 가 빈 배열). 빈 상자를 띄우거나
 *    "권한을 허용해 주세요" 를 브라우저에 보여주지 않는다 — 브라우저에서는 허용할 권한이
 *    아예 없다. 아무 데도 안 가는 자리를 만들지 않는다는 규칙과 같다 (00 로그인 `ready:false`).
 *
 * 🚨 **썸네일 주소도 사진이다.** 콘솔·로그에 찍지 않는다 (최상위 CLAUDE.md §2 개인정보).
 */

/** 시트에 한 칸으로 서는 사진 한 장. */
export interface RecentPhoto {
  /** 셸이 정하는 식별자. 원본을 다시 달라고 할 때 그대로 돌려준다. */
  id: string;
  /**
   * 썸네일 주소. `<img src>` 에 그대로 쓴다 — 셸은 `data:` 를 주고,
   * 🚨 웹이 `file:` 을 열 수는 없으므로 셸이 `file:` 을 주면 그림이 깨진다.
   */
  thumbnailUrl: string;
}

/**
 * 셸이 `window.icatch.recentPhotos` 에 꽂는 것.
 *
 * ⚠️ **`read()` 가 `File` 을 돌려주는 것은 웹 쪽 약속이다.** WebView 메시지로 `File` 을 그대로
 *    보낼 수는 없으니, 셸은 base64 를 넘기고 브릿지 구현이 `File` 로 만들어 준다 —
 *    그 변환이 어디서 일어나는지는 이 파일이 알 바가 아니고, 여기는 화면이 쓸 모양만 정한다.
 */
export interface RecentPhotosBridge {
  /** 최근 `limit` 장. 권한이 없거나 못 읽으면 **빈 배열**이다 (예외를 던지지 않는다). */
  list(limit: number): Promise<RecentPhoto[]>;
  /** 고른 한 장의 원본. 실패하면 `null` — 화면은 "그 사진을 못 읽었어요" 로 넘어간다. */
  read(id: string): Promise<File | null>;
}

declare global {
  interface Window {
    icatch?: { recentPhotos?: RecentPhotosBridge };
  }
}

/** 이 화면이 지금 최근 사진을 보여줄 수 있는가. 셸 안에서만 참이다. */
export function hasRecentPhotosBridge(): boolean {
  return typeof window !== "undefined" && Boolean(window.icatch?.recentPhotos);
}

/**
 * 최근 사진을 읽는다. 셸이 없으면 빈 배열이다 — 호출부는 길이만 보고 줄을 그릴지 정한다.
 * 🚨 셸이 던지는 예외를 화면까지 올리지 않는다. 사진을 못 읽는 것은 이 흐름의 실패가 아니라
 *    **그냥 줄이 없는 것**이고, 촬영·앨범이라는 길이 그대로 남아 있다.
 */
export async function listRecentPhotos(limit: number): Promise<RecentPhoto[]> {
  const bridge = typeof window === "undefined" ? undefined : window.icatch?.recentPhotos;
  if (!bridge) return [];
  try {
    return await bridge.list(limit);
  } catch {
    return [];
  }
}

/** 고른 한 장의 원본. 셸이 없거나 못 읽으면 `null`. */
export async function readRecentPhoto(id: string): Promise<File | null> {
  const bridge = typeof window === "undefined" ? undefined : window.icatch?.recentPhotos;
  if (!bridge) return null;
  try {
    return await bridge.read(id);
  } catch {
    return null;
  }
}
