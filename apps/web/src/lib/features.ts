/**
 * 서버에 엔드포인트가 아직 없는 기능의 진입점 (#409).
 *
 * 목 서버에는 그 엔드포인트가 있으니, **목이 실제로 뜨는 환경**(개발 + 목 켬)에서만 들어가는 버튼을
 * 그린다. 실서버를 부르는 빌드에서는 버튼을 숨긴다 — 누르면 404 로 끝나는 문을 보호자에게 보이지 않는다.
 * 화면 라우트와 목 핸들러는 그대로 둔다. 닫는 것은 들어가는 문뿐이다.
 *
 * 🚨 **환경변수로 켜는 길을 따로 두지 않는다.** 실서버에 엔드포인트가 생기면 그 항목을 지우고
 *    호출부의 조건을 걷어낸다. 스위치를 두면 서버보다 먼저 켜진 빌드가 나갈 수 있다.
 * 🚨 조건은 `mocks/start.ts` 가 목을 띄우는 조건과 같아야 한다. 프로덕션 빌드는 목 스위치가 켜져
 *    있어도 목이 뜨지 않으므로 여기서도 꺼진다.
 */
const MOCK_SERVER =
  process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_API_MOCKING === "enabled";

export const PENDING_SERVER_FEATURES = {
  /** 08 사진으로 적기 — `POST /children/{cid}/photos`. 홈 · 04 대화의 진입점. */
  photoEntry: MOCK_SERVER,
  /** 11-2 알레르기 검사지에서 가져오기 — `POST /children/{cid}/health-safety/scan`. 11 프로필 안전 정보 추가 시트의 진입점. */
  safetyScan: MOCK_SERVER,
} as const;
