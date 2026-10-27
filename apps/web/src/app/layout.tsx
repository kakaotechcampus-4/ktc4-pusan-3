import type { Metadata, Viewport } from "next";
import { preload } from "react-dom";

import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  // 배포 도메인. 상대경로 메타데이터(OG · canonical)가 이 주소를 기준으로 풀린다.
  metadataBase: new URL("https://icatch.ai.kr"),
  title: "아이캐치",
  description: "육아를 가장 많이 아는 AI가 아니라, 우리 아이를 가장 오래 알아온 AI.",
  // 🚨 public repo · 실서비스 전까지 검색 노출 금지.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // 웹뷰에서 입력창 포커스 시 확대되는 것을 막되, 접근성상 확대 자체는 허용한다.
  maximumScale: 5,
  viewportFit: "cover",
};

/**
 * 어느 화면이든 받는 손글씨 서브셋 (자주 쓰는 한글 · 숫자 · 라틴). 화면이 그 글자를 그릴 때가 아니라
 * HTML 을 읽자마자 받기 시작한다.
 *
 * 🚨 **서체 교체가 레이아웃을 민다** (CLS). `font-display: swap` 이라 손글씨가 늦게 붙으면 먼저 그린
 *    대체 서체의 줄바꿈이 손글씨의 줄바꿈으로 바뀌고, 그 아래가 통째로 움직인다 (11-1 머리말이 두 줄 → 한 줄).
 *    세션을 확인하는 동안(`SessionChecking`)에 받아 두면 화면이 설 때는 이미 붙어 있다.
 * 🚨 번호는 `hakgyoansim.css` 의 파티션 번호다 (`scripts/build-hakgyoansim-subset.py` 가 만든다).
 *    파티션을 다시 뽑으면 화면 몇 개를 열어 받는 파일을 보고 여기를 맞춘다 — 안 쓰는 파일을 미리 받으면 낭비고
 *    브라우저가 "미리 받았는데 안 썼다" 고 경고한다. 지금 8개 · 약 157KB 다 (화면 9개 중 7개 이상이 받는 것).
 */
const PRELOADED_FONT_SUBSETS = [82, 85, 86, 87, 88, 89, 90, 91];

export default function RootLayout({ children }: LayoutProps<"/">) {
  for (const subset of PRELOADED_FONT_SUBSETS) {
    // 🚨 `crossOrigin` 을 빼지 않는다 — 폰트는 같은 출처여도 CORS 모드로 받아서, 빠지면 미리 받은 것을 못 쓰고 두 번 받는다.
    preload(`/fonts/hakgyoansim/HakgyoansimNalgaeR.subset.${subset}.woff2`, {
      as: "font",
      type: "font/woff2",
      crossOrigin: "anonymous",
    });
  }

  return (
    /*
     * 🚨 `suppressHydrationWarning` 은 이 태그 **하나**에만 걸린다 (한 겹 깊이).
     *
     * 웹뷰 셸이 페이지가 뜨기 전에 `<html>` 인라인 스타일로 safe area 크기를 꽂는다
     * (`--shell-inset-top`/`--shell-inset-bottom` — apps/mobile/src/native/safe-area.ts).
     * 서버가 그려 보낸 HTML 에는 그 속성이 없으니 하이드레이션 때 **속성이 다르다**고 React 가 경고하고,
     * 웹뷰로 열 때마다 개발 오버레이에 "1 Issue" 가 떠서 **진짜 문제가 그 뒤에 묻힌다.**
     *
     * 값을 서버가 알 방법이 없어서(상태바 높이는 기기가 안다) 경고를 없애려면 이 태그에서 꺼야 한다.
     * 🚨 범위를 좁게 두려고 `<html>` 에만 건다 — 여기 있는 다른 속성은 `lang` 하나뿐이다.
     *    `<body>` 나 그 아래로 내리면 화면 코드의 진짜 불일치까지 같이 가려진다.
     */
    <html lang="ko" suppressHydrationWarning>
      <body className="antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
