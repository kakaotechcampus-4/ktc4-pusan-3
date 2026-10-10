import { authHeaders, buildUrl } from "@/lib/api/client";

/**
 * 렌더 중 터진 예외를 개발자에게 남긴다 — 콘솔에, 그리고 로그인했으면 서버에도
 * (`POST /client-errors`, 멘토 #267 2번 · #166).
 *
 * 서버로 보내는 이유 — 보호자 폰에서 난 화면 오류는 그 폰의 콘솔에만 남아서 아무도 몰랐다.
 * 서버가 받으면 ERROR 로 찍고 Discord 알림이 그대로 간다 (apps/api `app/core/alerts.py`).
 *
 * 🚨 **`error.message` 를 남기지 않는다 — 콘솔에도, 서버에도.** 이 경로에는 서버 에러 봉투의
 *    문구와 보호자가 방금 친 한 줄(`raw_text`)이 실릴 수 있다 — 최상위 CLAUDE.md §2 는
 *    "로그에 원문 대신 `memory_id`" 라고 못박았고, 로그인 콜백이 "받은 코드를 화면에
 *    그대로 출력하지 않는다" 로 지키는 것과 같은 규칙이다.
 *    남기는 것은 **생성자 이름 · `digest` · 화면 경로 · 기기 요약** 뿐이다. 서버도 그 모양만
 *    받는다 (`app/api/v1/schemas/client_errors.py`) — 양쪽이 같아야 한다.
 *
 * `digest` 는 서버에서 난 예외에만 붙는다 (Next 가 메시지를 해시로 바꿔 클라이언트에
 * 내려 준다). 클라이언트 렌더에서 터지면 없는 것이 정상이다.
 *
 * 🚨 로그인하지 않았으면 보내지 않는다 — 서버가 로그인한 보호자만 받는다. 아무나 그 주소로
 *    팀 채널을 울릴 수 없게 하려는 것이고, 그래서 로그인 · 동의 화면의 오류는 콘솔에만 남는다.
 * 🚨 보내기가 실패해도 조용하다 — 오류 화면에서 또 터지면 안 된다.
 *
 * ⚠️ 인자가 `unknown` 인 것은 실수가 아니다 — JS 는 `Error` 가 아닌 것도 던질 수 있고
 *    (`throw "..."`), 바운더리는 그것도 그대로 넘겨 준다. 꺼내 쓰기 전에 좁힌다.
 */

/** 서버로 보내는 모양. apps/api `ClientErrorReport` 와 같다. */
export interface ClientErrorReport {
  name: string;
  digest: string | null;
  path: string;
  platform: string;
}

/** 화면이 지금 어디서 돌고 있나. 테스트는 이걸 직접 넘기고, 화면은 `window` 에서 읽는다. */
export interface ClientEnvironment {
  pathname: string;
  userAgent: string;
  /** 앱 껍데기(웹뷰) 안인가 — 셸이 `window.icatch` 를 꽂아 둔다 (`lib/native/recent-photos.ts`). */
  isApp: boolean;
}

const OS_PATTERNS: ReadonlyArray<readonly [string, RegExp]> = [
  ["android", /Android (\d+)/],
  ["ios", /(?:iPhone|iPad|iPod).*? OS (\d+)_/],
  ["mac", /Mac OS X (\d+)[_.]/],
  ["windows", /Windows NT (\d+)/],
  ["linux", /Linux/],
];

/**
 * 브라우저의 긴 식별 문자열(User-Agent)에서 운영체제 · 주요 버전 · 앱/브라우저만 — "android 14 app".
 *
 * 전체를 보내지 않는 이유 — 기종 · 빌드 번호까지 들어 있어 사람을 좁히는 힘이 있고, 처리방침에
 * 적기도 애매해진다 (⑥ "자동으로 남는 정보"). "어떤 폰에서만 깨진다" 를 가르는 데는 이걸로 충분하다.
 * 결과는 서버가 받는 모양(소문자 · 숫자 · 공백, 40자 이내)을 벗어나지 않는다.
 */
export function platformSummary(userAgent: string, isApp: boolean): string {
  const kind = isApp ? "app" : "browser";
  for (const [os, pattern] of OS_PATTERNS) {
    const found = pattern.exec(userAgent);
    if (found) return found[1] ? `${os} ${found[1]} ${kind}` : `${os} ${kind}`;
  }
  return `other ${kind}`;
}

function browserEnvironment(): ClientEnvironment | null {
  if (typeof window === "undefined" || typeof navigator === "undefined") return null;
  return {
    pathname: window.location.pathname,
    userAgent: navigator.userAgent,
    isApp: Boolean((window as { icatch?: unknown }).icatch),
  };
}

/** 예외에서 꺼내도 되는 두 가지 — 생성자 이름과 digest. 메시지는 꺼내지 않는다. */
export function describeRenderError(error: unknown): { name: string; digest: string | null } {
  const thrown = error as { name?: unknown; digest?: unknown } | null | undefined;
  return {
    name: typeof thrown?.name === "string" ? thrown.name : typeof error,
    digest: typeof thrown?.digest === "string" ? thrown.digest : null,
  };
}

/** 서버가 받는 글자만 남긴다 — 경로에 이상한 글자가 있어도 보고가 400 으로 버려지지 않게. */
function safePath(pathname: string): string {
  const cleaned = pathname.replace(/[^A-Za-z0-9/_.-]/g, "_").slice(0, 200);
  return cleaned.startsWith("/") ? cleaned : `/${cleaned}`;
}

/**
 * 로그인했으면 서버로 한 건 보낸다. 실패는 조용히 — 호출한 쪽이 기다리거나 잡을 것이 없다.
 *
 * 🚨 `api.post` 가 아니라 `fetch` 를 직접 부른다 — CLAUDE.md "API 호출" 의 유일한 예외다.
 *    ① 오류 화면에서 보내는 보고라 페이지를 떠나도 끝까지 가야 해서 `keepalive` 가 필요한데,
 *    `api.post` 는 그 옵션을 넘길 자리가 없다. ② 토큰이 만료돼 401 이 오면 `api.post` 는 401 처리기를
 *    타서 오류 화면을 로그인 화면으로 튕긴다 — 보고 하나 때문에 보호자가 보던 화면이 바뀌면 안 된다.
 *    토큰 · 주소는 같은 클라이언트의 `authHeaders` · `buildUrl` 을 쓴다.
 */
export async function sendClientError(report: ClientErrorReport): Promise<void> {
  if (!("Authorization" in authHeaders())) return;
  try {
    await fetch(buildUrl("/client-errors"), {
      method: "POST",
      keepalive: true,
      headers: authHeaders({ "Content-Type": "application/json; charset=utf-8" }),
      body: JSON.stringify(report),
    });
  } catch {
    // 보고를 못 보낸 것까지 알릴 길은 없다. 오류 화면에서 또 터지는 것보다 조용한 쪽이 낫다.
  }
}

export function reportRenderError(
  error: unknown,
  env: ClientEnvironment | null = browserEnvironment(),
): void {
  const described = describeRenderError(error);
  console.error("[render-error]", described);
  if (!env) return;
  void sendClientError({
    ...described,
    path: safePath(env.pathname),
    platform: platformSummary(env.userAgent, env.isApp),
  });
}
