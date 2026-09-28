import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 빌드가 조용히 통과하는 것보다 CI 에서 걸리는 게 낫다. 기본값이지만 명시해 둔다.
  // (Next 16 은 next lint 를 뺐다 — eslint 키는 없다. lint 는 별도 스크립트로 돈다.)
  typescript: { ignoreBuildErrors: false },

  // 배포 이미지를 위한 설정 (Dockerfile). `.next/standalone/` 에 server.js + 필요한
  // node_modules 만 추려 담는다 — 런타임 이미지에 pnpm install 이 필요 없어진다.
  // 🚨 standalone 은 `public/` 과 `.next/static/` 을 자기 안에 넣지 않는다. Dockerfile 이
  //    손으로 옮긴다 — 안 옮기면 화면은 뜨는데 CSS 와 폰트 92개가 전부 404 다.
  output: "standalone",
};

export default nextConfig;
