---
name: 육아기억 AI
description: 밤에 지친 손으로 여는, 아이에 대한 기록이 주인공인 화면의 디자인 시스템
colors:
  canvas: "#FBF9F5"
  surface: "#FFFEFB"
  surface-muted: "#F3F1EC"
  ink: "#221F1B"
  ink-muted: "#605A52"
  ink-subtle: "#6F6A61"
  line: "#E8E4DC"
  line-strong: "#969086"
  brand: "#3A6B52"
  brand-hover: "#2F5943"
  brand-soft: "#E7EFE9"
  brand-ink: "#26493A"
  food: "#9A4F22"
  food-soft: "#F6EBE1"
  food-ink: "#7E3F1B"
  activity: "#0F7084"
  activity-soft: "#E5EFF1"
  activity-ink: "#055766"
  education: "#4E5AA0"
  education-soft: "#E9EAF5"
  education-ink: "#3B4680"
  health: "#8A4E74"
  health-soft: "#F3E9EF"
  health-ink: "#6E3B5B"
  caution: "#97620E"
  caution-soft: "#FAEFDB"
  caution-ink: "#7A4F09"
  danger: "#B74351"
  danger-hover: "#A02E40"
  danger-soft: "#F7E8E8"
  danger-ink: "#891E32"
  kakao: "#FEE500"
  kakao-hover: "#F0D800"
  kakao-ink: "rgb(0 0 0 / 0.85)"
  scrim: "rgb(34 31 27 / 0.45)"
typography:
  display:
    fontFamily: "Hakgyoansim Nalgae R, Pretendard Variable, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Hakgyoansim Nalgae R, Pretendard Variable, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.35
    letterSpacing: "-0.01em"
  section:
    fontFamily: "Hakgyoansim Nalgae R, Pretendard Variable, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Hakgyoansim Nalgae R, Pretendard Variable, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  body-sm:
    fontFamily: "Hakgyoansim Nalgae R, Pretendard Variable, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  button:
    fontFamily: "Hakgyoansim Nalgae R, Pretendard Variable, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "normal"
  label:
    fontFamily: "Hakgyoansim Nalgae R, Pretendard Variable, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "normal"
  caption:
    fontFamily: "Hakgyoansim Nalgae R, Pretendard Variable, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
  body-doc:
    fontFamily: "Pretendard Variable, Pretendard, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
rounded:
  field: "10px"
  card: "14px"
  sheet: "20px"
  full: "9999px"
spacing:
  2xs: "4px"
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "20px"
  xl: "24px"
  2xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.brand}"
    textColor: "#FFFFFF"
    typography: "{typography.button}"
    rounded: "{rounded.field}"
    padding: "0 20px"
    height: "48px"
  button-primary-hover:
    backgroundColor: "{colors.brand-hover}"
    textColor: "#FFFFFF"
  button-approve:
    backgroundColor: "{colors.brand}"
    textColor: "#FFFFFF"
    typography: "{typography.button}"
    rounded: "{rounded.field}"
    height: "52px"
    width: "100%"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.field}"
    padding: "0 20px"
    height: "48px"
  button-tertiary:
    backgroundColor: "transparent"
    textColor: "{colors.brand}"
    typography: "{typography.button}"
    rounded: "{rounded.field}"
    padding: "0 8px"
    height: "44px"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "#FFFFFF"
    typography: "{typography.button}"
    rounded: "{rounded.field}"
    padding: "0 20px"
    height: "48px"
  button-kakao:
    backgroundColor: "{colors.kakao}"
    textColor: "{colors.kakao-ink}"
    typography: "{typography.button}"
    rounded: "{rounded.field}"
    padding: "0 20px"
    height: "48px"
  button-disabled:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.ink-subtle}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.field}"
    padding: "0 14px"
    height: "52px"
  input-error:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
  chip-choice:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "0 10px"
    height: "28px"
  chip-choice-selected:
    backgroundColor: "{colors.brand-soft}"
    textColor: "{colors.brand-ink}"
  chip-domain:
    backgroundColor: "{colors.activity-soft}"
    textColor: "{colors.activity-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "0 10px"
    height: "28px"
  chip-evidence:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.caption}"
    rounded: "{rounded.full}"
    padding: "0 10px"
    height: "28px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "16px"
  card-personalized:
    backgroundColor: "{colors.brand-soft}"
    textColor: "{colors.brand-ink}"
    rounded: "{rounded.card}"
    padding: "16px"
  card-general:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "16px"
  card-failed:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.card}"
    padding: "16px"
  banner-caution:
    backgroundColor: "{colors.caution-soft}"
    textColor: "{colors.caution-ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.field}"
    padding: "12px 14px"
  banner-danger:
    backgroundColor: "{colors.danger-soft}"
    textColor: "{colors.danger-ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.field}"
    padding: "12px 14px"
  bottom-sheet:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sheet}"
    padding: "20px"
  checkbox-checked:
    backgroundColor: "{colors.brand}"
    textColor: "#FFFFFF"
    rounded: "{rounded.full}"
    size: "20px"
