/**
 * 목 시나리오 스위치.
 *
 * 실서버로는 만들기 어려운 상태들을 손으로 켜고 끈다. 기억이 쌓인 뒤에야 나오거나
 * (scarcity · stale), 타이밍에 걸려야 나오거나 (partial), 모델이 죽어야 나오는 것들이라
 * (failed) 화면을 확인할 방법이 이것뿐이다.
 *
 * 쓰는 법 — 주소창에 `?scenario=partial` 을 붙이면 localStorage 에 저장되고 그다음부터 유지된다.
 * 되돌리려면 `?scenario=default`.
 */

export const SCENARIOS = {
  default: "기본 — 기억이 쌓인 상태 · 개인화 추천",
  empty: "기록 0건 — 03 홈 빈 상태 (highlight: null)",
  scarcity: "근거 부족 — 개인화 대신 일반 추천 + 되묻는 질문 1개",
  partial: "Agent 2개 중 1개 실패 — 성공·실패를 한 화면에 (NF-06)",
  failed: "입력 처리 실패 — raw_text 를 입력창에 되돌림",
  // 🚨 **failed 가 아니다** (#253 · #271). 서버는 저장한 뒤에 끊긴 run 을 done 으로 닫고 키를 놓지
  //    않는다. 화면이 "저장하지 않았어요" 나 다시 시도를 세우면 같은 말이 두 번 저장된다.
  //    일정 낱말만 쓰면 `saved` 도 못 보내고 끊긴 run 이 된다 (무엇이 저장됐는지 화면이 모르는 경우).
  interrupted: "04 저장한 뒤 끊김 — done + completed: false (다시 시도 없이 '반영했어요')",
  disconnected: "종료 이벤트 없이 스트림이 끊김 — 저장 여부를 모르는 상태",
  // 🚨 **아래 둘은 끊긴 뒤 5분이 지난 것으로 친다** (#538). 서버는 닫힌 채널을 5분 뒤 지우는데 키는 24시간 남아서,
  //    그 뒤에 같은 한 줄을 다시 보내면 SSE 가 404 다. 서버는 대화 기록이 있을 때만 끝났다고 알린다.
  replay_finished:
    "04 결과 못 받음 → 다시 시도 — 서버는 끝냈고 대화 기록이 있다 (SSE 없이 저장된 결과가 그 줄에 · 다시 보내기 없음)",
  replay_unknown:
    "04 결과 못 받음 → 다시 시도 — 채널도 대화 기록도 없다 (또 결과 미확인 · 기록에서 확인하라는 안내)",
  consent: "필수 동의 미완료 — 신규 가입 대기 · 403 consent_required 로 저장 차단",
  auth_unready: "로그인 미연결 — GET /auth/kakao/status 가 ready: false",
  // 🚨 **받아 간 뒤에 바뀐 것**이라 정적으로 만들 수 없다 (`handlers/policies.ts`). 첫 제출이
  //    버전을 올려 400 을 만들고, 화면은 다시 받아 **바뀐 항목만** 체크를 풀어야 한다.
  policy_bumped: "가입 동의 — 제출 직전에 약관이 새 판으로 (400 policy_version_invalid)",
  stale: "6개월 지난 근거 — is_stale 인 기억만 남은 상태 (NF-08)",
  observations_many: "07 기록 25건 — 한 쪽 20건을 넘겨 쪽 넘기기가 서는 상태 (#584)",
  suggestions_many:
    "07 제안 피드백 22건 — 피드백 전이 21건이라 2쪽, 2쪽의 한 줄을 평가하면 1쪽으로 옮겨지는 상태 (#592)",
  photo_unreadable: "08 사진 — 읽어낼 게 없는 사진 (failed · 저장된 것 없음)",
  // 🚨 **화면에는 아무 차이도 안 난다.** 08 은 시트에서 고른 lane 을 그대로 쓰고 서버 추측을
  //    보여주지 않는다 — 이 시나리오는 "추측이 선언을 덮지 않는다" 를 거는 **계약 테스트용**이다.
  photo_lane_mismatch: "08 사진 — 서버 추측이 고른 종류와 어긋남 (계약 테스트용 · 화면 변화 없음)",
  photo_meal_plan: "08 사진 — 한 달치 식단표 (항목 21건 · 잘 읽은 것이 접혀 있어야 하는 이유)",
  // 🚨 아래 넷은 Supervisor 가 붙어야 나오는 상태다 (#141). 안전 사전검사에 걸리거나, 아직 없는
  //    Agent 로 가거나, Memory 가 되묻거나, 하루 한도에 걸려야 나온다 — 실서버로는 만들기 어렵다.
  guidance: "04 안내 — 알레르기를 대신 저장하지 않는다 (저장 0건 · 직접 입력 안내)",
  // 🚨 **한 줄에 두 얘기가 섞인 경우다** ("계란 잘 먹었어. 그리고 땅콩 알레르기 있어").
  //    서버는 앞은 저장하고 뒤는 안내로 돌린다 — 화면이 안내만 세우고 저장을 감추면
  //    보호자는 **아무것도 저장 안 된 줄 안다.** unavailable 과 같은 종류의 회귀다.
  guidance_mixed: "04 안내 + 저장 — 섞인 한 줄 (안내와 저장된 기록이 한 화면에)",
  // 🚨 **저장과 같이 온다는 것이 요점이다.** 이 시나리오가 잡는 회귀는 "준비 중 안내가 떴다고
  //    저장된 기록이 화면에서 사라지는 것" 이다 (NF-06 과 같은 규칙).
  unavailable: "04 준비 중 — 없는 Agent 로 간 요청 + 저장은 그대로 (성공·실패 한 화면)",
  note_question: "04 되묻기 — Memory 질문 하나만 (저장 0건 · 대화 안에서 답하는 동선)",
  // 🚨 **#158 리뷰가 짚은 빠진 경우다.** Memory 는 한 후보를 저장한 뒤 다른 후보의 정보가
  //    모자라면 글로 되묻는다 — `Saved` 와 `MemoryNote` 가 한 run 에 같이 나간다
  //    (`apps/api/app/agents/pipeline.py`). 이 대본이 없어서, 답할 때 원문을 되돌리면
  //    **이미 저장된 조각이 또 저장된다**는 것을 못 봤다.
  note_mixed: "04 일부 저장 + 되묻기 — 한 run 에 저장과 질문이 같이 (원문 재전송 금지 근거)",
  // 🚨 **이어받기 run 이 한 번 실패한다** (#175). 서버는 그때 맥락을 되돌려 두므로, 다시 시도가
  //    같은 `reply_to` 를 실으면 저장되고 빠뜨리면 맥락 없는 새 입력이라 **질문이 또 뜬다.**
  reply_failed: "04 되묻기 답 — 이어받기 run 이 한 번 실패 (다시 시도가 reply_to 를 유지해야 한다)",
  // 🚨 **이어받기 run 이 또 묻는다** (#226). 답한 뒤 다음 답의 `reply_to` 가 **처음 질문이 아니라
  //    두 번째 질문을 낸 run** 이어야 한다 — 처음 run 은 서버가 이미 한 번 꺼내 썼다(400).
  reply_asks_again:
    "04 되묻기 답 — 이어받기 run 이 한 번 더 묻는다 (다음 답은 그 run 을 가리켜야 한다)",
  // 🚨 **재질문 상한에 닿은 run 이다** (#246 · 서버 #251). 처음 질문까지 4번 물은 조각에 또 물으려 하면
  //    서버는 질문 대신 안내 문장(`kind=message`)을 내고 맥락을 남기지 않는다 — 질문이 닫히고 새로 열리면 안 된다.
  reply_ask_limit:
    "04 되묻기 답 — 네 번 묻고 나면 질문 대신 '저장하지 않았어요' 로 끝난다 (재질문 상한)",
  // 🚨 **서버가 앞 이야기를 놓친 경우다** (#175 — 15분 만료 · 서버 재시작). 방금 물었어도 답은 400 이다.
  reply_unavailable:
    "04 되묻기 답 — 400 reply_context_unavailable (다시 시도 없이 질문을 놓아야 한다)",
  // 🚨 **응답을 못 받은 보내기다** (#231 리뷰). 서버에 닿기 전에 끊긴 것이라 같은 키로 다시 보내면 닿는다.
  //    이 말풍선을 다음 한 줄이 대체하면, 보낼 때 입력창을 비웠으니 원문이 어디에도 안 남는다.
  send_failed:
    "04 보내기 실패 (네트워크) — 키마다 처음 한 번은 서버에 닿지 않는다 (같은 키로 다시 시도하면 닿는다)",
  daily_limit:
    "04 한 줄 보내기 — 하루 한도 초과 429 (다시 시도 버튼이 없어야 한다) · 05 추천도 429",
  // 🚨 **앱을 다시 켠 상태다** (#228). 새로고침하면 목이 비어서 대화 스토어도 빈다 — 그때 서버가 남긴 오늘 대화가
  //    다시 서고, 위로 올리면 어제 · 사흘 전이 붙어야 한다. 지난 턴에서 막다른 버튼(넣기 · 다시 준비하기)이 없어야 한다.
  chat_history:
    "04 지난 대화 — 오늘 · 어제 · 사흘 전 (지운 기록 · 답한 질문 · 닫힌 질문 · 지난 초안 · 부분 실패). 09 하루 보기에 대화 수",
  // 🚨 **첫 장(오늘)이 비어 온다.** 캘린더에서 지난 날로 들어가면 전날 장들을 받는 동안 빈 상태가 서면 안 된다.
  chat_history_past:
    "04 지난 대화 — 오늘 대화 없이 어제 · 사흘 전만 (09 에서 지난 날로 들어갈 때 빈 상태가 깜빡이지 않아야 한다)",
  // 🚨 아래 셋은 **요청형 한 줄**이다 (#227). 저장할 관찰 없이 도메인 Agent 결과(`agent_result`)만 온다.
  //    실서버는 Agent 가 붙어야 나오고, 추천이 없는 결과(성장 추이 · 0개 안내 · 되묻기)는 기억이 쌓이는 것과
  //    상관없이 Agent 사정으로 나와서 손으로 켜야 볼 수 있다.
  agent_readout:
    "04 Agent 결과 — 성장 추이 + 0개 안내 + 화면에 보일 것 없는 급식 갱신 (블록 둘 · 빈 결과는 안 선다)",
  agent_question: "04 Agent 결과 — Agent 가 되묻는 한 줄 (답하기 버튼이 없어야 한다 · #246 전)",
  agent_partial: "04 Agent 결과 — 한 Agent 는 안내, 다른 Agent 는 실패 (성공 · 실패를 한 화면에)",
  agent_suggestions:
    "04 Agent 결과 — 추천이 든 결과 (식사 개인화 3개 · 놀이 일반 1개 + 날씨 안내). 후보 보기가 그 run 의 05 를 연다",
  // 🚨 **서버가 화면보다 먼저 새 검사를 붙인 날이다** (#233 후속). 화면은 물을 수 없으니 그 제안을
  //    막아야 한다 — 예전에는 시트만 열리고 아무것도 안 물은 채 그대로 채택됐다.
  precheck_unknown_code: "05 사전검사 — 화면이 모르는 code (그 제안은 고르지 않아야 한다)",
  // 🚨 아래 둘은 **실패가 아니다** (#249). 0개로 끝난 Agent 도 안내와 함께 묶음이 오고, 이어서 할 수
  //    없는 Agent 도 안내만 온다. 화면이 이것을 실패 카드로 그리면 다시 시도가 같은 응답만 받아 온다.
  suggest_counts: "05 개수 안내 — 식사 2개(fewer 안내) · 놀이 0개(empty 안내 · 실패 아님)",
  cannot_resume:
    "05 이어서 할 수 없음 — '후보 보기' 로 왔는데 놀이는 맥락이 없다 (다시 시도 버튼이 없어야 한다)",
  // 🚨 식품 사전에 없는 알레르기(꽃가루)는 뺄 놀이를 코드가 못 정해서 보호자에게 확인을 맡긴다 (#433 리뷰 ③)
  suggest_cautions: "05 식품 아닌 알레르기 — 놀이 묶음에 확인 문구(꽃가루)",
  // 🚨 같은 Agent 안에서도 후보마다 `kind` 가 갈린다 (#512 리뷰 · 서버 `build()`). 실서버로는 기록이 반쯤
  //    쌓인 상태를 맞춰야 나온다.
  suggest_mixed:
    "05 섞인 묶음 — 식사 개인화 3개 + 식사 일반 1개(같은 Agent) · 놀이 0개 안내 · 되묻기 카드",
  // 🚨 실서버는 좌표가 실린 입력 run 에서만 장소를 찾는다 (#516). 웹이 좌표를 보내기 전(#593)에는 볼 길이 없다.
  suggest_place:
    "05 장소가 붙은 놀이 — 개인화 1개 · 일반 1개에 확인 문구(place_notice) · 근거 목록에는 없다",
  // 🚨 아래 셋은 **응답을 못 받은 탈퇴**다 (#389 · PR #384). 서버는 커밋 뒤에 응답이 사라질 수 있어서
  //    화면이 `/me` 로 한 번 확인한다. 끊기는 시점을 실서버로 맞출 수 없다.
  withdraw_lost:
    "10 탈퇴 — 지운 뒤에 응답이 사라짐 (/me 가 401 · '탈퇴했어요' 로 끝나야 한다, 첫 화면으로 튕기면 안 된다)",
  // 🚨 **다시 누른 탈퇴가 401 을 받는 길**이다. 응답도 확인(`/me`)도 못 받아 "확인하지 못했어요" 가 선
  //    뒤 다시 누르면, 세션이 이미 없어서 401 이다 — 이 401 이 전역 처리로 새면 첫 화면으로 튕긴다.
  withdraw_unconfirmed:
    "10 탈퇴 — 지운 뒤 응답도 첫 확인도 끊김 ('확인하지 못했어요' → 다시 누르면 401 → '탈퇴했어요')",
  // 🚨 아래 둘은 **refresh 로 세션을 잇는 길**이다 (#465). 15분 기다리거나 서버를 꺼야 나온다.
  session_expired:
    "세션 갱신 — 처음 받은 access 가 이미 만료 (첫 요청 401 → 갱신 뒤 다시 보내 그대로 이어져야 한다)",
  session_unreachable:
    "세션 갱신 — 앱을 켤 때 서버에 닿지 못함 (로그인 화면이 아니라 연결 확인 · 다시 시도)",
  logout_unreached:
    "10 로그아웃 — 서버에 닿기 전에 한 번 끊김 (나간 척하지 않고 실패를 남긴다 · 다시 누르면 나간다)",
  withdraw_unreached:
    "10 탈퇴 — 서버에 닿기 전에 한 번 끊김 (/me 가 200 · '아직 지워지지 않았어요', 다시 누르면 닿는다)",
  // 🚨 보호자당 24시간 10건을 채워야 나오는 상태다 (#487 · 서버 PR #488). 목은 건수를 세지 않는다.
  report_limit:
    "10 문제 알리기 — 24시간 안에 보낼 수 있는 만큼 보냄 429 too_many_reports (서버 문구 그대로 · 다시 시도가 없어야 한다)",
  report_unreached:
    "10 문제 알리기 — 서버에 닿기 전에 한 번 끊김 ('전해졌는지 확인하지 못했어요' · 적은 글 그대로 · 다시 누르면 닿는다)",
} as const;

export type Scenario = keyof typeof SCENARIOS;

export const DEFAULT_SCENARIO: Scenario = "default";

const STORAGE_KEY = "icatch.mock.scenario";

function isScenario(value: string | null): value is Scenario {
  return value !== null && value in SCENARIOS;
}

/** 핸들러는 요청마다 이걸 부른다 — 새로고침 없이 바꿔도 다음 요청부터 반영된다. */
export function currentScenario(): Scenario {
  if (typeof localStorage === "undefined") return DEFAULT_SCENARIO;
  const stored = localStorage.getItem(STORAGE_KEY);
  return isScenario(stored) ? stored : DEFAULT_SCENARIO;
}

export function setScenario(scenario: Scenario): void {
  localStorage.setItem(STORAGE_KEY, scenario);
}

/** `?scenario=` 를 읽어 저장한다. 목을 켜기 전에 한 번 부른다. */
export function syncScenarioFromUrl(): void {
  if (typeof window === "undefined") return;
  const requested = new URL(window.location.href).searchParams.get("scenario");
  if (isScenario(requested)) setScenario(requested);
}
