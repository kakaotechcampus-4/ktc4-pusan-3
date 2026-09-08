import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // eslint-plugin-react 의 자동 버전 탐지가 ESLint 10 API 와 맞지 않아 터진다.
    // (detectReactVersion → contextOrFilename.getFilename is not a function)
    // package.json 의 react 버전과 함께 올릴 것.
    settings: { react: { version: "19.2" } },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
