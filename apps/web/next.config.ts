import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 빌드가 조용히 통과하는 것보다 CI 에서 걸리는 게 낫다. 기본값이지만 명시해 둔다.
  // (Next 16 은 next lint 를 뺐다 — eslint 키는 없다. lint 는 별도 스크립트로 돈다.)
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
