/**
 * 초대 코드 — 링크 대신 **손으로 옮겨 적는 8자**를 쓴다.
 *
 * ⚠️ **계약서 §05 는 아직 `invite_url` 이다** (#96 에서 제안 중 · 확정 전). 서버가 링크를
 *    유지하기로 하면 이 파일과 `InviteResponse` · 목을 함께 되돌린다.
 *
 * 왜 링크가 아닌가 — 링크는 **어느 브라우저에서 열리는지 통제할 수 없다.** 카톡에서 누르면
 * 인앱 브라우저가 열리고, 거기서 카카오 로그인이 카카오톡 앱으로 튀었다 돌아오면 같은 탭
 * 세션이라는 보장이 없다. `sessionStorage` 가 날아가면 초대 토큰만이 아니라 **`bind` 까지
 * 날아가서 로그인 자체가 깨진다** (`lib/auth/oauth-bind.ts`). 코드는 로그인을 먼저 끝내고
 * 입력하므로 카카오 왕복에 실어 보낼 상태가 하나도 없다. 앱 딥링크 설정도 필요 없다.
 *
 * 🚨 **짧은 코드는 추측이 된다.** 8자 = 40비트라, 서버의 시도 제한(`429 too_many_attempts`)이
 *    이 선택의 전제다. 화면에서 길이를 줄이지 말 것.
 */

/** Crockford Base32 — 사람이 옮겨 적다 헷갈리는 `I` `L` `O` `U` 가 빠져 있다. */
const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

export const INVITE_CODE_LENGTH = 8;

/**
 * 입력을 서버가 보는 모양으로 되돌린다.
 *
 * 🚨 **비교·전송은 항상 이 값으로 한다.** 화면이 보여주는 `ABCD-1234` 는 읽기 편하라고 끊어
 *    놓은 것이지 코드가 아니다. 하이픈이 섞인 채로 보내면 서버에서 없는 코드가 된다.
 *
 * 혼동 문자는 **버리지 않고 옮긴다** (Crockford 해독 규칙) — `O`→`0`, `I`·`L`→`1`.
 * 받아 적은 사람이 0 을 O 로 썼다고 해서 틀렸다고 말하지 않는다.
 */
export function normalizeInviteCode(input: string): string {
  return input
    .toUpperCase()
    .replace(/[OIL]/g, (c) => (c === "O" ? "0" : "1"))
    .split("")
    .filter((c) => ALPHABET.includes(c))
    .join("")
    .slice(0, INVITE_CODE_LENGTH);
}

/** 보낼 수 있는 모양인가. 형식 검사일 뿐 **유효한 코드인지는 서버만 안다.** */
export function isInviteCodeComplete(code: string): boolean {
  return code.length === INVITE_CODE_LENGTH;
}

/** 화면 표시용. 네 자씩 끊어야 눈으로 옮겨 적을 수 있다. */
export function formatInviteCode(code: string): string {
  const normalized = normalizeInviteCode(code);
  if (normalized.length <= 4) return normalized;
  return `${normalized.slice(0, 4)}-${normalized.slice(4)}`;
}
