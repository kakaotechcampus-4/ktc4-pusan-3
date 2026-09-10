/**
 * `bind` — 1회용 코드가 오가는 마지막 홉을 지키는 비밀.
 *
 * 🚨 이게 없으면 계정이 넘어간다. 복귀 URL 의 코드만으로 세션이 나오면, 공격자가 **자기
 *    카카오 로그인으로 얻은 코드**를 링크나 딥링크로 피해자에게 던져 **피해자를 공격자
 *    계정에 로그인**시킬 수 있다. 그 뒤 피해자가 입력하는 아이 이름·생일·건강 정보가
 *    전부 공격자 계정에 쌓인다.
 *
 * 시작할 때 브라우저가 만든 비밀을 서버가 **해시로** 보관하고, 교환할 때 같은 값을 다시
 * 제시해야 세션이 나온다. 남이 던진 코드는 해시가 애초에 안 맞는다.
 *
 * 🚨 시작 단계의 `state` 쿠키로는 이걸 못 막는다. `state` 는 카카오 왕복을 지키고 서버
 *    콜백에서 소비되는데, 막아야 하는 마지막 홉은 그 뒤다. 둘 다 필요하다.
 *
 * 정본: docs/api/auth-kakao-v1.md §7-2 · docs/web/kakao-login-v1.md §3-4 · §4-3
 */

const KEY = "yukameo.oauth.bind";

/**
 * 256비트 난수를 base64url 43자로. 서버가 길이·문자셋을 검증하므로 모양을 맞춘다
 * ("보냈다" 만 확인하면 `bind=1` 로도 통과해서 위 방어가 무의미해진다).
 */
export function createBind(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const secret = btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  sessionStorage.setItem(KEY, secret);
  return secret;
}

export function readBind(): string {
  return sessionStorage.getItem(KEY) ?? "";
}

/**
 * 🚨 교환 단계에서 지우지 않는다 — 신규 가입이 `/signup` 에서 같은 값을 한 번 더 쓴다.
 *    로그인이 끝나거나 실패한 뒤에만 부른다.
 */
export function clearBind(): void {
  sessionStorage.removeItem(KEY);
}
