/**
 * 화면 전환에서 **이어 붙는 이름 쌍** (디자인 시스템 §8 "허용하는 화면 전환").
 *
 * 같은 이름이 떠나는 화면과 들어오는 화면에 하나씩 있으면 브라우저가 위치·크기를 이어 준다.
 * 이름이 없는 나머지는 루트 크로스페이드(`base` 200ms)다 — `globals.css` 의 `::view-transition-*`.
 *
 * 🚨 **이름 쌍은 "같은 물건이 자리를 옮겼다(또는 그대로 있다)" 를 말하는 신호다.** 늘리면 그 뜻이 흐려진다.
 *    화면 전체의 움직임은 이름이 아니라 아래 `TRANSITION_TYPE` 이 정한다.
 * 🚨 **`default="none"` 을 줄 때는 `share` 를 꼭 같이 준다.** 빠지면 쌍이 조용히 안 이어진다
 *    (Next 문서 `view-transitions.md`). 한 화면에 같은 이름이 **둘** 서면 전환이 통째로 건너뛴다.
 * 🚨 **화면 이동(transition)에서만 켜진다.** `router.push` · `<Link>` 는 되고 `setState` 로는 안 켜진다 —
 *    그래서 대화 화면 안에서 보낸 말풍선에는 등장 애니메이션이 없다 (§8 "하지 않는 것").
 */
export const VIEW_TRANSITION = {
  /** 채팅바. 홈에서는 네비 위, 대화 화면에서는 맨 아래에 있다 — 그 사이를 잇는다. */
  chatBar: "chat-bar",
  /** 홈 입력창에서 막 보낸 한 줄 → 대화 화면의 그 말풍선. */
  sentLine: "sent-line",
  /**
   * 하단 네비. 두 화면이 다 네비를 달고 있으면(03 → 10 · 10 → 10-1 · 11 → 11-1) **움직이지 않고** 켜진
   * 칸만 바꿔 낀다 — 화면과 함께 미끄러지면 바닥이 통째로 들썩인다. 한쪽에만 있으면 그 화면과 같이 움직인다.
   */
  childNav: "child-nav",
} as const;

/**
 * 화면 이동의 **방향**. 이동을 일으키는 쪽(`router.push` · `<Link>`)이 붙이고, 두 화면의
 * `ScreenTransition` 이 이 값을 보고 움직임을 고른다. 값이 없는 이동(기기 뒤로가기 · 하단 네비)은
 * 이름 쌍만 이어 붙고 화면은 움직이지 않는다 — 방향을 모르는 이동에 방향을 지어내지 않는다.
 *
 * 🚨 **붙이는 자리는 §8 "허용하는 화면 전환" 표에 있는 쌍뿐이다.** 부모 화면에서 자식 화면으로
 *    들어가면 `forward`, 자식에서 **그 부모로** 돌아가면 `back`. 어디서 왔는지에 따라 부모가 달라지는
 *    이동(08 사진을 저장한 뒤 홈으로 가는 것처럼 09 에서 들어왔을 수도 있는 것)에는 붙이지 않는다.
 */
export const TRANSITION_TYPE = {
  /** 한 칸 앞으로. 들어오는 화면이 오른쪽에서 오고, 떠나는 화면이 왼쪽으로 조금 비켜난다. */
  forward: "forward",
  /** 부모로 돌아가기. 떠나는 화면이 오른쪽으로 빠지고, 부모가 왼쪽에서 제자리로 돌아온다. */
  back: "back",
} as const;

/** `router.push(href, FORWARD)` · `<Link {...FORWARD}>` 처럼 펼쳐 쓴다. */
export const FORWARD = { transitionTypes: [TRANSITION_TYPE.forward] };
/** `router.push(href, BACK)` · `<Link {...BACK}>` 처럼 펼쳐 쓴다. */
export const BACK = { transitionTypes: [TRANSITION_TYPE.back] };
