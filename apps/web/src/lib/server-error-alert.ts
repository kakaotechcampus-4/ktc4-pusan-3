/**
 * Next 서버에서 난 오류를 Discord 로 보낸다 (멘토 #267 2번 · #166). `src/instrumentation.ts` 의
 * `onRequestError` 가 부른다 — 서버 렌더 · 라우트 핸들러 · 프록시에서 던져진 예외가 한 곳으로 온다.
 *
 * 왜 웹훅으로 직접 보내나 — Next 서버에는 보호자의 로그인 토큰이 없어서(토큰은 브라우저가 들고
 * 있다) api 의 `POST /client-errors` 를 못 부른다. 서버 코드라 웹훅 URL 을 환경변수로 가질 수 있고,
 * `NEXT_PUBLIC_` 이 아니니 브라우저 번들에는 들어가지 않는다. 채널 하나에 `web-alert` 이름으로 뜬다
 * (api 는 api-alert, 브라우저 화면 오류는 browser-alert, 컨테이너 감시는 infra-alert).
 *
 * 🚨 보내는 것은 예외의 생성자 이름 · digest · 요청 경로(쿼리 뗀 것) · 라우트뿐이다. 예외 메시지는
 *    보내지 않는다 — 서버 에러 봉투의 문구나 보호자의 입력이 섞일 수 있다 (루트 CLAUDE.md §2).
 * 🚨 분당 상한 — 한 페이지가 매 요청 터지면 Discord 를 도배하고 정작 봐야 할 알림이 묻힌다.
 * 🚨 보내기가 실패해도 조용하다 — 오류를 처리하는 자리에서 또 터지면 안 된다.
 * 🚨 기한이 있다(3초). Next 16 은 라우트 핸들러 예외에서 `onRequestError` 를 **기다린 뒤** 500 을
 *    낸다 (next/dist/build/templates/app-route.js 의 catch). Discord 가 멈추면 사용자 응답도 같이 멈추므로,
 *    `instrumentation.ts` 는 기다리지 않고 띄워 보내고, 여기서도 기한을 건다 (api 쪽 flush 4초와 같은 생각).
 * 🚨 개발 모드(`next dev`)에서는 보내지 않는다 — 노트북의 오류가 팀 채널로 가지 않게. api 의
 *    "APP_ENV=local 이면 끔" 과 같은 규칙이다.
 */

/** Next 가 onRequestError 에 넘기는 모양 (next/dist/server/instrumentation/types). 겉으로 내보내지 않아 베껴 둔다. */
export interface RequestErrorContext {
  routerKind: "Pages Router" | "App Router";
  routePath: string;
  routeType: "render" | "route" | "action" | "proxy";
  renderSource?: "react-server-components" | "react-server-components-payload" | "server-rendering";
  revalidateReason: "on-demand" | "stale" | undefined;
}

export interface ErrorRequest {
  path: string;
  method: string;
  headers: Record<string, string | string[] | undefined>;
}

/** Discord 웹훅 본문. 서버 쪽 Sender(app/integrations/discord.py)와 같은 모양이다. */
export interface WebAlert {
  content: string;
  username: "web-alert";
  allowed_mentions: { parse: [] };
}

const PER_MINUTE = 10;
const WINDOW_MS = 60_000;
const TIMEOUT_MS = 3_000;
const PATH_MAX = 200;

function stamp(date: Date): string {
  // 2026-10-10 03:12:45+0900 — api 로그 · 알림과 같은 모양. 어긋나면 알림을 보고 로그를 못 찾는다.
  const pad = (n: number) => String(n).padStart(2, "0");
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  const ymd = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const hms = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return `${ymd} ${hms}${sign}${pad(Math.floor(abs / 60))}${pad(abs % 60)}`;
}

/** 쿼리 · 해시를 떼고, 이상한 글자는 _ 로. 인가 코드 · 검색어가 쿼리에 실린다. */
function safePath(path: string): string {
  const bare = path.split(/[?#]/, 1)[0] ?? "";
  return bare.replace(/[^A-Za-z0-9/_.\-[\]]/g, "_").slice(0, PATH_MAX) || "/";
}

export function buildWebAlert(
  error: unknown,
  request: ErrorRequest,
  context: RequestErrorContext,
  options: { env: string; now?: Date },
): WebAlert {
  const thrown = error as { name?: unknown; digest?: unknown } | null | undefined;
  const name = typeof thrown?.name === "string" ? thrown.name : typeof error;
  const digest = typeof thrown?.digest === "string" ? thrown.digest : "-";
  // 🟡 — 내일 봐도 되는 세기. 서버가 죽은 건 infra-alert 가 🔴 로 따로 온다.
  const first =
    `🟡 [${options.env}] ERROR web ${context.routeType} ${safePath(context.routePath)} — ` +
    `${name} digest=${digest} path=${safePath(request.path)}`;
  const nextStep = '→ journalctl CONTAINER_NAME=ktc4-web --since "10 min ago"';
  return {
    content: `${first}\n${stamp(options.now ?? new Date())}\n${nextStep}`,
    username: "web-alert",
    allowed_mentions: { parse: [] },
  };
}

export function shouldSendWebAlert(options: {
  nodeEnv: string | undefined;
  url: string | undefined;
}): boolean {
  return options.nodeEnv === "production" && Boolean(options.url);
}

/** 웹훅으로 보내는 함수를 만든다. 분당 상한은 이 함수 안의 상태다 — 서버 프로세스마다 하나. */
export function createWebAlerter(options: {
  url: string;
  perMinute?: number;
  now?: () => number;
  timeoutMs?: number;
}): (alert: WebAlert) => Promise<void> {
  const perMinute = options.perMinute ?? PER_MINUTE;
  const now = options.now ?? Date.now;
  const timeoutMs = options.timeoutMs ?? TIMEOUT_MS;
  let windowStart = now();
  let sentInWindow = 0;

  return async (alert) => {
    const at = now();
    if (at - windowStart >= WINDOW_MS) {
      windowStart = at;
      sentInWindow = 0;
    }
    if (sentInWindow >= perMinute) return;
    sentInWindow += 1;
    try {
      await fetch(`${options.url}?wait=true`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(alert),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch {
      // 알림을 못 보낸 걸 알릴 길은 없다. 요청 처리 쪽에 영향을 주지 않는 게 먼저다.
    }
  };
}
