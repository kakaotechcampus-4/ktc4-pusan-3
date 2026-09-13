import type { Metadata, Viewport } from "next";

import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "육아기억 AI",
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
    <html lang="ko">
      <body className="antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