---

# Design System: 육아기억 AI

## Overview

**Creative North Star: "아이 관찰 노트"**

이 화면의 주인공은 UI 가 아니라 **적어 둔 것**이다. 부모가 하원길에 한 줄 남기면 그것이 관찰로 쌓이고, 나중에 추천이 나올 때 "사용한 기록 3건 · 가장 최근 오늘" 이라는 근거를 달고 나온다. 그래서 화면이 해야 할 일은 감탄을 만드는 것이 아니라 **기록과 근거를 앞에 세우고 비켜서는 것**이다. 관찰 노트의 미감은 단정(斷定)하지 않는 데 있다 — 한 번 본 것을 성향으로 확정하지 않고, 근거가 없으면 "또래 기준 일반 추천" 이라고 화면에 쓰고, 6개월 지난 기록은 점선으로 눌러 그린다. 그 태도가 색과 서체보다 먼저다.

본문 서체가 손글씨(`학교안심 날개 R`)인 것이 이 시스템의 가장 큰 선택이다. **부모가 수첩에 적어 둔 글씨**라는 뜻이고, 가독성을 조금 내주고 온기를 사는 교환이다. 다루는 것이 아이에 대한 기록이라 그 교환이 성립한다고 봤다. 다만 **약관·처리방침·동의 전문은 그 교환을 하지 않는다** — 거기서는 온기가 아니라 판별성이 필요해서 통째로 Pretendard(`font-doc`)로 바꾼다.

바탕은 웜 오프화이트(`#FBF9F5`)이고 순백·순검정이 없다. 실제 사용 장면이 새벽 수유와 재운 뒤 확인이라, 강조는 **채도가 아니라 명도**로 만든다. 그림자는 바텀시트 하나뿐이고 카드의 경계는 1px 선이 만든다. 한 화면에 도메인 색은 최대 2개, 예쁘라고 칠하는 색은 없다.

**Key Characteristics:**

- 기록·근거·건수가 화면의 주어. 장식이 아니라 본체다
- 웜 오프화이트 위 세이지 그린 하나 — 나머지는 전부 뜻이 정해진 색
- 손글씨 본문 + 법률 문서용 Pretendard, 한 화면에서 섞지 않음
- 평면이 기본. 그림자 1단계, 경계는 `line` 1px
- 전 구간 1열 · 최대 폭 560px · 데스크톱 레이아웃 없음
- 따뜻하고 단정하고 손으로 쓴 듯한 · 조용하고 단단한 컴포넌트

## Colors

뜻이 정해진 색만 있다. 뉴트럴이 화면의 90% 를 덮고, 나머지는 "이 색 = 이 뜻" 이 1:1 로 붙는다.

### Primary

- **세이지 그린** (`brand` · 5.9:1): 주 버튼 · 활성 탭 · 링크 · 포커스 링. 하루에 여러 번 보는 색이라 채도를 팔레트에서 가장 낮게(0.068) 잡았다.
- **세이지 그린 소프트** (`brand-soft`): **개인화 추천 카드의 배경**. 이 배경이 깔렸다는 것은 근거 `memory_id` 가 붙어 있다는 뜻이다.
- **세이지 그린 잉크** (`brand-ink` · 8.6:1): `brand-soft` 위의 글자.

성공 상태에 별도 색을 두지 않는다. `brand` 를 쓴다 — 초록을 두 뜻으로 쓰면 브랜드가 "성공" 으로 읽힌다.

### Secondary

