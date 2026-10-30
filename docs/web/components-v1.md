# 컴포넌트 · 스타일 구현 규칙 v1

문서 목적: 직접 만든 primitive 와 조합의 목록, 화면 공통 UI 패턴, 아이콘, 스타일 토큰을 코드에서 쓰는 규칙을 고정한다.

기준 브랜치: `docs/fe-406-split-web-claude-md`
작성일: 2026-10-14
담당: 고태영
선행 문서: [`apps/web/CLAUDE.md`](../../apps/web/CLAUDE.md) (작업마다 읽는 요약. 이 문서는 해당 코드를 만질 때 읽는 원문이다)

> `apps/web/CLAUDE.md` 에서 옮겨 온 본문이다 (#406). 문장은 고치지 않고 자리만 옮겼다.
> 본문이 원문 그대로라, 앞에 아무것도 안 붙은 `§N` 은 맥락에 따라 `apps/web/CLAUDE.md` · 최상위 `CLAUDE.md` · 디자인 시스템 중 한 곳의 절이다.
> "위" · "아래" 도 원문(`apps/web/CLAUDE.md`)에서의 위치 기준이다.
> 사양(색 · 크기 · 상태)의 원본은 [`design-system-v1.md`](design-system-v1.md) 와 `globals.css` 다. 이 문서는 코드에서 그것을 **어떻게 쓰는가**만 다룬다.

---

## 1. 컴포넌트 — 직접 만든다

별도 UI 라이브러리를 쓰지 않는다. 토큰과 1:1 로 붙고 고치기 쉬운 쪽을 골랐다.
사양은 [디자인 시스템 §7](../../docs/web/design-system-v1.md) 에 있다 — 없는 값을 즉석에서 만들지 않는다.

🚨 **예외는 recharts 하나이고, 쓰는 자리도 하나다** (11-1 변화 그래프). 그리는 물건이 버튼이나
입력이 아니라 **축 계산 · 눈금 배치 · 반응형 리사이즈**라서, 손으로 만들면 그게 곧 차트 라이브러리다.
🚨 **recharts 로 화면 부품을 만들지 않는다** — 토큰과 1:1 로 안 붙는 물건이라 카드·칩·목록이
거기 들어오는 순간 §5 스타일 규칙이 두 벌이 된다. 색·글자 크기는 그 안에서도
`var(--color-*)` 로 넘긴다 (`GrowthChart`).

- `components/ui/` — 토큰만 아는 primitive. 도메인 타입(`Suggestion` 등)을 import 하지 않는다
- `components/` — 도메인을 아는 조합
- 지금 있는 것 (`components/ui/`) — `Screen`(최대 폭·좌우 여백·**상하 여백+safe area**) · `PageTitle` ·
  `Button`(§7 6변형) · `TextInput`(검색칸은 `leading` · `trailing` · `labelHidden` · 🚨 브라우저 search × 숨김) · `TextArea` · `DateField` · `DateRangeField`(기간 한 칸 · 범위 달력 · 한 날이면 그날 하루) · `TimeField` · `Checkbox` · `Chip`/`ChipRow` · `RemovableChip`(걸린 조건 · 누르면 풀린다) ·
  `EvidenceChip`/`CountChip`/`EvidenceRow` · `Card`(`accent`)/`CardFailed` · `Banner` · `Spinner` ·
  `IconButton`/`IconButtonLink`/`IconButtonPlaceholder`(받는 동안 버튼 자리) · `IconTile` · `ProgressSteps` · `EmptyState`/`EmptyStateSkeleton` ·
  `Skeleton`/`SkeletonText`/`SkeletonTile`/`SkeletonGroup`(🚨 받은 내용과 같은 상자 · 디자인 시스템 §7 "빈 상태 · 스켈레톤") ·
  `BottomSheet` · `Tabs`/`PanelTabs`(🚨 주소가 바뀌면 앞, **패널만 바뀌면** 뒤 · 디자인 시스템 §7) · `Toast` · `ButtonLink` ·
  `Select` · `ChoiceField`(반드시 하나 · 🚨 선택지가 둘이면 `Select` 를 쓰지 않는다 · 넷 이상이거나 문장이 길면 `direction="vertical"` · 목록 위의 보기 전환만 `labelHidden` · 디자인 시스템 §7) ·
  `Pagination`(번호 쪽 목록의 아래 한 줄 · 화살표 둘 + 번호 다섯 개의 창 · 디자인 시스템 §7 "쪽 넘기기") ·
  `PhotoCard`/`PhotoSlotButton` · `Swap`(같은 자리의 교체 페이드 · `phaseOf`) · `Named`(화면 전환의 이름 쌍)
- 움직임 (`lib/motion.ts`) — `useEnterMotion`(데이터가 바꾸는 교체 · 단계 넘김) · `useMotionCue`(누른 것만) ·
  `useReveal`(펼침) · `useFlip`(자리 옮김) · `playEnter`. 화면 전환은 `components/route-transition.tsx` 의 `ScreenTransition` 과
  `lib/view-transition.ts` 의 `FORWARD` · `BACK`. 쓰는 자리와 규칙은 디자인 시스템 §8
- 도메인을 아는 조합 (`components/`) — `DomainChip`/`DomainMeta`/`ObservationMeta` · `AgentPrompts` · `ChildNav` · `SuggestionList` ·
  `HomeComposer`/`VoiceLevelMeter`(말로 적기 듣는 동안의 음량 막대 · 셸 `volume` 이벤트를 직접 구독) · `GeneralSuggestionCard` · `ChatThread` · `RunReply` · `SafetyCheckSheet`(게이트 ㉡) · `ApprovalSheet`(게이트 ㉠) · `ConsentRequiredCard` ·
  `AuthGate`/`SessionChecking`/`SessionUnreachable` · `WebPushBridge`(그리는 것 없음 · 로그인이 살아나면 기기 등록, 앱이면 셸이 넘긴 토큰 등록, 알림을 누르면 그 화면으로 · #609) · `ChildScope` · `ObservationList` · `AffinityList` · `CorrectionButtons` · `MemoryDetailSheet` ·
  `SuggestionFeedbackList` · `MonthGrid`/`DayMarkLegend` · `CalendarDayPanel` ·
  `SettingsGroup`/`SettingsLinkRow`/`SettingsInfoRow` · `StartBackLink`(01 · 초대 → 00-1) · `ConsentSection`/`LegalDocumentSection` · `LocationPermissionNote`(10 설정 · 위치 동의는 켜졌는데 휴대폰이 허용 안 했을 때만 · #593) ·
  `ParentSection` · `AccountSection` · `NotificationSection`(10 설정의 일정 알림 켜고 끄기 · 동의 줄과 같은 모양 · 복약 줄은 발송이 붙을 때까지 없다 · #609) ·
  `ChildIdentityCard`/`ChildIdentitySheet` · `GrowthLogList`/`GrowthLatestCard` · `GrowthSheet` ·
  `GrowthChart` · `HealthSafetyList`/`HealthSafetySheet` · `SafetyScanReview`/`SafetyScanRowSheet` ·
  `PhotoReview`/`PhotoEntrySheet` · `PhotoSourceSheet` ·
  `ReminderField`(일정 알림 고르기 · 초안 카드 · 08 시트 · 09 패널이 같이 쓴다 · 지난 값은 목록에서 뺀다 · #609)
  (시트 안의 사진 종류 줄 · 최근 사진 줄 · 촬영/앨범 줄은 그 파일의 내부 조각이다)
- 받는 동안의 모양은 **그 컴포넌트 옆에 `…Skeleton` 으로** 둔다 — `SettingsRowSkeleton` · `ChildIdentityCardSkeleton` ·
  `GrowthLatestCardSkeleton`/`GrowthLogListSkeleton` · `GrowthChartSkeleton` · `HealthSafetyListSkeleton` · `ObservationListSkeleton` ·
  `AffinityListSkeleton` · `SuggestionFeedbackListSkeleton` · `SuggestionListSkeleton` · `CalendarDayPanelSkeleton` · `ConsentChecklistSkeleton`.
  🚨 진짜 컴포넌트의 여백 · 줄을 바꾸면 옆의 스켈레톤도 같은 PR 에서 바꾼다 (`e2e/layout-shift.spec.ts` 는 아래가 통째로 밀리는 큰 어긋남만 잡는다 · 모양은 받는 중 화면을 직접 보고 맞춘다 · [`mock-and-tests-v1.md`](mock-and-tests-v1.md) §3-1)

- 🚨 **화면 위 · 아래 고정 바는 `Screen` 의 `topBar` · `bottomBar` · `nav` 로 넘긴다.** `topBar` 는 위 safe area + 여백을 대신 받는다 (04 대화의 머리 — 대화가 자라도 돌아가는 화살표가 밀려나지 않게). 화면이 직접 `sticky` 를 붙이면
  아래 여백을 0 으로 되돌려야 하는데, 그게 상하 여백을 두 번 죽인 바로 그 조작이다.
  둘 다 넘기면 채팅바가 위·네비가 아래로 한 덩어리가 되고 **safe area 는 제일 아래 것만** 받는다
- 🚨 **하단 네비(`ChildNav`)는 가는 곳 네 화면과 설정에만 붙인다** (홈·캘린더·기록/기억·아이). 04 대화·05 제안 후보처럼
  흐름 중인 화면에 붙이면 고르는 도중에 새는 길이 생겨 그 화면이 끝나지 않는다 (디자인 시스템 §7).
  설정의 하위 화면 셋이 이 기준으로 갈린다 — **고객센터는 읽는 화면이라 붙이고, 탈퇴는 절차가
  있어서 안 붙인다**(빠져나가는 길은 자기 "그만두고 돌아가기" 하나다). **문제 알리기도 안 붙인다** —
  적는 도중에 새는 길이 있으면 적던 글이 그 자리에서 사라진다 (#487)
- 🚨 **돌아가기 화살표는 `IconButtonLink` 다** (`components/ui/icon-button.tsx`). `IconButton` 과
  **같은** 모양·톤 표를 쓴다. 🚨 `router.back()` 으로 만들지 않는다 — 히스토리는 어디서 왔는지에
  따라 달라져서, 알림이나 링크로 바로 들어오면 돌아갈 데가 없다. 🚨 `label` 에 "뒤로" 가 아니라
  **가는 곳**을 적는다("설정으로 돌아가기"). 🚨 **`PageTitle` 과 한 줄**에 세우고 `-ml-3` 를
  **줄 전체**에 건다 — 44px 원 안에 20px 아이콘이 가운데 있어 좌우 12px 이 비고, 그대로 두면
  화살표가 화면 왼쪽 기준선보다 안쪽에 선다.
  ⚠️ 그러면 **제목만 왼쪽이 안 맞는다**(화살표 16 · 제목 52 · 나머지 전부 16). 가로로 붙이는 한
  피할 수 없고(타깃 44 > 여백 16), 세로 44px 을 아끼는 값으로 받기로 했다 (제품 결정 · #89)
- 🚨 **한 화면에 같은 이름의 링크를 둘 두지 않는다.** 고객센터 아래에 화살표와 같은 이름의
  돌아가기를 하나 더 뒀더니 스크린리더 링크 목록에 같은 이름이 두 번 떴다 — 둘 다 같은 곳으로
  갔다. 탈퇴 화면은 예외가 아니라 **다른 경우**다(아래 "그만두고 돌아가기" 는 흐름을 그만둔다는
  뜻이라 이름도 하는 일도 화살표와 다르다)
- 🚨 **10 설정 › 문제 알리기는 앱 안에서 보낸다** (#487 · 서버 `POST /reports`). Google Play 정책(AI 생성
  콘텐츠 · 사용자 제작 콘텐츠)이 불쾌한 AI 답 · 공동 기록 · 다른 사용자를 **앱을 떠나지 않고** 알릴 길을
  요구한다 — `mailto:` 로 대신하지 않는다. 🚨 적은 글은 컴포넌트 상태에만 둔다 — 콘솔 · `persist` ·
  `localStorage` · `sessionStorage` 로 옮기지 않는다(서버도 DB 에만 두고 로그에 안 찍는다). 🚨 승인
  게이트도 되돌릴 수 없는 5곳도 아니다 — 게이트 표식도 `Idempotency-Key` 도 없다. 🚨 분류는 첫 항목이
  골라진 채로 시작한다(`ChoiceField` 세로 변형) — 아무것도 안 고른 칸은 테두리만 있는 상자라 고르는
  칸인지 모르고, 그러면 보내기가 왜 꺼져 있는지 알 수 없다. 🚨 열람 · 삭제처럼 답이 필요한
  요청은 처리방침의 이메일(`SERVICE_EMAIL`)로 보내 달라고 **적기 전에** 말한다 — 이 양식은 답을
  보내는 길이 없어서 공식 권리 행사 창구가 아니다. 고객센터의 "질문에 없는 일" 도 이 화면으로 보낸다
- 🚨 **버튼처럼 보이는 링크는 `ButtonLink` 다** (`components/ui/button.tsx`). `Button` 과 **같은**
  변형·크기 표를 쓴다 — 두 벌이 되면 한쪽만 고쳐져 같은 자리에 선 둘이 달라진다.
  🚨 `<button onClick={router.push}>` 로 대신하지 않는다(링크가 링크가 아니게 된다) ·
  🚨 행동에 쓰지 않는다(누르면 값이 바뀌는 것은 `Button`) · 🚨 `disabled` 가 없다(`<a>` 에는
  그 상태가 없다 — 못 가는 링크는 안 그린다)
- 🚨 **탈퇴처럼 끝나면 세션이 없어지는 화면은 끝 상태를 `AuthGate` 밖에 둔다.** 안에 두면
  게이트가 그 순간 `/` 로 튕겨서 "탈퇴했어요" 를 아무도 못 보고, 게이트가 **복귀 경로로 그
  주소를 기억**해 둬서 다음 로그인이 탈퇴 화면으로 떨어진다 (실제로 그랬다)
- 🚨 **네비는 `surface-muted` 면이고 위에 선이 없다.** 선을 하나 더 긋는 것으로는 채팅바와 안 갈린다 —
  `line` 1px 은 `canvas` 위에서 1.21:1 이고 03 홈에는 **같은 선이 27px 위에도** 있어서, 한 신호가
  "여기부터 고정" 과 "여기부터 다른 종류" 를 나눠 쓰면 하단이 줄 쳐진 슬래브 하나로 읽힌다
- 🚨 **켜진 탭에 모양 신호를 함께 준다** (칸 위쪽 `brand` 2px). `brand` 와 `ink-subtle` 은 휘도 차가
  1.15:1 이고 손글씨는 굵기 대비가 약해 굵기로도 못 만든다 — 색만 두면 단독 신호가 된다 (디자인 시스템 §3)
- 🚨 **설정에서 `aria-current` 를 붙이지 않는다** (`onRoute={false}`). 홈 칸을 켜 두는 것은 시각적
  결정이고, 제목이 "설정" 인 화면에서 "홈, 현재 페이지" 라고 읽히면 그건 사실이 아니다
- 🚨 **네비가 가리키는 곳에는 라우트가 먼저 있어야 한다.** 07·09·10 은 내용이 생기기 전에도 화면을 뒀다 —
  아무 데도 안 가는 탭을 만들지 않는다 (00 로그인의 `ready:false` 와 같은 원칙). 지금 남은 자리표시는 10 하나다
- 🚨 **브랜드색은 정해진 여섯 자리에만 쓴다** (디자인 시스템 §2-2 표). 밋밋하다고 아무 데나
  초록을 넣으면 "색 하나 = 뜻 하나" 가 무너진다. `brand-soft` 로 **큰 면을 칠하지 않는다** —
  제안이 앉는 색 면은 **그 제안의 도메인 색**이고(§2-3), 브랜드는 고르는 버튼이 가져간다.
  "어디서 왔나"(도메인)와 "무엇을 하는가"(브랜드)를 같은 색으로 쓰지 않는다

- 🚨 **고르기 상자(`Select`)는 직접 만든 드롭다운이다.** 네이티브 `<select>` 는 닫혀 있을 때
  말고는 생김새를 우리가 못 정해서 쓰지 않기로 했다 — 대신 **키보드 · 포커스 · 스크린리더 ·
  바깥 클릭이 전부 그 파일의 책임**이 된다. 그 파일의 🚨 를 지우지 말 것:
  ARIA 는 `combobox` + `listbox` 한 쌍 · 열 때 고른 항목으로 포커스가 들어가고 **닫을 때 버튼으로
  돌아온다**(안 그러면 포커스가 `<body>` 로 떨어진다 · 09 달력에서 낸 사고와 같다) ·
  ESC · 바깥 클릭 · Tab 에 닫힌다 · 그림자 없이 `line-strong` 1px 로 뜬 면을 만든다 ·
  열린 목록은 투명도로만 서고 쉐브론은 회전하지 않는다(방향은 아이콘을 갈아 끼워 말한다)
  🚨 **스크롤에는 닫지 않는다** (#306). 목록이 `relative` 루트 안의 `absolute` 라 트리거에 붙어 같이
  움직이는데, 떨어질까 봐 넣었던 스크롤 가드가 두 번 사고를 냈다 — 처음엔 **열릴 때 포커스가 일으킨
  스크롤**을 잡아 바텀시트 안에서 상자가 아예 안 열렸고, 그걸 한 프레임 미뤄 막은 뒤에는 **본문 바닥에
  잘린 항목을 보려고 미는 스크롤**에 닫혀서 아래쪽 항목을 터치로 고를 수 없었다. 대신 **열 때 목록이
  보이게** 가장 가까운 스크롤 면을 움직인다. ⚠️ 목록을 포털이나 `fixed` 로 옮기면 이 전제가 깨진다
- 🚨 **접히는 질문(10 › 고객센터)은 네이티브 `<details>`/`<summary>` 다.** 펼침 상태·키보드·포커스를
  브라우저가 준다 — `aria-expanded` 를 손으로 달지 않는다. 🚨 기본 마커를 지우고(`list-none` +
  `::-webkit-details-marker`) 쉐브론은 **회전이 아니라 갈아 끼운다**. 🚨 `summary` 여백은 카드 밖으로
  **네 방향 다** 되민다(`-m-4 p-4`) — 아래만 남기면 닫힌 카드에 32px 빈 띠가 생긴다
- 🚨 **화면을 그리는 외부 라이브러리는 `<dialog>`(시트) 와 `react-day-picker`(달력) 둘뿐이다.**
  접근성을 손으로 짜면 반드시 빠뜨리는 것만 예외로 얹는다. 달력은 **기본 CSS 를 불러오지 않고**
  `classNames` 로 토큰만 입힌다 — 버튼·입력을 주는 UI 킷은 계속 쓰지 않는다 (디자인 시스템 §7).
  🚨 고르기 상자(`Select`)는 그래서 **직접 만들었다** — 예외를 늘리지 않았다
- **화면을 안 그리는 것은 이 규칙 밖이다.** 지금 있는 것은 `es-hangul`(조사) 하나다 —
  한글 받침 규칙은 손으로 짜면 `ㄹ` 예외 같은 데서 틀리는데, 화면 생김새에는 아무 영향이 없다.
  🚨 **여기에 UI 를 그리는 것을 끼워 넣지 않는다** — 그러면 위 규칙이 이 줄로 빠져나간다
- 🚨 **`<Screen>` 에 `py-*` 를 넘기지 않는다.** 상하 여백은 `Screen` 이 소유한다.
  safe-area 유틸은 **여백을 함께 받는다** (`pt-safe-8` = safe area + 32px) — 예전처럼 safe area 만 넣는
  유틸에 `py-8` 을 겹치면 같은 padding 속성이라 **한쪽이 조용히 죽는다.** 이 사고를 두 번 냈다:
  화면 5개의 상하 여백이 0 이었고, 바텀시트 닫기 버튼이 화면 맨 아래에 붙었다
- 🚨 **화면 제목은 `PageTitle` 로 쓴다.** §9 가 좁은 폰(< 380px)에서 `display` → `title` 로 낮추라고
  정했는데, 화면마다 손으로 쓰면 어긋난다
- ⚠️ `cn()` 은 **tailwind-merge 가 아니다.** 뒤에 온 클래스가 앞을 덮어주지 않으니, primitive 밖에서
  `className` 으로 색·크기를 덮어쓰지 않는다. 필요하면 변형을 primitive 안에 추가한다
- ⚠️ 라이브러리를 안 쓰는 대신 **접근성이 전부 우리 책임**이다. 포커스 트랩·ESC·스크롤 락을 직접 짜야 한다
- 🚨 **토스트는 "조용히 되돌아간 실패" 에만 쓴다** (디자인 시스템 §7 토스트). 성공은 화면이 이미
  말하고, 되돌릴 것이 있으면 화면 안에 자리를 만든다 — 토스트는 사라지므로 되돌릴 길을 담으면
  길이 같이 사라진다. 🚨 **바텀시트 안에서 부르지 않는다** — `<dialog>` 의 top layer 뒤로 깔려
  안 보인다. 🚨 발화 원문을 싣지 않는다 (최상위 §2)
- 🚨 **바텀시트는 네이티브 `<dialog>` 위에 얹는다.** 포커스 트랩 · ESC · 바깥 `inert` · 스크림을 브라우저가 준다.
  "승인 시트는 스크림 탭으로 닫히지 않는다"(디자인 시스템 §7)는 `cancel` 이벤트를 막아 처리한다

## 2. 아이콘 — `lucide-react`

Next 16 기본 `optimizePackageImports` 목록에 있어서 배럴 임포트를 그대로 써도 트리셰이킹된다
(`next.config.ts` 에 설정할 것 없음. 아이콘 1개 추가에 번들 +12KB 로 확인).

크기 · 굵기 · 도메인 매핑은 `components/ui/icon.tsx` 한 곳에 있다. 화면에서 `size={18}` 처럼 직접 쓰지 않는다.

🚨 `DomainIcon` 은 **`aria-hidden`** 이다. 도메인 아이콘은 색과 함께 단독 신호가 될 수 없고
(디자인 시스템 §3), 의미는 항상 옆의 텍스트 라벨이 진다.

기록 한 건을 그리는 자리(07 · 09 기록 줄 · 기록 상세 · 04 기록 줄)는 Agent 가 아니라 **기록 종류**로 그린다 —
`ObservationIcon` · `ObservationMeta` · `observationLabel()` (셋 다 `components/domain-chip.tsx` — 관찰 테이블 이름은 도메인 타입이라 `ui/icon.tsx` 에 두지 않는다). 성장 기록만 교육 · 생활습관으로 갈리고,
색은 그대로 `observationAgent()` 로 고른다 (디자인 시스템 §7 아이콘 · #501). 필터 · 제안 · Agent 블록은 `DomainIcon` 그대로다.

## 3. 스타일

- 색·radius·폰트는 `globals.css` 의 `@theme` 토큰으로만. 컴포넌트에서 `#hex` 를 직접 쓰지 않는다.
- 🚨 **토큰을 "비슷한 값" 으로 쓰지 않는다.** 이름이 곧 용도다 — `brand-ink` 는 **`brand-soft` 배경 위** 텍스트고,
  `canvas` 위 브랜드 텍스트는 `brand` 다. 대비만 맞으면 된다고 생각하면 "색 하나 = 뜻 하나"(문서 §1)가 무너진다.
- **`/design-system` 을 열어 보고 만든다.** 문서의 표를 실제 토큰·컴포넌트로 렌더하고 **대비비를 그 자리에서 계산**한다 — 토큰을 바꾸면 통과/미달이 바뀌므로 문서와 코드가 어긋나면 거기서 보인다. §7 에 사양은 있는데 아직 없는 컴포넌트 목록도 그 화면 맨 아래에 있다.
- 🚨 **`globals.css` 는 `@theme static` 이다.** 빼면 Tailwind 가 **쓰이는 토큰의 변수만** 내보내서, 아직 컴포넌트가 없는 토큰이 CSS 에서 사라진다 (실제로 9개가 빠져 있었다).
- **원본이 종류마다 한 곳이다** ([디자인 시스템](../../docs/web/design-system-v1.md) 머리말). 의도·사용 조건·금지 예는 **문서**,
  색·타이포·radius·높이처럼 여러 컴포넌트가 같이 쓰는 값은 **`globals.css` 의 `@theme`**, 한 컴포넌트 안의 치수와
  상태·포커스 동작은 **`components/ui/` 코드**다. 화면을 그리기 전에 문서를 읽고, 없는 값을 즉석에서 만들지 않는다.
- 🚨 **문서와 코드가 어긋나면 한쪽이 틀렸다고 가정하지 않는다.** 바뀐 이유를 먼저 찾는다 — 실기기 검증이나 접근성 때문에
  코드를 먼저 고친 것일 수 있다. 이유가 있으면 **같은 PR 에서** 문서 근거와 `DESIGN.md` 사본까지 맞추고, 못 찾으면 코드를 되돌린다.
- 🚨 **높이는 `min-h-*` 토큰으로만 쓴다** (`min-h-button` · `min-h-approve` · `min-h-field` · `min-h-touch` · `min-h-chip`).
  글자가 들어가는 요소에는 `h-12` 같은 숫자도, `h-button` 같은 고정 높이도 쓰지 않는다
  (아이콘만 있는 정사각 버튼은 `h-touch aspect-square` 로 고정해도 된다). 문구가 길거나 글자를 키우면 글자가 밖으로 흘러
  **승인 버튼이 무엇을 승인하는지 가려진다.** 컴포넌트를 추가·수정하면 `/design-system` 의 "긴 문구" 줄을
  **글자만 200%** 로 키워서 본다 (방법은 디자인 시스템 §10). 말줄임(`truncate`)으로 잘라 맞추지 않는다.
- 🚨 **실패를 빨강으로 칠하지 않는다.** `failed` · `partial` 의 실패 쪽 · `llm_unavailable` 은 `surface-muted` + `ink-muted` 다. `danger` 는 알레르기·건강 중단에만, `caution` 은 승인 게이트 2곳에만 쓴다.
- 🚨 **일반 추천과 개인화 추천을 색으로 구분하지 않는다.** 라벨과 기록 건수가 본체다 (§4 첫 줄과 같은 규칙).
- 본문 기본은 **16px / 1.6** 이다. 프로토타입의 11~13px 을 그대로 옮기지 말 것.
- 🚨 **화면에 보이는 글자에 이모지·기호를 쓰지 않는다** (디자인 시스템 §4). `①②③` · `↓` · `★` 로 뜻을 나타내지 않는다 —
  스크린리더가 제각각으로 읽고, 글리프가 없으면 두부(□)가 되며, 뜻을 나르는 것은 lucide 한 곳에 모아야 한다.
  순서는 `<ol>`, 목록 마커는 CSS `list-*`/`marker:`, 그림은 아이콘 + 텍스트 라벨.
  🚨 **줄표(`—`) 도 화면 텍스트에서 쓰지 않는다** — 괄호·쉼표·줄바꿈으로 바꾼다.
  **붙인 가운뎃점**(`수집·이용`)은 병렬 합성어라 그대로 두고, **띄운 가운뎃점**(` · `)은 메타 스트립에서 **줄당 하나까지**다.
  주석·문서·`/design-system`(내부 문서)은 대상이 아니다.
- **서체는 두 벌이고 쓰는 자리가 다르다** (디자인 시스템 §4). 둘 다 자체 호스팅 · 92개 동적 서브셋이다.
  - `font-sans` = **학교안심 날개 R.** 기본값이라 따로 붙일 일이 없다.
  - 🚨 `font-doc` = **Pretendard.** 이용약관 · 개인정보 처리방침 · 동의 전문처럼 **읽고 동의해야 하는 긴 법률 문서** 화면에 쓴다.
    폴백이 아니라 역할이다 — 손글씨는 자소 판별성이 낮아서, 불리한 조항을 놓치지 않고 읽어야 하는 글에서는 손해만 된다.
    **문서 화면은 통째로 `font-doc` 이다.** 제목만 손글씨로 두는 식으로 한 화면에서 섞지 않는다.
  - 🚨 `src/app/hakgyoansim.css` · `pretendard.css` 를 **손으로 고치지 않는다.** 각각 `scripts/build-hakgyoansim-subset.py` 와
    `pretendard` 패키지에서 뽑은 파일이고, 올릴 때 다시 뽑는다 (prettier 대상에서도 뺐다).
  - 🚨 **폰트 CDN 을 쓰지 않는다.** 둘 다 자체 호스팅이다 (최상위 §9). 새 폰트를 넣을 때는
    **라이선스가 woff2 변환과 재배포를 허용하는지 먼저 확인한다** — 금지면 자체 호스팅 자체가 불가능해진다.
  - 본문 서체는 **`wght 400–700` 가변폰트**다. 원본(학교안심 날개)이 Regular 한 벌뿐이라 빌드 스크립트가 700 마스터를
    만들어 굽는다 — 선언한 네 굵기가 전부 실물이고 브라우저 합성은 없다.
    ⚠️ 그래도 **굵기 하나에 신호를 걸지 말 것.** 손글씨는 굵기 대비가 약해서 13px 에서 500 과 600 의 차이는
    활성/비활성을 혼자 나르지 못한다 — 색·크기와 함께 건다.
  - 🚨 **어느 화면이든 받는 손글씨 서브셋은 `app/layout.tsx` 가 미리 받는다** (`PRELOADED_FONT_SUBSETS`). 늦게 붙으면
    줄바꿈이 바뀌어 화면이 밀린다(CLS). 파티션을 다시 뽑으면 이 번호도 다시 고른다 — 고르는 기준은 디자인 시스템 §4
  - 근거와 실측치는 [디자인 시스템 §4](../../docs/web/design-system-v1.md) 에 있다.
- 🚨 **`cursor` 는 `globals.css` 가 한 번에 건다.** Tailwind 4 preflight 는 버튼에 `cursor` 를 주지 않아서(v3 와 달라진 점) 전부 기본 화살표였다. 컴포넌트마다 붙이면 빠뜨린다 — 실제로 칩만 `not-allowed` 였다.
- **상호작용 상태는 primitive 안에 있다** (디자인 시스템 §8 표). 화면에서 `hover:`·`active:` 를 따로 붙이지 않는다.
  🚨 **`active:` 를 빠뜨리면 웹뷰에서 아무 반응이 없다** — 웹뷰에는 호버가 없어서 `hover:` 는 브라우저에서만 걸린다.
  🚨 `hover:` 는 `globals.css` 의 `@custom-variant` 로 **`@media (hover: hover)` 안에서만** 걸리게 덮어 뒀다.
  Tailwind 기본값은 그냥 `:hover` 라 터치 기기에서 탭한 뒤 **눌러붙는다** — 이 화면은 대부분 웹뷰라 그게 기본 경험이 된다.
- **기다리는 버튼에는 `Spinner` 를 붙인다.** 🚨 `prefers-reduced-motion` 에서는 스피너가 숨고 문구만 남는다 — 멈춘 스피너는 고장난 화면으로 읽힌다.
- **움직임은 바뀐 순간에만 쓴다** (디자인 시스템 §8 — 시트 · 펼침 · 교체 · 단계 넘김 · 자리 옮김 · 화면 전환).
  🚨 **화면이 처음 뜰 때 · 스크롤할 때는 움직이지 않는다.** 하루에 여러 번 지친 상태로 여는 화면이라, 처음엔 살아 있어
  보여도 100번째엔 매번 기다려야 하는 것이 된다. `lib/motion.ts` 의 훅과 `Swap` 은 전부 첫 렌더를 건너뛴다 — 직접
  `animate()` 를 부르지 말고 그것을 쓴다. 🚨 승인 게이트 안에는 색 변화 말고 넣지 않는다. 게이트를 담은 시트 자체는 다른 시트와 같이 열리고 닫힌다 (#490).
- 🚨 **도메인 4색을 한 화면에 다 쓰지 않는다** (최대 2개 · 디자인 시스템 §3). 예외는 **로그인 전 소개 화면 하나뿐**이다 — 거기엔 추천이 없어서 도메인 색이 "어느 Agent 결과인가" 신호로 쓰이지 않는다.
- **모바일 우선.** 이 화면은 대부분 [`apps/mobile`](../../apps/mobile) 웹뷰 안에서 보인다. 데스크톱 레이아웃을 먼저 잡지 않는다.
- 노치·상태바·홈 인디케이터는 `Screen` 이 상하 여백과 **함께 calc 로** 먹는다. 값의 출처는 둘이다 — 웹뷰 안에서는 네이티브 셸이 꽂아 주는 `--shell-inset-*`, 모바일 브라우저 직접 접속에서는 `env(safe-area-inset-*)`. 🚨 **셸이 그 자리를 대신 칠하게 두지 않는다** — 화면마다 아래 색이 달라서(네비 · 시트 · 그냥 화면) 셸이 한 색으로 칠하면 흰 띠가 남는다 (#133).
  `pt-safe` / `pb-safe` 유틸은 **safe area 만** 필요한 곳(하단 고정 바 등)에 남겨 뒀다 — 여백과 같이 주려면 위 §3 의 경고를 볼 것.
