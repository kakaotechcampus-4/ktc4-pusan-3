/**
 * 04 대화의 "위치 권한이 꺼져 있어서" 안내를 하루에 한 번까지만 띄운다 (#593 · #638 리뷰).
 *
 * 위치 동의는 켰는데 휴대폰이 허용하지 않은 보호자에게 놀이 추천 아래에서 알린다. 매번 뜨면 조르는 느낌이라,
 * 아무것도 누르지 않으면 **하루(한국 날짜)에 한 번**만 띄운다. 띄우기로 정한 줄은 그 뒤로도 계속 보인다 —
 * 같은 줄에서 깜빡이지 않게.
 *
 * 🚨 **저장소에 두는 것은 날짜 하나다** (`localStorage`). 좌표도, 권한 상태도, 어느 아이인지도 두지 않는다.
 *    저장소를 못 쓰는 환경(사생활 보호 모드 등)이면 이 탭이 살아 있는 동안만 기억한다.
 */

const DAY_KEY = "icatch.location-nudge.day";

/** 띄우기로 정한 줄. 다시 그려져도 같은 답을 준다. */
const claimed = new Set<string>();
/** 저장소를 못 쓸 때 대신 기억하는 날. */
let memoryDay: string | null = null;

/**
 * 이 줄에 안내를 띄워도 되는가. 된다고 답하면 그날은 다른 줄에 띄우지 않는다.
 * @param today 한국 날짜 `YYYY-MM-DD` (`toSeoulDateKey`)
 */
export function claimLocationNudge(turnId: string, today: string): boolean {
  if (claimed.has(turnId)) return true;
  if (shownDay() === today) return false;
  rememberDay(today);
  claimed.add(turnId);
  return true;
}

function shownDay(): string | null {
  try {
    return localStorage.getItem(DAY_KEY) ?? memoryDay;
  } catch {
    return memoryDay;
  }
}

function rememberDay(day: string): void {
  memoryDay = day;
  try {
    localStorage.setItem(DAY_KEY, day);
  } catch {
    // 저장소를 못 쓰면 이 탭에서만 기억한다 (머리말).
  }
}

/** 테스트 사이에 비운다. */
export function resetLocationNudge(): void {
  claimed.clear();
  memoryDay = null;
}