도메인 4종. [`suggestion_agent`](CLAUDE.md) enum 과 1:1 이고 **다른 용도로 쓰지 않는다.**

- **테라코타** (`food` · 5.7:1): 식사
- **틸** (`activity` · 5.4:1): 놀이. 216° 는 sRGB 가 채도를 가장 못 내는 구간이라 갈 수 있는 끝(0.086)에 붙여 나머지 셋과 동급으로 맞췄다
- **인디고** (`education` · 6.1:1): 교육
- **플럼** (`health` · 5.8:1): 건강

각각 `-soft` 배경과 `-ink` 글자를 함께 가진다. **soft 배경 위 텍스트의 대비는 `canvas` 가 아니라 그 soft 색과 비교한다.**

### Tertiary

- **앰버** (`caution` · 4.9:1): 🚨 **승인 게이트 2곳 전용** — 캘린더 쓰기, 건강·알레르기 기록 확정. 도메인에 노랑 계열이 없어서 "노랑 = 내 확인이 필요함" 이 한 번에 학습된다.
- **크림슨** (`danger` · 5.1:1): 🚨 **알레르기 경고 · 건강 추천 중단 · 파괴적 확정 전용.** 테라코타와 적록색약에서 붙지 않도록 명도를 올려(L 54.6) 색조를 16° 까지 벌린 값이다.
- **카카오 옐로** (`kakao`): 🚨 **우리 팔레트가 아니다.** 카카오가 정한 색이고 00 로그인 화면의 그 버튼 밖에서는 쓰지 않는다.

### Neutral

- **웜 오프화이트** (`canvas`): 페이지 바탕. 순백 대비 야간 글레어를 줄인다
- **서피스** (`surface`): 카드·시트·입력. `canvas` 와 같은 색조(OKLCh H 85)로 밝기만 올렸다 — 순백이면 웜 바탕 위에서 혼자 차가워 보인다
- **뮤티드 서피스** (`surface-muted`): 비활성 · 접힌 영역 · **실패 카드** · 스켈레톤
- **잉크** (`ink` · 15.6:1) / **뮤티드 잉크** (`ink-muted` · 6.5:1) / **서틀 잉크** (`ink-subtle` · 5.1:1): 본문 / 보조·설명 / 메타·타임스탬프·건수
- **라인** (`line`) / **강한 라인** (`line-strong` · 3.0:1): 카드 경계·구분선 / 입력 테두리·점선 테두리
- **스크림** (`scrim`): 시트 뒤 가림막. 순검정 스크림은 저조도에서 눈을 때린다

### Named Rules

**The 뜻 하나 규칙.** 모든 색은 "이 색 = 이 뜻" 이 1:1 이다. 예쁘라고 칠하는 색은 없고, 뜻이 없는 자리에는 뉴트럴을 쓴다. 새 색이 필요하다고 느끼면 대개 새 뜻을 만들고 있는 것이다.

**The 라벨이 본체 규칙.** 색은 단독 신호가 될 수 없다. 도메인 칩은 아이콘 + 텍스트 라벨과 함께 나가고, 아이콘은 `aria-hidden` 이다. 검증은 **도메인 칩에서 라벨을 지워 보는 것** — 그래도 구분되면 통과가 아니라 위반이다.

**The 실패는 빨강이 아니다 규칙.** Agent 2개 중 1개만 성공해도 화면은 나가야 한다. 그걸 빨강으로 칠하면 부모가 매일 빨간 화면을 본다. 실패 카드는 `surface-muted` + `ink-muted` 이고, 빨강은 알레르기·파괴적 확정에만 남는다.

**The 흐린 회색 글씨를 만들지 않는다 규칙.** `ink-subtle` 도 5.1:1 을 넘긴다. 기록 건수·날짜·타임스탬프는 장식이 아니라 부모가 실제로 읽는 정보다.

## Typography

**Body Font:** 학교안심 날개 R (폴백 Pretendard Variable → system-ui) · 자체 호스팅 92개 동적 서브셋 · SIL OFL 1.1
**Document Font:** Pretendard Variable (`font-doc`) — 이용약관 · 개인정보 처리방침 · 동의 전문 전용
**Display Font:** 별도로 두지 않는다. 손글씨가 그 자리를 겸한다

