/**
 * 가입 중인 보호자의 이름. **계정이 만들어지기 전까지만** 들고 있는 값이다.
 *
 * 왜 저장소가 필요한가 — 이름과 동의는 **화면이 다르다**. 법적 고지를 읽고 확인하는 화면에
 * 무관한 입력이 같은 제출 버튼에 묶이면 "무엇에 동의한 것인가" 가 흐려져서, 동의 화면에서
 * 보호자 이름을 받지 않기로 했다 (docs/web/kakao-login-v1.md §4-5). 그래서 앞 화면이 받은
 * 값을 뒤 화면까지 옮겨야 하고, 새로고침으로 날아가면 처음부터 다시 하게 된다.
 *
 * 🚨 **가입이 끝나거나 실패하면 지운다.** `consent_code` 와 같은 자리에서 정리한다 —
 *    계정이 생긴 뒤에는 서버가 아는 값이라 브라우저가 들고 있을 이유가 없다 (최상위 §2 개인정보).
 * 🚨 **URL 에 싣지 않는다.** 주소창·브라우저 기록에 남는다 (대기표와 같은 이유).
 */

const KEY = "icatch.signup.nickname";

export function rememberSignupNickname(nickname: string): void {
  sessionStorage.setItem(KEY, nickname);
}

/** 없으면 `null` — 호출자는 이름 화면으로 되돌린다. */
export function readSignupNickname(): string | null {
  return sessionStorage.getItem(KEY);
}

export function clearSignupNickname(): void {
  sessionStorage.removeItem(KEY);
}
