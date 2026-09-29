/**
 * 렌더 중 터진 예외를 개발자에게 남긴다.
 *
 * 🚨 **`error.message` 를 남기지 않는다.** 이 경로에는 서버 에러 봉투의 문구와
 *    보호자가 방금 친 한 줄(`raw_text`)이 실릴 수 있다 — 최상위 CLAUDE.md §2 는
 *    "로그에 원문 대신 `memory_id`" 라고 못박았고, 로그인 콜백이 "받은 코드를 화면에
 *    그대로 출력하지 않는다" 로 지키는 것과 같은 규칙이다.
 *    남기는 것은 **생성자 이름과 `digest`** 뿐이다.
 *
 * `digest` 는 서버에서 난 예외에만 붙는다 (Next 가 메시지를 해시로 바꿔 클라이언트에
 * 내려 준다). 클라이언트 렌더에서 터지면 없는 것이 정상이다.
 *
 * ⚠️ 인자가 `unknown` 인 것은 실수가 아니다 — JS 는 `Error` 가 아닌 것도 던질 수 있고
 *    (`throw "..."`), 바운더리는 그것도 그대로 넘겨 준다. 꺼내 쓰기 전에 좁힌다.
 */
export function reportRenderError(error: unknown): void {
  const thrown = error as { name?: unknown; digest?: unknown } | null | undefined;

  console.error("[render-error]", {
    name: typeof thrown?.name === "string" ? thrown.name : typeof error,
    digest: typeof thrown?.digest === "string" ? thrown.digest : null,
  });
}