**Character:** 부모가 수첩에 적어 둔 글씨. 아이에 대한 기록을 다루는 화면이라 온기를 위해 가독성을 조금 내준 교환이고, `size-adjust: 106%` · `ascent-override: 82.7%` · `descent-override: 26.5%` 세 보정을 걸어 아래 8단계가 의도한 크기로 읽히게 맞췄다(빌드 스크립트가 두 폰트를 실측해 산출).

### Hierarchy

- **Display** (700, 24px, 1.3, -0.02em): 화면 제목. 좁은 폰(< 380px)에서는 `title` 로 낮아진다
- **Title** (700, 20px, 1.35, -0.01em): 카드 제목 · 시트 헤더
- **Section** (600, 17px, 1.4, -0.01em): 섹션 구분
- **Body** (400, 16px, 1.6): 본문 기본값 · 입력값. 한 줄은 35자 안팎 (컨테이너 560px 이 그 역할을 한다)
- **Body-sm** (400, 14px, 1.55): 보조 설명
- **Button** (600, 15px, 1.2): 버튼 라벨
- **Label** (500, 13px, 1.4): 칩 · 탭 · 폼 라벨
- **Caption** (400, 12px, 1.4): 메타 · 타임스탬프 · 건수. **이보다 작게 쓰지 않는다**

⚠️ 본문 서체는 **Regular 400 단일 웨이트**다. 600·700 은 브라우저 합성이고, 브라우저는 600 이상만 합성하므로 **`label`(500)은 `body`(400)와 똑같이 렌더된다.** 위계를 굵기에만 걸지 말고 크기·색과 함께 걸 것. `font-doc` 화면에서는 4단계가 그대로 나온다.

### Named Rules

**The 한 화면 한 서체 규칙.** 문서 화면은 통째로 `font-doc` 이다. 제목만 손글씨로 남기면 `size-adjust` 때문에 같은 문단 안에서 글자 크기감이 어긋난다.

**The 12px 하한 규칙.** 화면에 12px 미만 글자를 만들지 않는다. 입력 필드는 16px 미만으로 내리지 않는다 — iOS 웹뷰가 포커스 시 화면을 확대하고 그대로 남는다.

**The 기호를 글자로 찍지 않는다 규칙.** `①②③` · `↓` · `★` 같은 문자로 뜻을 나타내지 않는다. 스크린리더가 제각각 읽고, 글리프가 없으면 두부(□)가 된다. 순서는 `<ol>`, 마커는 CSS `marker:`, 그림은 lucide 아이콘 + 텍스트 라벨.

## Layout

간격은 **4px 배수 7단계**만 쓴다: `2xs` 4(아이콘-라벨) · `xs` 8(칩 사이) · `sm` 12(**카드 사이**) · `md` 16(**카드 안쪽 · 화면 좌우**) · `lg` 20(시트 안쪽) · `xl` 24(섹션 사이) · `2xl` 32(화면 상단 · 빈 상태).

콘텐츠 최대 폭은 **560px**, 그 이상에서는 가운데 정렬하고 양옆은 `canvas` 로 둔다. 좌우 여백은 16px, 폭 380px 미만 기기에서만 12px. 반응형은 세 구간뿐이고 **전 구간 1열**이다 — 폭이 남는다고 2열로 벌리지 않는다.

상하 여백 32px 과 노치·홈 인디케이터는 `Screen` 컴포넌트가 `calc` 로 **함께** 소유한다. safe-area 유틸이 여백을 같이 받는 이유가 이것이다 (`pt-safe-8` = safe area + 32px) — 같은 padding 속성이라 `py-*` 를 나란히 쓰면 한쪽이 조용히 죽는다.

터치 타깃은 승인 게이트 버튼 48×48 이상(전체 폭 · 높이 52), 일반 버튼·탭·칩 44×44, 인접 타깃 간격 8 이상. 칩 자체는 28px 이지만 위아래 8px 투명 여백으로 44px 을 채운다 — 그래서 **칩 줄에는 가로 gap 만 주고 세로 gap 은 주지 않는다.**

### Named Rules

**The 1열 고정 규칙.** 제안 카드도, 관찰 목록도, 도메인 칩 줄도 세로로 쌓는다. 2열이 필요해 보이면 정보가 많은 것이지 열이 부족한 게 아니다. 데스크톱 레이아웃을 만들면 화면이 두 벌이 되고 안전 규칙을 두 곳에서 지켜야 한다.

