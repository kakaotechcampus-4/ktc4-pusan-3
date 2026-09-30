import type { Metadata, Viewport } from "next";

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

export default function RootLayout({ children }: LayoutProps<"/">) {
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
