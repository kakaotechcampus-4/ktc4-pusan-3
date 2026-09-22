# apps/web 배포 이미지. 빌드 컨텍스트는 이 폴더다 (deploy/docker/docker-compose.deploy.yml).
#
#   make web-image        굽기
#   make web-up / web-down / web-logs
#
# 🚨 이 파일의 전제 두 개
#   ① `NEXT_PUBLIC_*` 는 런타임 환경변수가 아니다 — `next build` 가 문자열로 박아 넣는다
#      (src/lib/env.ts 주석). 그래서 API 주소는 ENV 가 아니라 --build-arg 로 받고,
#      주소가 바뀌면 컨테이너를 재시작하는 게 아니라 **이미지를 다시 굽는다.**
#   ② `output: "standalone"` 이 켜져 있어야 한다 (next.config.ts). 없으면 .next/standalone 이
#      안 만들어져서 마지막 스테이지의 COPY 가 실패한다.
#
# 개발은 이 이미지로 하지 않는다 — `make web-dev` 가 맞다 (Next 공식 권고: 웹뷰 개발에서
# 컨테이너 파일시스템은 느리다). 이건 배포용이다.

# ── ① deps — 의존성만. package.json 이 안 바뀌면 이 레이어가 캐시로 산다 ──────────
FROM node:24.21-alpine AS deps
WORKDIR /app

# Next 의 SWC 바이너리가 alpine(musl) 에서 glibc 심볼을 찾는 경우가 있다.
RUN apk add --no-cache libc6-compat

# pnpm 버전의 정본은 package.json 의 `packageManager` 하나다 — corepack 이 그 값을 읽어 온다.
# 여기에 버전을 또 쓰면 두 곳이 어긋난다.
# ⚠️ corepack 은 Node 가 deprecate 한 기능이다. 베이스 이미지를 올릴 때 `corepack enable` 이
#    아직 있는지 확인할 것 — 없어졌으면 `npm i -g pnpm@<packageManager 값>` 으로 바꾼다.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

# 🚨 pnpm-workspace.yaml 을 함께 넣는다. 빌드 스크립트 허용 목록(allowBuilds)이 거기 있어서,
#    빼면 msw 의 postinstall 이 돌아 커밋된 public/mockServiceWorker.js 를 덮어쓴다.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# ── ② builder — 소스 + 빌드 ───────────────────────────────────────────────────────
FROM node:24.21-alpine AS builder
WORKDIR /app
RUN apk add --no-cache libc6-compat
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# FastAPI 주소. 🚨 카카오 로그인은 이 값의 오리진과 서버의 KAKAO_CALLBACK_URL 오리진이
# 같아야 한다 — 다르면 state 쿠키가 콜백에 실리지 않는다 (docs/api/auth-kakao-v1.md §5-5).
# 로컬은 포트가 달라도 쿠키가 공유돼 우연히 통과하니 배포 기준으로 확인할 것.
ARG NEXT_PUBLIC_API_BASE_URL

# 비어 있으면 어차피 lib/env.ts 가 빌드 중에 터진다. zod 스택 대신 읽을 수 있는 말로 먼저 막는다.
RUN [ -n "$NEXT_PUBLIC_API_BASE_URL" ] || { \
      echo ""; \
      echo "NEXT_PUBLIC_API_BASE_URL 이 비어 있다."; \
      echo "deploy/docker/.env 에 값을 넣고 make web-image 로 굽는다."; \
      echo ""; exit 1; }

ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

# 🚨 목 서버는 ARG 로 열지 않는다. NODE_ENV=production 빌드에는 msw 가 통째로 빠지고
#    (apps/web/CLAUDE.md §7) 이 이미지로는 켤 방법이 없다 — 켤 수 있는 척하면
#    "배포에서 목이 안 뜬다" 를 디버깅하게 된다.
ENV NEXT_PUBLIC_API_MOCKING=disabled
ENV NEXT_TELEMETRY_DISABLED=1

RUN pnpm build

# ── ③ runner — 실행에 필요한 것만 ─────────────────────────────────────────────────
FROM node:24.21-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# standalone 의 server.js 가 읽는 두 값. HOSTNAME 을 안 주면 루프백에만 붙어서
# 컨테이너 밖에서 포트 매핑이 닿지 않는다.
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# 긴 옵션(--system) 을 쓰지 않는다 — alpine 은 busybox adduser 라 빌드에 따라 없다.
RUN addgroup -g 1001 -S nodejs \
 && adduser -u 1001 -S -G nodejs nextjs

# standalone 은 server.js + 추려낸 node_modules 만 담는다. pnpm install 이 필요 없다.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# 🚨 아래 두 줄이 standalone 이 스스로 넣지 않는 것이다 (Next 공식: CDN 전제).
#    빼면 화면은 뜨는데 CSS 와 서브셋 폰트가 전부 404 로 깨진다.
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