**The 위는 넉넉히 목록은 촘촘히 규칙.** 화면 상단(인사말·상태)은 32px, 카드 목록은 12px. 스크롤 한 번에 더 많은 카드가 들어오는 편이 이 서비스에 맞다 — 부모는 감상하러 온 게 아니라 확인하러 왔다.

## Elevation & Depth

**평면이 기본이다.** 카드·배너·입력·목록(화면의 95%)에는 그림자가 없고, 경계는 `line` 1px 이 만든다. 무언가를 띄우고 싶으면 그림자 대신 배경색(`surface` vs `canvas`)이나 테두리로 구분한다. 다크모드는 v1 범위 밖이라 `color-scheme: light` 가 의도적으로 고정돼 있다.

### Shadow Vocabulary

- **시트** (`box-shadow: 0 -2px 16px rgb(34 31 27 / 0.08)`): 바텀시트 상단에만. 시스템 전체에서 유일한 그림자다
- **가림막** (`background: rgb(34 31 27 / 0.45)`): 시트 뒤. 순검정이 아니라 `ink` 를 깐 것이다

### Named Rules

**The 단계를 늘리지 않는다 규칙.** 미세한 그림자가 여러 겹 깔린 화면은 저조도에서 뿌옇게 보인다. 높낮이는 평면 / 시트 하나 / 가림막, 이 셋에서 끝난다.

## Shapes

radius 는 네 개뿐이다 — `field` 10px(입력·버튼·배너) · `card` 14px(카드) · `sheet` 20px(바텀시트 상단) · `full` 9999px(칩·탭·아바타·체크박스 표식). **직각(0px)은 화면 전체 바탕과 구분선에만 남는다.**

사이 값을 만들지 않는다. 체크박스 표식이 `rounded-full` 인 것도 그래서다 — 20px 사각형에 `field`(10px)는 과하고, 그 사이 값을 만들면 radius 가 다섯 개가 된다.

## Components

**UI 라이브러리를 쓰지 않는다.** 토큰과 1:1 로 붙고 고치기 쉬운 쪽을 골랐다. 예외는 둘뿐이다 — 바텀시트는 네이티브 `<dialog>` 위에(포커스 트랩·ESC·바깥 `inert`·스크림을 브라우저가 준다), 달력은 `react-day-picker` 위에(그리드 ARIA·방향키·월 경계·로케일). 둘 다 자기 디자인을 들고 오지 않는다.

**아이콘은 lucide,** 크기 3단계(16·20·24) · `strokeWidth: 1.75` 고정. 도메인 4종은 전부 **집에 있는 물건**이다 — 수저 · 블록 · 책 · 체온계. `health` 에 심전도를 쓰지 않는다: 그 기호는 병원과 진단을 뜻하는데 이 제품은 진단하지 않는다.

성격은 한 마디로 **조용하고 단단하다.** 상태 변화는 색으로만 일어나고 크기·위치는 움직이지 않는다.

### Buttons

- **Shape:** 완만한 모서리(`field` 10px). 높이 48, 좌우 20
- **Primary:** `brand` 배경 + 흰 글자. 🚨 **한 화면에 하나.** 두 개면 무엇이 다음 행동인지 부모가 판단해야 한다
- **Approve:** `brand` 배경 · **높이 52 · 전체 폭.** 🚨 승인 게이트 2곳 전용이다 — 되돌릴 수 없는 행동을 잘못 눌러 실행하는 경로를 만들지 않는다
- **Secondary:** `surface` 배경 + `line-strong` 1px + `ink` 글자. 승인 게이트의 "아니에요" 가 여기다 — 거절은 파괴가 아니다
- **Tertiary:** 배경 없음 + `brand` 글자 · 높이 44 · 좌우 8
- **Danger:** `danger` 배경 + 흰 글자. 파괴적 확정에만(동의 철회 · 기록 삭제)
- **Kakao:** `kakao` 배경 + `kakao-ink` 글자. 00 로그인 화면 전용
- **Hover / Active:** 색만 바뀐다(120ms · `cubic-bezier(0.2, 0, 0, 1)`). `hover:` 는 `@media (hover: hover)` 안에서만 걸고, 누른 느낌은 `active:` 가 맡는다 — 터치 기기에서 호버가 눌러붙기 때문이다
- **Disabled:** `surface-muted` + `ink-subtle` + `line` 1px. 🚨 **브랜드색을 흐리게 만들지 않는다** — "누를 수 있어 보이는데 안 눌리는" 상태가 지친 사용자에게 제일 나쁘다

