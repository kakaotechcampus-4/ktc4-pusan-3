/**
 * 서버에 엔드포인트가 아직 없는 기능의 진입점 (#409).
 *
 * 목 서버에는 그 엔드포인트가 있으니, **목이 실제로 뜨는 환경**(개발 + 목 켬)에서만 들어가는 버튼을
 * 그린다. 실서버를 부르는 빌드에서는 버튼을 숨긴다 — 누르면 404 로 끝나는 문을 보호자에게 보이지 않는다.
 * 화면 라우트와 목 핸들러는 그대로 둔다. 닫는 것은 들어가는 문뿐이다.
 *
 * 🚨 **항목을 풀면 00 소개(`app/page.tsx`)에도 그 기능을 더한다.** 소개는 실서버에 있는 기능만 말해서
 *    (#704) 지금은 이 파일의 기능을 하나도 적지 않았다. 소개는 이 파일을 import 하지 않아 grep 으로 안 잡힌다.
 * 🚨 **환경변수로 켜는 길을 따로 두지 않는다.** 실서버에 엔드포인트가 생기면 그 항목을 지우고
 *    호출부의 조건을 걷어낸다. 스위치를 두면 서버보다 먼저 켜진 빌드가 나갈 수 있다.
 * 🚨 조건은 `mocks/start.ts` 가 목을 띄우는 조건과 같아야 한다. 프로덕션 빌드는 목 스위치가 켜져
 *    있어도 목이 뜨지 않으므로 여기서도 꺼진다.
 */
const MOCK_SERVER =
  process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_API_MOCKING === "enabled";

export const PENDING_SERVER_FEATURES = {
  /** 11-2 알레르기 검사지에서 가져오기 — `POST /children/{cid}/health-safety/scan`. 11 프로필 안전 정보 추가 시트의 진입점. */
  safetyScan: MOCK_SERVER,
} as const;

/**
 * 08 사진 시트의 "아이 활동 사진" (#884). 🚨 **목에서도 꺼 둔다** — 위의 `PENDING_SERVER_FEATURES` 와 다르다.
 *
 * 서버가 문서 사진만 받는다(`lane != "document"` 면 `422 lane_not_supported`). 그것만이면 위처럼 목에서만
 * 켜면 되지만, 화면에 남은 문구가 9월 설계(#60 · 사진에서 활동 태그 뽑기) 그대로라 11-03 결정과 반대다.
 * 활동 사진은 AI 로 보내지 않고 캘린더 날짜에 넣어 두는 기능으로 정했고, 처리방침 11-03 판 ⑨ 도 그렇게 적었다.
 * 목에서 켜 두면 개발 화면이 그 반대("활동 태그만 뽑아요")를 계속 말한다.
 *
 * - 숨기는 것은 시트 1단계의 줄뿐이다. `PHOTO_LANES` · 목 핸들러 · 계약 테스트의 `activity` 와
 *   `photo-review.tsx` 의 활동 화면은 그대로 둔다 — 캘린더 방식으로 다시 만들 자리다.
 * 🚨 **문구를 캘린더 방식으로 고치기 전에 켜지 않는다.** 켜면 위의 반대 문구가 그대로 나간다.
 *    켜는 PR 에서 시트 · 08 안내 · 확인 화면 문구를 같이 바꾸고, 00 소개(`app/page.tsx`)에도 더한다.
 * 🚨 **켜면 1단계가 다시 서서 e2e 둘이 깨진다.** 시트를 열자마자 "최근 사진" 을 누르는 진입
 *    (`e2e/photo-review.spec.ts` 의 `openNotice` · 버튼 개수 테스트, `e2e/no-extra-gates.spec.ts` 의 08)에
 *    "알림장·식단표" 를 누르는 단계를 되살린다. `e2e/photo-activity-hidden.spec.ts` 는 저절로 건너뛴다.
 */
export const ACTIVITY_PHOTO_ENABLED: boolean = false;
