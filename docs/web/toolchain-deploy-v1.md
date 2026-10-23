# 툴체인 · CI · 배포 v1

문서 목적: 프론트 스택 버전을 그 값으로 둔 이유, 명령어에서 안 보이는 것, CI 와 배포 이미지가 지켜야 하는 규칙을 고정한다.

기준 브랜치: `docs/fe-406-split-web-claude-md`
작성일: 2026-10-14
담당: 고태영
선행 문서: [`apps/web/CLAUDE.md`](../../apps/web/CLAUDE.md) (작업마다 읽는 요약. 이 문서는 해당 코드를 만질 때 읽는 원문이다)

> `apps/web/CLAUDE.md` 에서 옮겨 온 본문이다 (#406). 문장은 고치지 않고 자리만 옮겼다.
> 본문이 원문 그대로라, 앞에 아무것도 안 붙은 `§N` 은 맥락에 따라 `apps/web/CLAUDE.md` · 최상위 `CLAUDE.md` · 디자인 시스템 중 한 곳의 절이다.
> "위" · "아래" 도 원문(`apps/web/CLAUDE.md`)에서의 위치 기준이다.

---

## 1. 스택 · 버전

| | 버전 | 비고 |
| --- | --- | --- |
| Next.js | 16.3.4 | App Router · Turbopack. `next lint` 는 16 에서 빠졌다 — lint 는 별도 스크립트 |
| React | 19.2.8 | |
| TypeScript | **6.0.3** | ⚠️ 아래 참고 |
| Tailwind CSS | 4.3.3 | CSS-first. `tailwind.config.*` 없음 — 토큰은 `globals.css` 의 `@theme` |
| Zustand | 5.0.15 | 클라이언트 상태 전용 |
| TanStack Query | 5.102.8 | 서버 상태 전용 |
| Zod | 4.5.4 | 환경변수 검증 · (필요해지면) 응답 파싱 |
| Recharts | 3.10.1 | 11-1 변화 그래프 **하나**에만 쓴다 — 아래 §3 컴포넌트 |
| ESLint | 10.10.0 | `eslint-config-next` flat config |
| Prettier | 3.9.6 | `prettier-plugin-tailwindcss` 로 클래스 정렬 |

### ⚠️ TypeScript 는 왜 7 이 아닌가

TS 7 (네이티브 컴파일러) 이 최신이지만 **`typescript-eslint` 가 아직 지원하지 않는다**
(`typescript-eslint does not support TS 7.0` 로 lint 가 죽는다 · [typescript-eslint#10940](https://github.com/typescript-eslint/typescript-eslint/issues/10940)).
그래서 툴체인 전체가 도는 최신 버전인 **6.0.3** 에 맞춰 뒀다. 위 이슈가 닫히면 그때 7 로 올린다.

`eslint.config.mjs` 의 `settings.react.version` 도 같은 종류의 임시 조치다 —
`eslint-plugin-react` 의 자동 버전 탐지가 ESLint 10 API 와 안 맞아 터진다. **React 버전을 올리면 이 값도 같이 올릴 것.**

## 2. 명령어

스크립트 목록은 `package.json` 에 있다. 거기서 안 보이는 것만 적는다.

`pnpm typecheck` 가 `next typegen` 을 먼저 도는 이유: `LayoutProps` · `PageProps` 같은 전역 타입은 Next 가 `.next/types` 에 생성한다. 빌드/타입젠 전에는 `tsc` 가 그 타입을 못 찾는다.

**최초 세팅** — `cp .env.example .env.local`. `NEXT_PUBLIC_API_BASE_URL` 이 없으면 앱이 뜨지 않고 바로 에러를 던진다 (조용히 잘못된 주소로 붙는 것보다 낫다).

🚨 `NEXT_PUBLIC_*` 는 **브라우저 번들에 그대로 박힌다.** 비밀은 여기 넣지 않는다 (§9 · NF-09).

루트에서 `make` 를 치면 `web-*` 로 같은 스크립트를 부를 수 있다 (6명이 파트를 오갈 때 한 줄로 끝내기 위한 것이다).
`make web-check` = format(검사만) + typecheck + lint + test.

🚨 **`make web-build` 를 PR 전에 한 번 돈다.** `pnpm dev` 가 통과하는데 프로덕션 빌드만 멈추는 자리가 있다
(§3 라우팅의 `useSearchParams` Suspense 경계). dev 서버만 보고 올리면 그 화면은 배포에서만 죽는다.

## 3. CI — PR 마다 같은 검사를 다시 돈다

[`.github/workflows/ci-web.yml`](../../.github/workflows/ci-web.yml) 이 `apps/web/**` 를 바꾼 PR(→ `develop` · `main`)과
`develop` push 에서 `format:check → lint → typecheck → test → build` 를 돈다. `make web-check` + `make web-build` 와 같다.
같은 워크플로의 `e2e` job 이 화면 규칙 테스트(`make web-e2e`)를 따로 돈다 — 목은 개발 서버에서만 떠서 build 결과를 쓸 수 없다 (§8).
셸은 [`ci-mobile.yml`](../../.github/workflows/ci-mobile.yml) 이 `typecheck` · `test` 를 돈다.
[`ci-contract.yml`](../../.github/workflows/ci-contract.yml) 은 웹 API 타입을 서버 OpenAPI 와 맞춘다 (`make web-contract` · #559).
`apps/web/src/lib/api/**` 뿐 아니라 **서버 스키마(`apps/api/app/**`)를 바꾼 PR 에서도** 돈다 — 규칙은 [`mock-and-tests-v1.md`](mock-and-tests-v1.md) §2.

- 🚨 **pnpm · Node 버전을 워크플로에 적지 않는다.** pnpm 은 `package.json` 의 `packageManager` 를 읽고,
  Node 는 `Dockerfile` 의 메이저(24)와 같다 — 베이스 이미지를 올리면 워크플로의 `node-version` 도 같이 올린다
  - `ci-contract.yml` 의 uv(0.13.0) · Python(3.12)은 `apps/api/Dockerfile` 과 같다. 그쪽을 올리면 같이 올린다
  - `ci-contract.yml` 이 서버 계약을 뽑을 때 쓰는 설정값(`DB_*` · `AUTH_RETURN_URL_*`)도 더미다. 설정 검사만 지나가면 되고 DB 에는 붙지 않는다
- `build` 단계의 `NEXT_PUBLIC_API_BASE_URL` 은 더미다. `lib/env.ts` 가 값이 없으면 import 시점에 던져서
  넣는 것이고, 빌드는 그 주소로 요청하지 않는다
- 포맷이 어긋나 빨간불이면 `make web-fmt` 로 고쳐서 **별도 커밋**으로 올린다 (로직 변경과 섞지 않는다 · CONTRIBUTING §3)

## 4. 배포 — 이미지는 API 주소를 안고 굳는다

[`Dockerfile`](../../apps/web/Dockerfile) 3단계(deps → builder → runner) · [`deploy/docker/docker-compose.deploy.yml`](../../deploy/docker/docker-compose.deploy.yml) ·
`make web-image` / `web-up` / `web-down` / `web-logs`.

- 🚨 **`NEXT_PUBLIC_API_BASE_URL` 은 런타임 환경변수가 아니라 `--build-arg` 다.** 위 줄이 이유 전부다 —
  빌드가 문자열로 박아 버리므로 컨테이너에 환경변수를 새로 꽂아도 바뀌지 않는다. 주소가 바뀌면
  **다시 굽는다.** 값의 정본은 `deploy/docker/.env` 고, 비어 있으면 빌드가 그 자리에서 멈춘다
  (조용히 잘못된 주소로 굳는 것보다 낫다 — `.env.local` 규칙과 같은 이유)
- 🚨 **그 주소의 오리진은 서버의 `KAKAO_CALLBACK_URL` 오리진과 같아야 한다.** 다르면 `state` 쿠키가
  콜백에 실리지 않는다 ([`docs/api/auth-kakao-v1.md`](../../docs/api/auth-kakao-v1.md) §5-5) —
  **로컬은 포트가 달라도 쿠키가 공유돼 우연히 통과한다.** 이 값은 배포 기준으로 확인한다
- 🚨 **목 서버는 이 이미지로 켤 수 없고, 켤 수 있는 척하지도 않는다.** `NEXT_PUBLIC_API_MOCKING` 을
  빌드 인자로 열지 않고 `disabled` 로 고정해 뒀다 — 프로덕션 빌드에는 msw 가 통째로 빠져서(§7)
  인자만 열어 두면 "배포에서 목이 안 뜬다" 를 디버깅하게 된다
- 🚨 **`.dockerignore` 의 `.env*` 를 지우지 않는다.** 저장소엔 없지만 각자 로컬엔 `.env.local` 이 있고,
  빌드 컨텍스트에 들어가면 Next 가 그것을 읽어 **개인 설정이 배포 이미지에 굳는다** (최상위 §9)
- `output: "standalone"` 을 끄지 않는다 (`next.config.ts`). 끄면 `.next/standalone` 이 안 생겨서
  Dockerfile 의 마지막 COPY 가 실패한다. 🚨 standalone 은 `public/` 과 `.next/static/` 을 **자기 안에
  넣지 않는다** — Dockerfile 이 손으로 옮기고, 그 두 줄을 빼면 화면은 뜨는데 CSS 와 서브셋 폰트 92개가
  전부 404 다 (§5 서체)