### Chips

- **Style:** 높이 28 · 좌우 10 · `full` · label. 넘치면 줄바꿈하고 **가로 스크롤하지 않는다**
- **chip-choice:** 선택 전 `surface` + `line` 1px + `ink-muted` / 선택 후 `brand-soft` + `brand` 1px + `brand-ink`. `aria-pressed` 로 상태를 낸다 — 색만으로 "골랐음" 을 전달하지 않는다
- **chip-domain:** `{domain}-soft` 배경 + `{domain}-ink` 글자 + 아이콘 16 + 라벨
- **chip-evidence:** `surface` + `line` 1px + `ink-muted` + caption. `is_stale`(6개월 초과) 근거는 **점선 + `ink-subtle`** 로 눌러 그린다
- **chip-count:** 배경 없이 `ink-subtle` caption ("기록 12건")
- 근거 칩이 4개를 넘으면 **"+N" 으로 접는다** — 숨기는 게 아니라 카드에서 목록으로 옮기는 것이다

### Cards / Containers

- **Corner Style:** `card` 14px · 안쪽 여백 16 · 카드 사이 12
- **card:** `surface` + `line` 1px. 그림자 없음
- **card-personalized:** `brand-soft` 배경 · 테두리 없음 · **근거 칩 행 필수**
- **card-general:** `canvas` 배경 · `line-strong` **1px 점선** · "또래 기준 일반 추천" 라벨 + 기록 건수 **필수**
- **card-failed:** `surface-muted` · `ink-muted` · 재시도는 tertiary 버튼
- 🚨 **card-personalized 와 card-general 을 같은 컴포넌트로 만들지 않는다.** 한 컴포넌트에 플래그를 넣으면 언젠가 근거 0건인데 개인화로 그려진다

### Inputs / Fields

- **Style:** `surface` 배경 · `line-strong` 1px · `field` 10px · 높이 52 · 좌우 14 · **body(16px)** · placeholder `ink-subtle`
- **Focus:** `brand` 2px outline · offset 2. 테두리는 그대로. 🚨 `outline: none` 을 쓰지 않는다
- **Error:** `danger` 1px 테두리 + 아래 caption / `danger-ink` 로 사유 한 줄 + `aria-invalid`
- **Caret:** `brand`. 선택 영역은 `brand-soft` + `brand-ink` — 브라우저 기본값을 그대로 내보내지 않는다
- **Date field:** 값을 보여주는 트리거(입력과 같은 사양) + 달력을 담은 **바텀시트**. 오늘 이후 비활성 · 요일과 월 이름은 한국어
- **Checkbox:** 네이티브 `<input>` 을 `sr-only` 로 숨기고 표식만 그린다. 표식 20×20 · `full` · 선택 전 `line-strong` 1px / 선택 후 `brand` 채움 + 흰 체크 16 · 행 전체가 44px 터치 타깃

### Bottom Sheet

상단 `sheet` 20px · `surface` · 유일한 그림자 · 뒤에 `scrim`. 최대 높이 `88dvh`, 넘치면 시트 안에서만 스크롤. 드래그 핸들 36×4 · `line-strong` · `full`. 하단 버튼 영역은 `pb-safe-4`(safe area + 16). 🚨 **승인 시트는 스크림 탭·ESC 로 닫히지 않는다** — 실수로 닫혀 draft 가 만료되는 경로를 만들지 않는다. 되돌릴 수 있는 시트(약관 상세 등)에는 그 모드를 쓰지 않는다.

### Navigation

탭은 07 화면의 2계층에만 있다. 활성은 `ink` + label 600 + 아래 `brand` 2px, 비활성은 `ink-muted` + label 400 + 밑줄 없음. 높이 44. 탭 전환은 URL 에 남긴다 — 다른 엔드포인트라 뒤로가기가 동작해야 한다.

### Waiting

