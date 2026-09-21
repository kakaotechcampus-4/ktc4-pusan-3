import type { RecentPhoto, RecentPhotosBridge } from "@/lib/native/recent-photos";

/**
 * 네이티브 셸 흉내 — **개발 환경 전용**. 08 사진 시트의 "최근 사진" 줄을 화면에서 볼 수 있게 한다.
 *
 * 🚨 **이건 MSW 가 아니다.** 최근 사진은 API 가 아니라 `apps/mobile` 의 Expo 셸이
 *    `expo-media-library` 로 읽어 WebView 에 꽂아 주는 값이라(`lib/native/recent-photos.ts`),
 *    서비스 워커가 가로챌 대상이 없다. 그래도 **켜고 끄는 스위치는 목과 같은 것**을 쓴다 —
 *    "개발 환경에서 화면을 보려고 채워 넣는 가짜" 라는 성격이 같고, 스위치가 둘이면
 *    "왜 안 보이지" 를 두 군데서 찾게 된다.
 *
 * 🚨 **진짜 사진을 픽스처로 넣지 않는다** (저장소가 public · 최상위 CLAUDE.md §9).
 *    썸네일은 실행 시점에 SVG 로 그린다 — 커밋되는 이미지 파일이 없다.
 *
 * 🚨 **셸이 붙으면 이 파일은 아무 일도 하지 않는다.** 이미 꽂혀 있으면 덮어쓰지 않는다 —
 *    개발 빌드를 셸 안에서 열었을 때 진짜 갤러리를 가짜가 가리면 안 된다.
 */

/** 실제 사진이 아니라는 것이 한눈에 보여야 한다. 색만 다른 네모에 번호를 적는다. */
function placeholderThumbnail(index: number): string {
  // 팔레트 밖의 임의 색을 쓰지 않도록 뉴트럴 두 개 사이에서만 밝기를 움직인다.
  const tone = 214 - (index % 4) * 18;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160">
    <rect width="160" height="160" fill="rgb(${tone},${tone - 6},${tone - 14})"/>
    <text x="80" y="92" font-family="sans-serif" font-size="44" fill="rgb(110,105,97)"
          text-anchor="middle">${index + 1}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

const PHOTOS: RecentPhoto[] = Array.from({ length: 12 }, (_, i) => ({
  id: `dev_recent_${i}`,
  thumbnailUrl: placeholderThumbnail(i),
}));

const devBridge: RecentPhotosBridge = {
  async list(limit) {
    // 셸도 즉답하지 않는다. 0ms 로 두면 "불러오는 동안" 이 화면에 없는 상태가 된다.
    await new Promise((resolve) => setTimeout(resolve, 180));
    return PHOTOS.slice(0, limit);
  },

  async read(id) {
    const photo = PHOTOS.find((p) => p.id === id);
    if (!photo) return null;
    const blob = await fetch(photo.thumbnailUrl).then((r) => r.blob());
    return new File([blob], `${id}.svg`, { type: "image/svg+xml" });
  },
};

/**
 * 🚨 `process.env.NODE_ENV` 는 빌드 시점에 치환된다 — 프로덕션 빌드에서는 아래 조건이 항상
 *    거짓이라 이 파일을 부르는 쪽의 동적 import 가 통째로 떨어져 나간다 (`mocks/start.ts` 와 같다).
 */
export function startNativeBridgeStub(): void {
  if (process.env.NODE_ENV !== "development") return;
  if (typeof window === "undefined") return;
  // 진짜 셸이 이미 꽂아 뒀으면 건드리지 않는다.
  if (window.icatch?.recentPhotos) return;

  window.icatch = { ...window.icatch, recentPhotos: devBridge };
  console.info(
    "[mocks] 최근 사진 브릿지를 흉내 낸다 — 08 사진 시트의 줄이 가짜 썸네일로 채워진다.\n" +
      "        진짜 구현은 apps/mobile 의 Expo 셸이다 (lib/native/recent-photos.ts).",
  );
}