- **Spinner:** 16×16 · 2px 테두리 · **트랙 = 현재 글자색 25% + 머리 = 100%** · 1초에 1바퀴. 🚨 `prefers-reduced-motion` 에서는 **숨긴다** — 멈춘 스피너는 "고장난 화면" 으로 읽히고, 옆의 문구가 상태를 대신 말한다
- **진행 오버레이(SSE):** 단계를 세로로 쌓고 현재 단계만 살린다. 완료 `ink-muted` + 체크 `brand` / 진행 중 `ink` + label 600 / 대기 `ink-subtle`. `aria-live="polite"`

> **아직 구현되지 않은 것** — 배너 · 탭 · 진행 오버레이 · 빈 상태 · 스켈레톤은 위 사양이 확정돼 있지만 컴포넌트 파일은 아직 없다. 새로 만들 때 이 값에서 벗어나지 말 것. 구현된 것은 `Screen` · `PageTitle` · `Button` · `TextInput` · `DateField` · `Checkbox` · `Chip`/`ChipRow` · `Card`/`CardFailed` · `Spinner` · `BottomSheet` · `DomainIcon` 이다.

## Do's and Don'ts

### Do:

- **Do** 모든 색을 뜻과 1:1 로 쓴다. 도메인 색은 그 도메인에만, `caution` 은 승인 게이트 2곳에만, `danger` 는 알레르기·건강 중단·파괴적 확정에만.
- **Do** 색을 바꿀 때 **그 색이 얹히는 배경과의 쌍을 다시 계산한다.** soft 배경 위 텍스트는 `canvas` 가 아니라 그 soft 색과 비교한다 — 여기서 많이 틀린다. `/design-system` 이 그 자리에서 계산해 준다.
- **Do** 기록 건수 · 날짜 · 근거를 화면에 그대로 보여준다. 근거가 없으면 "또래 기준 일반 추천" 이라고 쓴다.
- **Do** 상태 변화를 **색으로만** 만든다(120ms). 크기와 위치는 움직이지 않는다.
- **Do** 브라우저가 대신 칠하는 표면(선택 영역 · 캐럿 · 탭 하이라이트 · 스크롤바)도 팔레트 안에 둔다.
- **Do** 화면 제목에 `PageTitle` 을 쓴다. 좁은 폰에서 `display` → `title` 로 낮추는 규칙이 거기 한 곳에 있다.

### Don't:

- **Don't** 병원·의료 앱처럼 보이게 만들지 않는다. 흰 바탕 + 파란 강조 + 차트·수치 중심은 이 제품이 **진단하지 않는다**는 규칙과 정면으로 어긋난다. 건강 도메인에도 심전도·청진기 기호를 쓰지 않는다.
- **Don't** 원색 캐릭터 키즈 앱으로 가지 않는다. 채도 높은 원색 · 둥근 말풍선 · 마스코트를 넣지 않는다 — 이 화면을 여는 사람은 아이가 아니라 지친 보호자다.
- **Don't** SaaS 대시보드로 만들지 않는다. KPI 타일 · 지표 카드 그리드 · 스파크라인을 만들지 않는다. 부모가 자기 아이를 지표로 보게 하지 않는다.
- **Don't** 실패·부분 성공을 빨강으로 칠하지 않는다. `surface-muted` + `ink-muted` 다.
- **Don't** 그림자 단계를 늘리거나 카드에 그림자를 넣지 않는다. 경계는 `line` 1px 이다.
- **Don't** 2열 그리드나 데스크톱 전용 레이아웃을 만들지 않는다. 560px 고정 · 1열이다.
- **Don't** 12px 미만 글자를 쓰지 않는다. 입력은 16px 미만으로 내리지 않는다.
- **Don't** 화면 텍스트에 이모지·기호(`①` · `↓` · `★`)와 줄표(`—`)를 쓰지 않는다. 뜻은 글자로 쓰고, 그림은 아이콘 + 라벨로 낸다.
- **Don't** 승인 게이트를 늘리지도 줄이지도 않는다. `btn-approve` 와 `caution` 은 그 2곳 전용이고, 법적 동의 체크박스는 거기 해당하지 않는다.
- **Don't** 비활성 상태를 브랜드색을 흐리게 만들어 표현하지 않는다.
- **Don't** 컴포넌트 밖에서 `#hex` 를 직접 쓰지 않는다. 토큰 클래스만 쓴다.
